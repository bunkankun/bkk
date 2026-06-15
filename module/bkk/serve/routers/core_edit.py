"""Direct-to-master editing of bkk-core records.

Editors (gated by GitHub team membership; ``is_editor`` on the session) push
edits straight to ``upstream@core_pr_base`` via their OAuth token. There is no
fork, no feature branch, and no PR step — saves become single commits on
master.

Endpoints:

* ``PATCH /core/{collection}/{uuid}`` — commit the proposed YAML (plus any
  ``extra_files``, e.g. a new sense when adding one to a word) to master.
* ``DELETE /core/{collection}/{uuid}`` — commit a delete of the record to
  master.

After GitHub accepts the commit the server applies the same change locally so
reads stay coherent:

1. The YAML is written to ``core_root`` atomically (temp-file + ``os.replace``).
2. The affected uuid's rows in ``_core.bkki`` are upserted / deleted in place
   so search, pickers, and lookup-by-uuid reflect the new state immediately.
3. A background ``run_core_sync`` job fast-forwards the local clone from the
   remote and rebuilds the index from scratch to catch wikilink resolution
   and any cross-record denormalisation.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import tempfile
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field

from bkk.index.core import (
    delete_core_record as index_delete_core_record,
    upsert_core_record as index_upsert_core_record,
)
from bkk.serialize.yaml_io import dumps_record, loads_record

from ..state import AppState, UserSession
from .admin import run_core_sync
from .auth import SESSION_COOKIE, _github_json, _github_status
from .core import COLLECTION_TYPES, _open, _require_collection

log = logging.getLogger("bkk.serve.core_edit")

router = APIRouter(prefix="/core", tags=["core"])

# Record keys that callers are never allowed to change. ``uuid`` + ``type``
# pin the file to its index row; renaming either breaks every reverse
# lookup.
LOCKED_RECORD_KEYS = frozenset({"uuid", "type"})


class ExtraFile(BaseModel):
    path: str
    # ``None`` for an extra-file delete; any dict for an upsert.
    data: dict[str, Any] | None = None


class EditRequest(BaseModel):
    data: dict[str, Any] = Field(default_factory=dict)
    message: str | None = None
    extra_files: list[ExtraFile] = Field(default_factory=list)


class ExtraFileResult(BaseModel):
    path: str
    commit_sha: str
    deleted: bool


class EditResponse(BaseModel):
    commit_sha: str
    commit_url: str
    data: dict[str, Any]
    extras: list[ExtraFileResult] = Field(default_factory=list)


class DeleteRequest(BaseModel):
    message: str | None = None


class DeleteResponse(BaseModel):
    commit_sha: str
    commit_url: str


# ---------- shared helpers --------------------------------------------------


def _editor_session(request: Request) -> UserSession:
    session_id = request.cookies.get(SESSION_COOKIE)
    user_session = request.app.state.bkk.sessions.get(session_id)
    if user_session is None:
        raise HTTPException(status_code=401, detail="Login required")
    if not user_session.is_editor:
        raise HTTPException(status_code=403, detail="Editor role required")
    return user_session


def _require_upstream(state: AppState) -> str:
    repo = state.config.core_upstream_repo
    if not repo or "/" not in repo:
        raise HTTPException(
            status_code=503,
            detail=(
                "core editing is not configured. Set core.upstream_repo in "
                ".bkkrc (e.g. 'bunkankun/bkk-core') or pass "
                "--core-upstream-repo when starting the server."
            ),
        )
    return repo


def _lookup_record(state: AppState, collection: str, uuid: str) -> tuple[str, str, str]:
    """Return ``(type, path, display_label)`` for ``collection/uuid`` from the index."""
    conn = _open(state)
    try:
        row = conn.execute(
            "SELECT type, path, display_label FROM notes "
            "WHERE uuid = ? AND collection = ?",
            (uuid, collection),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"{collection}/{uuid} not found in core index",
        )
    return row[0], row[1], row[2]


def _content_path(path: str) -> str:
    return quote(path, safe="/")


def _fetch_file(token: str, repo: str, path: str, ref: str) -> dict[str, Any] | None:
    """GET a file from the Contents API; None on 404."""
    try:
        payload = _github_json(
            "GET",
            f"/repos/{repo}/contents/{_content_path(path)}?ref={quote(ref, safe='')}",
            token,
        )
    except HTTPException as exc:
        if _github_status(exc) == 404:
            return None
        raise
    if isinstance(payload, list) or payload.get("type") != "file":
        raise HTTPException(
            status_code=400,
            detail=f"core path {path!r} on {repo}@{ref} is not a file",
        )
    return payload


def _decode_file(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if not isinstance(content, str):
        raise HTTPException(status_code=502, detail="GitHub file payload has no content")
    try:
        return base64.b64decode(content, validate=False).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=502, detail="core record content is not valid UTF-8",
        ) from exc


def _validate_record(
    proposed: dict[str, Any], original: dict[str, Any], type_name: str | None
) -> dict[str, Any]:
    """Reject changes to locked keys (``uuid``, ``type``)."""
    for key in LOCKED_RECORD_KEYS:
        if key in proposed and proposed[key] != original.get(key):
            raise HTTPException(
                status_code=400,
                detail=f"record key {key!r} is read-only",
            )
        if key not in proposed and key in original:
            # Auto-fill from original — UI may strip these for display.
            proposed = {**proposed, key: original[key]}
    if type_name and proposed.get("type") not in (None, type_name):
        raise HTTPException(
            status_code=400,
            detail=f"record type must be {type_name!r}",
        )
    return proposed


def _validate_extra_path(path: str) -> None:
    """Extra file paths must live under a known collection directory.

    Layout is ``<collection>/<hex>/<uuid>.yml`` — same shape as importer
    output. Reject ``..``, leading ``/``, or any unknown top-level dir.
    """
    parts = path.split("/")
    if len(parts) != 3 or any(p in ("", ".", "..") for p in parts):
        raise HTTPException(
            status_code=400,
            detail=f"extra_files path {path!r} must be '<collection>/<hex>/<uuid>.yml'",
        )
    collection, hex_dir, fname = parts
    if collection not in COLLECTION_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"extra_files path {path!r}: unknown collection {collection!r}",
        )
    if len(hex_dir) != 1 or hex_dir not in "0123456789abcdef":
        raise HTTPException(
            status_code=400,
            detail=f"extra_files path {path!r}: shard segment must be a single hex char",
        )
    if not fname.endswith(".yml"):
        raise HTTPException(
            status_code=400,
            detail=f"extra_files path {path!r}: filename must end with .yml",
        )


def _put_file(
    *,
    token: str,
    repo: str,
    rel_path: str,
    branch: str,
    text: str,
    message: str,
    parent_sha: str | None,
) -> tuple[str, str, str]:
    """PUT one file, returning ``(blob_sha, commit_sha, commit_url)``."""
    body: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if parent_sha is not None:
        body["sha"] = parent_sha
    try:
        result = _github_json(
            "PUT",
            f"/repos/{repo}/contents/{_content_path(rel_path)}",
            token,
            json=body,
        )
    except HTTPException as exc:
        if _github_status(exc) in (409, 422):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"core file {rel_path} on {repo}@{branch} changed since "
                    "you loaded it; reload and re-apply your edits"
                ),
            ) from exc
        raise

    return _extract_commit(result, rel_path)


def _delete_file(
    *,
    token: str,
    repo: str,
    rel_path: str,
    branch: str,
    message: str,
    parent_sha: str,
) -> tuple[str, str]:
    """DELETE one file, returning ``(commit_sha, commit_url)``."""
    try:
        result = _github_json(
            "DELETE",
            f"/repos/{repo}/contents/{_content_path(rel_path)}",
            token,
            json={"message": message, "branch": branch, "sha": parent_sha},
        )
    except HTTPException as exc:
        if _github_status(exc) in (409, 422):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"core file {rel_path} on {repo}@{branch} changed since "
                    "you loaded it; reload and re-apply your edits"
                ),
            ) from exc
        raise
    _blob_sha, commit_sha, commit_url = _extract_commit(result, rel_path)
    return commit_sha, commit_url


def _extract_commit(result: Any, rel_path: str) -> tuple[str, str, str]:
    """Pull ``(blob_sha, commit_sha, commit_url)`` from a Contents API response.

    ``blob_sha`` is ``""`` on a delete response (no new blob).
    """
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="GitHub returned unexpected payload")
    content_result = result.get("content")
    blob_sha = ""
    if isinstance(content_result, dict):
        sha = content_result.get("sha")
        if isinstance(sha, str):
            blob_sha = sha
    commit_obj = result.get("commit")
    commit_sha = commit_obj.get("sha") if isinstance(commit_obj, dict) else None
    commit_url = commit_obj.get("html_url") if isinstance(commit_obj, dict) else None
    if not isinstance(commit_sha, str):
        raise HTTPException(
            status_code=502,
            detail=f"GitHub commit for {rel_path} missing sha",
        )
    if not isinstance(commit_url, str):
        commit_url = ""
    return blob_sha, commit_sha, commit_url


def _atomic_write(path: "os.PathLike[str]", text: str) -> None:
    """Write ``text`` to ``path``, leaving any prior contents intact on failure."""
    target = os.fspath(path)
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".tmp-", suffix=".yml", dir=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _local_apply_upsert(state: AppState, rel_path: str, text: str) -> None:
    """Mirror a successful upstream commit into the local clone + index."""
    if state.core_root is None or state.core_index_path is None:
        return
    abs_path = state.core_root / rel_path
    try:
        _atomic_write(abs_path, text)
    except OSError as exc:
        log.warning("local write-through failed for %s: %s", rel_path, exc)
        return
    try:
        index_upsert_core_record(state.core_index_path, state.core_root, rel_path)
    except Exception as exc:  # noqa: BLE001 — best-effort; full sync recovers
        log.warning("local index upsert failed for %s: %s", rel_path, exc)


def _local_apply_delete(
    state: AppState, rel_path: str, uuid: str, type_name: str,
) -> None:
    if state.core_root is None or state.core_index_path is None:
        return
    abs_path = state.core_root / rel_path
    try:
        abs_path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("local delete failed for %s: %s", rel_path, exc)
    try:
        index_delete_core_record(state.core_index_path, uuid, type_name)
    except Exception as exc:  # noqa: BLE001
        log.warning("local index delete failed for %s: %s", rel_path, exc)


def _schedule_background_sync(
    state: AppState, background: BackgroundTasks, target: str,
) -> None:
    """Fire off a fast-forward + full reindex behind the commit."""
    if state.core_root is None or state.core_index_path is None:
        return
    job = state.jobs.create(kind="core_sync", target=target)
    background.add_task(
        run_core_sync,
        state.jobs,
        job.id,
        state.core_root,
        state.core_index_path,
        state.config.core_pr_base,
    )


# ---------- endpoints -------------------------------------------------------


@router.patch(
    "/{collection}/{uuid}",
    response_model=EditResponse,
    summary="Commit one core record edit directly to upstream master",
)
async def edit_record(
    request: Request,
    background: BackgroundTasks,
    collection: str,
    uuid: str,
) -> EditResponse:
    user_session = _editor_session(request)
    state: AppState = request.app.state.bkk
    upstream = _require_upstream(state)
    _require_collection(collection)

    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Expected JSON request body") from exc
    req = EditRequest.model_validate(payload)

    type_name, rel_path, display_label = _lookup_record(state, collection, uuid)

    token = user_session.access_token
    upstream_branch = state.config.core_pr_base
    base_payload = _fetch_file(token, upstream, rel_path, upstream_branch)
    if base_payload is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"{rel_path} not found on {upstream}@{upstream_branch}; "
                "the local index may be ahead of upstream"
            ),
        )
    base_text = _decode_file(base_payload)
    base_record = loads_record(base_text)
    if base_record.get("uuid") not in (None, uuid):
        raise HTTPException(
            status_code=500,
            detail=f"upstream {rel_path} record uuid does not match index",
        )
    if type_name and base_record.get("type") not in (None, type_name):
        raise HTTPException(
            status_code=500,
            detail=f"upstream {rel_path} record type does not match index",
        )
    parent_sha = base_payload.get("sha")
    if not isinstance(parent_sha, str):
        raise HTTPException(
            status_code=502,
            detail="GitHub file payload has no sha for parent",
        )

    merged = _validate_record(req.data, base_record, type_name)
    new_text = dumps_record(merged)

    for extra in req.extra_files:
        _validate_extra_path(extra.path)

    commit_message = req.message or f"Edit {collection}/{display_label or uuid}"
    _blob_sha, commit_sha, commit_url = _put_file(
        token=token, repo=upstream, rel_path=rel_path, branch=upstream_branch,
        text=new_text, message=commit_message, parent_sha=parent_sha,
    )
    _local_apply_upsert(state, rel_path, new_text)

    extras_out: list[ExtraFileResult] = []
    for extra in req.extra_files:
        extra_collection = extra.path.split("/", 1)[0]
        extra_type = COLLECTION_TYPES[extra_collection]
        extra_message = f"{commit_message} ({extra.path})"
        existing = _fetch_file(token, upstream, extra.path, upstream_branch)
        existing_sha = existing.get("sha") if isinstance(existing, dict) else None
        if not isinstance(existing_sha, str):
            existing_sha = None

        if extra.data is None:
            if existing_sha is None:
                # Nothing on master to delete — treat as a no-op rather than an
                # error so the client doesn't have to track which extras
                # already vanished.
                continue
            extra_commit, _extra_url = _delete_file(
                token=token, repo=upstream, rel_path=extra.path,
                branch=upstream_branch, message=extra_message,
                parent_sha=existing_sha,
            )
            extras_out.append(ExtraFileResult(
                path=extra.path, commit_sha=extra_commit, deleted=True,
            ))
            extra_uuid = (extra.path.rsplit("/", 1)[-1]).removesuffix(".yml")
            _local_apply_delete(state, extra.path, extra_uuid, extra_type)
            continue

        proposed_type = extra.data.get("type")
        if proposed_type not in (None, extra_type):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"extra_files {extra.path}: type {proposed_type!r} does not "
                    f"match collection {extra_collection!r} (expected {extra_type!r})"
                ),
            )
        extra_text = dumps_record(extra.data)
        _extra_blob, extra_commit, _extra_url = _put_file(
            token=token, repo=upstream, rel_path=extra.path,
            branch=upstream_branch, text=extra_text,
            message=extra_message, parent_sha=existing_sha,
        )
        extras_out.append(ExtraFileResult(
            path=extra.path, commit_sha=extra_commit, deleted=False,
        ))
        _local_apply_upsert(state, extra.path, extra_text)

    last_commit_sha = extras_out[-1].commit_sha if extras_out else commit_sha
    last_commit_url = commit_url
    _schedule_background_sync(state, background, f"{collection}/{uuid}")

    return EditResponse(
        commit_sha=last_commit_sha,
        commit_url=last_commit_url,
        data=merged,
        extras=extras_out,
    )


@router.delete(
    "/{collection}/{uuid}",
    response_model=DeleteResponse,
    summary="Delete one core record on upstream master (editor-only)",
)
async def delete_record(
    request: Request,
    background: BackgroundTasks,
    collection: str,
    uuid: str,
) -> DeleteResponse:
    user_session = _editor_session(request)
    state: AppState = request.app.state.bkk
    upstream = _require_upstream(state)
    _require_collection(collection)

    try:
        raw = await request.body()
        payload = await request.json() if raw else {}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Expected JSON request body") from exc
    req = DeleteRequest.model_validate(payload)

    type_name, rel_path, display_label = _lookup_record(state, collection, uuid)

    token = user_session.access_token
    upstream_branch = state.config.core_pr_base
    base_payload = _fetch_file(token, upstream, rel_path, upstream_branch)
    if base_payload is None:
        raise HTTPException(
            status_code=404,
            detail=f"{rel_path} not found on {upstream}@{upstream_branch}; nothing to delete",
        )
    parent_sha = base_payload.get("sha")
    if not isinstance(parent_sha, str):
        raise HTTPException(
            status_code=502,
            detail="GitHub file payload has no sha for parent",
        )

    commit_message = req.message or f"Delete {collection}/{display_label or uuid}"
    commit_sha, commit_url = _delete_file(
        token=token, repo=upstream, rel_path=rel_path, branch=upstream_branch,
        message=commit_message, parent_sha=parent_sha,
    )
    _local_apply_delete(state, rel_path, uuid, type_name)
    _schedule_background_sync(state, background, f"{collection}/{uuid}")

    return DeleteResponse(commit_sha=commit_sha, commit_url=commit_url)
