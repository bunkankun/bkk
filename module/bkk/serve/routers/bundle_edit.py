"""GitHub-backed editing for per-text BKK bundle repositories."""

from __future__ import annotations

import base64
import binascii
import copy
import re
import time
import uuid
from typing import Any, Literal
from urllib.parse import quote

import yaml
from fastapi import APIRouter, HTTPException, Path as PathParam, Request
from pydantic import BaseModel, Field

from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.idassigner import allocate_marker_ids
from bkk.importer.write.yaml_writer import dump, marker_to_flow, reflow_manifest
from bkk.marker_assets import (
    VALID_BUCKETS,
    build_marker_asset,
    effective_markers_for_bucket,
    marker_asset_entry_for_seq,
    marker_asset_filename,
    marker_asset_hash,
    split_inline_external_markers,
    toc_marker_ids,
)

from ..state import AppState, UserSession
from .auth import SESSION_COOKIE, _github_json, _github_status

router = APIRouter(prefix="/bundles", tags=["bundles"])


class TextSplice(BaseModel):
    start: int = Field(ge=0)
    delete_count: int = Field(ge=0)
    insert: str = ""


class BundleEditRequest(BaseModel):
    base_commit_sha: str
    bucket: Literal["front", "body", "back"]
    text: str
    markers: list[dict[str, Any]]
    text_splices: list[TextSplice] = Field(default_factory=list)
    renamed_marker_ids: dict[str, str] = Field(default_factory=dict)
    acknowledge_toc_deletions: bool = False
    unresolved_marker_indexes: list[int] = Field(default_factory=list)
    message: str | None = None


class MarkerIdAllocationRequest(BaseModel):
    base_commit_sha: str
    bucket: Literal["front", "body", "back"]
    marker_types: list[str] = Field(min_length=1, max_length=1000)
    occupied_ids: list[str] = Field(default_factory=list, max_length=100_000)


def _session(request: Request) -> UserSession:
    session = request.app.state.bkk.sessions.get(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise HTTPException(status_code=401, detail="GitHub login required")
    return session


def _repo(state: AppState, textid: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", textid):
        raise HTTPException(status_code=400, detail="invalid bundle textid")
    return f"{state.config.bundle_github_org}/{textid}"


def _branch(state: AppState, token: str, repo: str) -> str:
    configured = state.config.bundle_github_branch
    if configured and configured != "auto":
        return configured
    try:
        payload = _github_json("GET", f"/repos/{repo}", token)
    except HTTPException as exc:
        if _github_status(exc) == 404:
            raise HTTPException(status_code=404, detail=f"{repo} not found") from exc
        raise
    default_branch = (payload or {}).get("default_branch")
    if not isinstance(default_branch, str) or not default_branch:
        raise HTTPException(
            status_code=502,
            detail=f"GitHub repository {repo} has no default branch",
        )
    return default_branch


def _content_path(path: str) -> str:
    return quote(path, safe="/")


def _decode_file(payload: dict[str, Any], path: str) -> str:
    content = payload.get("content")
    if not isinstance(content, str):
        raise HTTPException(status_code=502, detail=f"GitHub file {path} has no content")
    try:
        return base64.b64decode(content, validate=False).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=502, detail=f"GitHub file {path} is not UTF-8") from exc


def _fetch_file(token: str, repo: str, path: str, ref: str) -> tuple[dict[str, Any], str]:
    try:
        payload = _github_json(
            "GET",
            f"/repos/{repo}/contents/{_content_path(path)}?ref={quote(ref, safe='')}",
            token,
        )
    except HTTPException as exc:
        status = _github_status(exc)
        if status == 404:
            raise HTTPException(status_code=404, detail=f"{path} not found in {repo}") from exc
        raise
    if not isinstance(payload, dict) or payload.get("type") != "file":
        raise HTTPException(status_code=502, detail=f"{path} in {repo} is not a file")
    if isinstance(payload.get("content"), str) and payload["content"]:
        return payload, _decode_file(payload, path)
    # The Contents API omits inline content for files above 1 MiB. Fetch the
    # blob explicitly so large juan YAML files remain editable.
    blob_sha = payload.get("sha")
    if not isinstance(blob_sha, str):
        raise HTTPException(status_code=502, detail=f"GitHub file {path} has no blob SHA")
    blob = _github_json("GET", f"/repos/{repo}/git/blobs/{blob_sha}", token)
    if not isinstance(blob, dict):
        raise HTTPException(status_code=502, detail=f"GitHub blob for {path} is invalid")
    return payload, _decode_file(blob, path)


def _head_sha(token: str, repo: str, branch: str) -> str:
    try:
        payload = _github_json(
            "GET", f"/repos/{repo}/git/ref/heads/{quote(branch, safe='')}", token,
        )
    except HTTPException as exc:
        if _github_status(exc) == 404:
            raise HTTPException(status_code=404, detail=f"{repo}@{branch} not found") from exc
        raise
    sha = ((payload or {}).get("object") or {}).get("sha")
    if not isinstance(sha, str):
        raise HTTPException(status_code=502, detail="GitHub branch response has no SHA")
    return sha


def _load_remote(
    token: str, repo: str, branch: str, textid: str, seq: int, *, ref: str | None = None,
) -> dict[str, Any]:
    base_sha = ref or _head_sha(token, repo, branch)
    manifest_path = f"{textid}.manifest.yaml"
    _manifest_payload, manifest_text = _fetch_file(token, repo, manifest_path, base_sha)
    manifest = yaml.safe_load(manifest_text) or {}
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=422, detail="bundle manifest is not a mapping")
    parts = (manifest.get("assets") or {}).get("parts") or []
    part = next(
        (p for p in parts if isinstance(p, dict) and p.get("seq") == seq), None,
    )
    if part is None or not isinstance(part.get("filename"), str):
        raise HTTPException(status_code=404, detail=f"{textid} juan {seq} not found")
    juan_path = part["filename"]
    _juan_payload, juan_text = _fetch_file(token, repo, juan_path, base_sha)
    juan = yaml.safe_load(juan_text) or {}
    if not isinstance(juan, dict):
        raise HTTPException(status_code=422, detail=f"{juan_path} is not a mapping")

    marker_path: str | None = None
    marker_asset: dict[str, Any] | None = None
    marker_entry = marker_asset_entry_for_seq(manifest, seq)
    if marker_entry is not None and isinstance(marker_entry.get("filename"), str):
        marker_path = marker_entry["filename"]
        _marker_payload, marker_text = _fetch_file(token, repo, marker_path, base_sha)
        parsed = yaml.safe_load(marker_text) or {}
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=422, detail=f"{marker_path} is not a mapping")
        marker_asset = parsed
    return {
        "base_sha": base_sha,
        "manifest_path": manifest_path,
        "manifest": manifest,
        "juan_path": juan_path,
        "juan": juan,
        "marker_path": marker_path,
        "marker_asset": marker_asset,
    }


def _validate_markers(
    markers: list[dict[str, Any]], text_len: int, textid: str,
) -> None:
    seen_ids: set[str] = set()
    last_offset = -1
    for index, marker in enumerate(markers):
        marker_type = marker.get("type")
        offset = marker.get("offset")
        if not isinstance(marker_type, str) or not marker_type:
            raise HTTPException(status_code=422, detail=f"marker {index}: type is required")
        if not isinstance(offset, int) or isinstance(offset, bool):
            raise HTTPException(status_code=422, detail=f"marker {index}: offset must be an integer")
        if not 0 <= offset <= text_len:
            raise HTTPException(
                status_code=422,
                detail=f"marker {index}: offset {offset} outside [0, {text_len}]",
            )
        if offset < last_offset:
            raise HTTPException(status_code=422, detail="markers must be ordered by offset")
        last_offset = offset
        length = marker.get("length")
        if length is not None:
            if not isinstance(length, int) or isinstance(length, bool) or length < 0:
                raise HTTPException(status_code=422, detail=f"marker {index}: length must be non-negative")
            if offset + length > text_len:
                raise HTTPException(status_code=422, detail=f"marker {index}: span exceeds text")
        marker_id = marker.get("id")
        if isinstance(marker_id, str) and marker_id:
            if marker_id in seen_ids and marker_type not in ("tls:div-start", "tls:div-end"):
                raise HTTPException(status_code=422, detail=f"duplicate marker id {marker_id}")
            seen_ids.add(marker_id)
            if marker_type not in ("tls:ann", "voice"):
                parts = marker_id.split("_", 2)
                if len(parts) != 3 or not parts[2] or parts[0] != textid:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"marker {index}: id must match "
                            f"{textid}_<edition>_<location>"
                        ),
                    )


def _replay_splices(original: str, splices: list[TextSplice]) -> str:
    text = original
    for edit in splices:
        end = edit.start + edit.delete_count
        if end > len(text):
            raise HTTPException(status_code=422, detail="text splice is outside the current text")
        text = text[:edit.start] + edit.insert + text[end:]
    return text


def _map_position(position: int, edit: TextSplice, *, right: bool) -> int:
    start = edit.start
    end = start + edit.delete_count
    delta = len(edit.insert) - edit.delete_count
    if position < start:
        return position
    if position > end:
        return position + delta
    if edit.delete_count == 0 and position == start:
        return start + (len(edit.insert) if right else 0)
    if position == end:
        return start + len(edit.insert)
    return start + (len(edit.insert) if right else 0)


def _rebase_toc_spans(
    manifest: dict[str, Any], seq: int, bucket: str, edits: list[TextSplice],
) -> None:
    for entry in manifest.get("table_of_contents") or []:
        if not isinstance(entry, dict):
            continue
        ref = entry.get("ref")
        if not isinstance(ref, dict) or ref.get("seq") != seq:
            continue
        span = ref.get("span")
        if not (
            isinstance(span, list) and len(span) == 3 and span[0] == bucket
            and isinstance(span[1], int) and isinstance(span[2], int)
        ):
            continue
        start, end = span[1], span[2]
        for edit in edits:
            start = _map_position(start, edit, right=False)
            end = _map_position(end, edit, right=True)
        ref["span"] = [bucket, start, max(start, end)]


def _cascade_toc_marker_ids(
    manifest: dict[str, Any],
    seq: int,
    all_marker_ids: set[str],
    renamed: dict[str, str],
    acknowledge_deletions: bool,
) -> list[str]:
    for old_id, new_id in renamed.items():
        if not old_id or not new_id or new_id not in all_marker_ids:
            raise HTTPException(
                status_code=422,
                detail=f"renamed marker {old_id!r} does not resolve to {new_id!r}",
            )
    removed: list[str] = []
    kept: list[Any] = []
    for entry in manifest.get("table_of_contents") or []:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        ref = entry.get("ref")
        if not isinstance(ref, dict) or ref.get("seq") != seq:
            kept.append(entry)
            continue
        marker_id = ref.get("marker_id")
        if isinstance(marker_id, str) and marker_id in renamed:
            ref["marker_id"] = renamed[marker_id]
            marker_id = renamed[marker_id]
        if isinstance(marker_id, str) and marker_id and marker_id not in all_marker_ids:
            if not acknowledge_deletions:
                raise HTTPException(
                    status_code=422,
                    detail=f"deleting TOC marker {marker_id} requires acknowledgement",
                )
            removed.append(marker_id)
            continue
        kept.append(entry)
    manifest["table_of_contents"] = kept
    return removed


def _patch_self_hash(obj: dict[str, Any]) -> str:
    value = copy.deepcopy(obj)
    value["hash"] = ZERO_HASH
    return sha256_jcs(value)


def _prepare_files(
    remote: dict[str, Any], textid: str, seq: int, request: BundleEditRequest,
) -> tuple[dict[str, str | None], list[str]]:
    manifest = copy.deepcopy(remote["manifest"])
    juan = copy.deepcopy(remote["juan"])
    original_bucket = juan.get(request.bucket)
    if not isinstance(original_bucket, dict):
        raise HTTPException(status_code=404, detail=f"bucket {request.bucket} not found")
    original_text = original_bucket.get("text") or ""
    if _replay_splices(original_text, request.text_splices) != request.text:
        raise HTTPException(status_code=422, detail="text splice history does not produce submitted text")
    if request.unresolved_marker_indexes:
        raise HTTPException(status_code=422, detail="ambiguous markers must be resolved before save")
    _validate_markers(request.markers, len(request.text), textid)

    original_bucket["text"] = request.text
    original_bucket["hash"] = sha256_text(request.text) if request.text else ZERO_HASH
    keep_ids = {
        request.renamed_marker_ids.get(marker_id, marker_id)
        for marker_id in toc_marker_ids(manifest, seq)
    }
    inline, external = split_inline_external_markers(request.markers, keep_ids=keep_ids)
    if inline:
        original_bucket["markers"] = [marker_to_flow(dict(marker)) for marker in inline]
    else:
        original_bucket.pop("markers", None)

    marker_asset = copy.deepcopy(remote["marker_asset"])
    marker_path = remote["marker_path"]
    assets = manifest.setdefault("assets", {})
    marker_entries = assets.get("markers") or []
    if external:
        if marker_asset is None:
            edition = ((juan.get("metadata") or {}).get("edition") or {}).get("short")
            edition = edition if isinstance(edition, str) else None
            marker_asset = build_marker_asset(textid, seq, edition, {})
            marker_path = marker_asset_filename(textid, seq, edition)
            marker_entries = [
                entry for entry in marker_entries
                if not (isinstance(entry, dict) and entry.get("seq") == seq)
            ]
            marker_entries.append({"seq": seq, "filename": marker_path, "hash": ZERO_HASH})
            marker_entries.sort(key=lambda entry: entry.get("seq", 0))
            assets["markers"] = marker_entries
        markers_obj = marker_asset.setdefault("markers", {})
        markers_obj[request.bucket] = [marker_to_flow(dict(marker)) for marker in external]
    elif marker_asset is not None:
        markers_obj = marker_asset.setdefault("markers", {})
        markers_obj.pop(request.bucket, None)

    _rebase_toc_spans(manifest, seq, request.bucket, request.text_splices)
    all_marker_ids: set[str] = set()
    for bucket in VALID_BUCKETS:
        effective = (
            request.markers
            if bucket == request.bucket
            else effective_markers_for_bucket(juan, bucket, marker_asset)
        )
        all_marker_ids.update(
            marker["id"] for marker in effective
            if isinstance(marker.get("id"), str) and marker["id"]
        )
    removed_toc = _cascade_toc_marker_ids(
        manifest,
        seq,
        all_marker_ids,
        request.renamed_marker_ids,
        request.acknowledge_toc_deletions,
    )

    files: dict[str, str | None] = {}
    if marker_asset is not None and any(
        marker_asset.get("markers", {}).get(bucket) for bucket in VALID_BUCKETS
    ):
        marker_asset["hash"] = marker_asset_hash(marker_asset)
        files[str(marker_path)] = dump(marker_asset)
        entry = marker_asset_entry_for_seq(manifest, seq)
        if entry is not None:
            entry["hash"] = marker_asset["hash"]
    elif marker_path is not None:
        files[str(marker_path)] = None
        assets["markers"] = [
            entry for entry in marker_entries
            if not (isinstance(entry, dict) and entry.get("seq") == seq)
        ]

    juan["hash"] = _patch_self_hash(juan)
    files[remote["juan_path"]] = dump(juan)
    for part in assets.get("parts") or []:
        if isinstance(part, dict) and part.get("seq") == seq:
            part["hash"] = juan["hash"]
            break
    reflow_manifest(manifest)
    manifest["hash"] = manifest_hash(manifest)
    files[remote["manifest_path"]] = dump(manifest)
    return files, removed_toc


def _create_blob(token: str, repo: str, text: str) -> str:
    result = _github_json(
        "POST", f"/repos/{repo}/git/blobs", token,
        json={"content": text, "encoding": "utf-8"},
    )
    sha = (result or {}).get("sha")
    if not isinstance(sha, str):
        raise HTTPException(status_code=502, detail="GitHub blob response has no SHA")
    return sha


def _commit_files(
    token: str,
    repo: str,
    parent_sha: str,
    branch: str,
    message: str,
    files: dict[str, str | None],
    *,
    create_branch: bool,
) -> str:
    commit = _github_json("GET", f"/repos/{repo}/git/commits/{parent_sha}", token)
    base_tree = ((commit or {}).get("tree") or {}).get("sha")
    if not isinstance(base_tree, str):
        raise HTTPException(status_code=502, detail="GitHub commit response has no tree SHA")
    entries: list[dict[str, Any]] = []
    for path, text in files.items():
        entries.append({
            "path": path,
            "mode": "100644",
            "type": "blob",
            "sha": _create_blob(token, repo, text) if text is not None else None,
        })
    tree = _github_json(
        "POST", f"/repos/{repo}/git/trees", token,
        json={"base_tree": base_tree, "tree": entries},
    )
    tree_sha = (tree or {}).get("sha")
    if not isinstance(tree_sha, str):
        raise HTTPException(status_code=502, detail="GitHub tree response has no SHA")
    created = _github_json(
        "POST", f"/repos/{repo}/git/commits", token,
        json={"message": message, "tree": tree_sha, "parents": [parent_sha]},
    )
    commit_sha = (created or {}).get("sha")
    if not isinstance(commit_sha, str):
        raise HTTPException(status_code=502, detail="GitHub commit response has no SHA")
    ref_path = f"/repos/{repo}/git/refs"
    if create_branch:
        _github_json(
            "POST", ref_path, token,
            json={"ref": f"refs/heads/{branch}", "sha": commit_sha},
        )
    else:
        try:
            _github_json(
                "PATCH", f"{ref_path}/heads/{quote(branch, safe='')}", token,
                json={"sha": commit_sha, "force": False},
            )
        except HTTPException as exc:
            if _github_status(exc) in (409, 422):
                raise HTTPException(status_code=409, detail="bundle changed; reload and retry") from exc
            raise
    return commit_sha


def _ensure_fork(token: str, upstream: str, login: str, textid: str) -> str:
    fork = f"{login}/{textid}"
    try:
        payload = _github_json("GET", f"/repos/{fork}", token)
        parent = ((payload or {}).get("parent") or {}).get("full_name")
        if not isinstance(parent, str) or parent.lower() != upstream.lower():
            raise HTTPException(status_code=409, detail=f"{fork} exists but is not a fork of {upstream}")
        return fork
    except HTTPException as exc:
        if _github_status(exc) != 404:
            raise
    _github_json("POST", f"/repos/{upstream}/forks", token, json={})
    for _ in range(10):
        try:
            _github_json("GET", f"/repos/{fork}", token)
            return fork
        except HTTPException as exc:
            if _github_status(exc) != 404:
                raise
            time.sleep(0.25)
    raise HTTPException(status_code=502, detail="GitHub fork was not ready in time")


@router.get("/{textid}/juan/{seq}/edit")
def get_bundle_edit(
    request: Request,
    textid: str = PathParam(...),
    seq: int = PathParam(..., ge=0),
) -> dict[str, Any]:
    session = _session(request)
    state: AppState = request.app.state.bkk
    repo = _repo(state, textid)
    branch = _branch(state, session.access_token, repo)
    remote = _load_remote(session.access_token, repo, branch, textid, seq)
    edition = ((remote["juan"].get("metadata") or {}).get("edition") or {}).get("short")
    if not isinstance(edition, str) or not edition:
        edition = ""
    buckets: dict[str, Any] = {}
    for bucket in VALID_BUCKETS:
        bucket_obj = remote["juan"].get(bucket)
        if not isinstance(bucket_obj, dict):
            continue
        buckets[bucket] = {
            "text": bucket_obj.get("text") or "",
            "markers": effective_markers_for_bucket(
                remote["juan"], bucket, remote["marker_asset"],
            ),
        }
    return {
        "repository": repo,
        "branch": branch,
        "base_commit_sha": remote["base_sha"],
        "seq": seq,
        "buckets": buckets,
        "toc_marker_ids": sorted(toc_marker_ids(remote["manifest"], seq)),
        "marker_id_context": {
            "edition": edition,
            "juan_label": f"{seq:03d}",
        },
    }


@router.post("/{textid}/juan/{seq}/edit/marker-ids")
def allocate_bundle_marker_ids(
    request: Request,
    payload: MarkerIdAllocationRequest,
    textid: str = PathParam(...),
    seq: int = PathParam(..., ge=0),
) -> dict[str, list[str]]:
    session = _session(request)
    state: AppState = request.app.state.bkk
    repo = _repo(state, textid)
    branch = _branch(state, session.access_token, repo)
    current_sha = _head_sha(session.access_token, repo, branch)
    if current_sha != payload.base_commit_sha:
        raise HTTPException(status_code=409, detail="bundle changed; reload and retry")
    remote = _load_remote(
        session.access_token, repo, branch, textid, seq, ref=payload.base_commit_sha,
    )
    if not isinstance(remote["juan"].get(payload.bucket), dict):
        raise HTTPException(status_code=404, detail=f"bucket {payload.bucket} not found")
    edition = ((remote["juan"].get("metadata") or {}).get("edition") or {}).get("short")
    if not isinstance(edition, str) or not edition:
        raise HTTPException(status_code=422, detail="juan edition short is required for marker IDs")
    marker_types = []
    for index, marker_type in enumerate(payload.marker_types):
        if not isinstance(marker_type, str) or not marker_type:
            raise HTTPException(
                status_code=422, detail=f"marker type {index} must be a non-empty string",
            )
        marker_types.append(marker_type)
    occupied = set(payload.occupied_ids)
    for bucket in VALID_BUCKETS:
        for marker in effective_markers_for_bucket(
            remote["juan"], bucket, remote["marker_asset"],
        ):
            marker_id = marker.get("id")
            if isinstance(marker_id, str) and marker_id:
                occupied.add(marker_id)
    return {
        "ids": allocate_marker_ids(
            marker_types,
            text_id=textid,
            edition=edition,
            juan_label=f"{seq:03d}",
            occupied_ids=occupied,
        ),
    }


@router.post("/{textid}/juan/{seq}/edit")
def save_bundle_edit(
    request: Request,
    payload: BundleEditRequest,
    textid: str = PathParam(...),
    seq: int = PathParam(..., ge=0),
) -> dict[str, Any]:
    session = _session(request)
    state: AppState = request.app.state.bkk
    upstream = _repo(state, textid)
    branch = _branch(state, session.access_token, upstream)
    current_sha = _head_sha(session.access_token, upstream, branch)
    if current_sha != payload.base_commit_sha:
        raise HTTPException(status_code=409, detail="bundle changed; reload and retry")
    remote = _load_remote(
        session.access_token, upstream, branch, textid, seq, ref=payload.base_commit_sha,
    )
    files, removed_toc = _prepare_files(remote, textid, seq, payload)
    message = payload.message or f"Edit {textid} juan {seq}"

    if session.is_admin:
        commit_sha = _commit_files(
            session.access_token,
            upstream,
            payload.base_commit_sha,
            branch,
            message,
            files,
            create_branch=False,
        )
        return {
            "kind": "commit",
            "commit_sha": commit_sha,
            "url": f"https://github.com/{upstream}/commit/{commit_sha}",
            "removed_toc_marker_ids": removed_toc,
        }

    fork = _ensure_fork(session.access_token, upstream, session.login, textid)
    edit_branch = f"bkk-edit/{seq:03d}-{uuid.uuid4().hex[:10]}"
    commit_sha = _commit_files(
        session.access_token,
        fork,
        payload.base_commit_sha,
        edit_branch,
        message,
        files,
        create_branch=True,
    )
    pr = _github_json(
        "POST", f"/repos/{upstream}/pulls", session.access_token,
        json={
            "title": message,
            "head": f"{session.login}:{edit_branch}",
            "base": branch,
            "body": f"Edits `{textid}` juan {seq} from the BKK web editor.",
        },
    )
    pr_url = (pr or {}).get("html_url")
    pr_number = (pr or {}).get("number")
    if not isinstance(pr_url, str):
        raise HTTPException(status_code=502, detail="GitHub pull request response has no URL")
    return {
        "kind": "pull_request",
        "commit_sha": commit_sha,
        "url": pr_url,
        "pull_request_number": pr_number,
        "removed_toc_marker_ids": removed_toc,
    }
