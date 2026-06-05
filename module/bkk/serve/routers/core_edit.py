"""Inline editing of bkk-core records, backed by the user's GitHub fork.

Two endpoints, both gated by a logged-in GitHub session:

* ``PATCH /core/{collection}/{uuid}`` — write proposed frontmatter / body
  changes to a feature branch on the user's fork of the upstream bkk-core
  repo. Idempotent across saves: the client passes back the ``branch``
  and ``parent_sha`` the previous response returned, so successive saves
  stack as commits on the same branch.

* ``POST  /core/{collection}/{uuid}/pr`` — opt-in opening of a pull
  request from that branch against ``upstream:<core.pr_base>``.

The read path (``GET /core/{collection}/{uuid}`` in ``core.py``) is
unchanged and still serves from the local ``core_root`` clone. The write
path never touches that clone — the maintainer keeps it fresh via
``POST /admin/core/sync`` or ``bkk core sync``.
"""

from __future__ import annotations

import base64
import binascii
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from bkk.serialize.frontmatter import parse_frontmatter, serialize_frontmatter

from ..state import AppState, UserSession
from .auth import (
    SESSION_COOKIE,
    _get_branch_ref,
    _github_json,
    _github_status,
    _repo_exists,
)
from .core import COLLECTION_TYPES, _open, _require_collection

router = APIRouter(prefix="/core", tags=["core"])

# Frontmatter keys that callers are never allowed to change. uuid + type
# pin the file to its index row; renaming either breaks every reverse
# lookup. Editor UIs should render them read-only.
LOCKED_FRONTMATTER_KEYS = frozenset({"uuid", "type"})

# Max attempts when polling for fork creation. Fork is async on GitHub's
# side; a freshly-forked repo can 404 for a few seconds.
FORK_READY_ATTEMPTS = 12


class EditRequest(BaseModel):
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    body: str = ""
    parent_sha: str | None = None
    branch: str | None = None
    message: str | None = None


class EditResponse(BaseModel):
    branch: str
    commit_sha: str
    parent_sha: str
    fork_repo: str
    compare_url: str
    pr_url: str | None
    frontmatter: dict[str, Any]
    body_markdown: str


class OpenPrRequest(BaseModel):
    branch: str
    title: str | None = None
    body: str | None = None


class OpenPrResponse(BaseModel):
    pr_url: str
    pr_number: int
    already_existed: bool


# ---------- shared helpers --------------------------------------------------


def _session(request: Request) -> UserSession:
    session_id = request.cookies.get(SESSION_COOKIE)
    user_session = request.app.state.bkk.sessions.get(session_id)
    if user_session is None:
        raise HTTPException(status_code=401, detail="Login required")
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


def _ensure_fork(token: str, login: str, upstream: str) -> str:
    """Return ``"<login>/<repo>"`` for the user's fork of ``upstream``.

    Creates the fork on demand and waits briefly for GitHub to make it
    queryable. Idempotent — a no-op if the fork already exists.
    """
    upstream_owner, upstream_name = upstream.split("/", 1)
    fork_full = f"{login}/{upstream_name}"
    existing = _repo_exists(token, login, upstream_name)
    if existing is not None:
        return fork_full
    _github_json("POST", f"/repos/{upstream}/forks", token, json={})
    for _ in range(FORK_READY_ATTEMPTS):
        time.sleep(1)
        repo = _repo_exists(token, login, upstream_name)
        if repo is not None:
            return fork_full
    raise HTTPException(
        status_code=504,
        detail=(
            f"Fork of {upstream} was requested but did not appear under "
            f"{fork_full} in time. Try the request again in a few seconds."
        ),
    )


def _ensure_branch(
    *,
    token: str,
    fork: str,
    branch: str,
    upstream: str,
    upstream_branch: str,
) -> None:
    """Create ``branch`` on ``fork`` off upstream's HEAD if it doesn't exist."""
    fork_owner, fork_repo = fork.split("/", 1)
    try:
        _get_branch_ref(
            token=token, owner=fork_owner, repo=fork_repo, branch=branch,
        )
        return
    except HTTPException as exc:
        if _github_status(exc) != 404:
            raise

    upstream_owner, upstream_name = upstream.split("/", 1)
    head = _get_branch_ref(
        token=token,
        owner=upstream_owner,
        repo=upstream_name,
        branch=upstream_branch,
    )
    sha = head.get("object", {}).get("sha")
    if not isinstance(sha, str):
        raise HTTPException(
            status_code=502,
            detail=f"upstream {upstream}#{upstream_branch} ref has no SHA",
        )
    _github_json(
        "POST",
        f"/repos/{fork}/git/refs",
        token,
        json={"ref": f"refs/heads/{branch}", "sha": sha},
    )


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


def _validate_frontmatter(
    proposed: dict[str, Any], original: dict[str, Any]
) -> dict[str, Any]:
    """Reject changes to locked keys and additions of new keys.

    Returns the merged frontmatter to write (preserves the original
    key order: every key from ``original`` in its existing order, with
    values replaced by ``proposed`` where present).
    """
    proposed_keys = set(proposed.keys())
    original_keys = set(original.keys())

    new_keys = proposed_keys - original_keys
    if new_keys:
        raise HTTPException(
            status_code=400,
            detail=(
                "frontmatter edit may not introduce new keys "
                f"(got: {sorted(new_keys)})"
            ),
        )
    for key in LOCKED_FRONTMATTER_KEYS:
        if key in proposed and proposed[key] != original.get(key):
            raise HTTPException(
                status_code=400,
                detail=f"frontmatter key {key!r} is read-only",
            )

    merged: dict[str, Any] = {}
    for key, value in original.items():
        merged[key] = proposed[key] if key in proposed else value
    return merged


def _default_branch_name(collection: str, uuid: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"bkk-edit/{collection}/{uuid}-{stamp}"


def _find_existing_pr(
    token: str, upstream: str, fork_owner: str, branch: str,
) -> dict[str, Any] | None:
    """Return the first open PR whose head matches ``fork_owner:branch``."""
    head = f"{fork_owner}:{branch}"
    payload = _github_json(
        "GET",
        f"/repos/{upstream}/pulls?state=open&head={quote(head, safe='')}",
        token,
    )
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            return first
    return None


# ---------- endpoints -------------------------------------------------------


@router.patch(
    "/{collection}/{uuid}",
    response_model=EditResponse,
    summary="Edit one core record on the user's fork; returns commit metadata",
)
async def edit_record(
    request: Request, collection: str, uuid: str,
) -> EditResponse:
    user_session = _session(request)
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
    base_fm, _base_body = parse_frontmatter(base_text)
    if base_fm.get("uuid") not in (None, uuid):
        raise HTTPException(
            status_code=500,
            detail=f"upstream {rel_path} frontmatter uuid does not match index",
        )
    if type_name and base_fm.get("type") not in (None, type_name):
        raise HTTPException(
            status_code=500,
            detail=f"upstream {rel_path} frontmatter type does not match index",
        )

    merged_fm = _validate_frontmatter(req.frontmatter, base_fm)
    new_text = serialize_frontmatter(merged_fm, req.body)

    fork = _ensure_fork(token, user_session.login, upstream)
    fork_owner = user_session.login

    branch = req.branch or _default_branch_name(collection, uuid)
    _ensure_branch(
        token=token, fork=fork, branch=branch,
        upstream=upstream, upstream_branch=upstream_branch,
    )

    if req.parent_sha is not None:
        parent_sha = req.parent_sha
    else:
        head_payload = _fetch_file(token, fork, rel_path, branch)
        if head_payload is None:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"{rel_path} missing on {fork}@{branch} after branch "
                    "creation; cannot determine parent sha"
                ),
            )
        parent_sha = head_payload.get("sha")
        if not isinstance(parent_sha, str):
            raise HTTPException(
                status_code=502,
                detail="GitHub file payload has no sha for parent",
            )

    commit_message = req.message or f"Edit {collection}/{display_label or uuid}"
    try:
        put_result = _github_json(
            "PUT",
            f"/repos/{fork}/contents/{_content_path(rel_path)}",
            token,
            json={
                "message": commit_message,
                "content": base64.b64encode(new_text.encode("utf-8")).decode("ascii"),
                "branch": branch,
                "sha": parent_sha,
            },
        )
    except HTTPException as exc:
        if _github_status(exc) in (409, 422):
            raise HTTPException(
                status_code=409,
                detail=(
                    "core record changed on the fork branch since you last "
                    "loaded it; reload and re-apply your edits"
                ),
            ) from exc
        raise

    content_result = put_result.get("content") if isinstance(put_result, dict) else None
    new_sha = (
        content_result.get("sha") if isinstance(content_result, dict) else None
    )
    commit_obj = put_result.get("commit") if isinstance(put_result, dict) else None
    commit_sha = commit_obj.get("sha") if isinstance(commit_obj, dict) else None
    if not isinstance(new_sha, str) or not isinstance(commit_sha, str):
        raise HTTPException(status_code=502, detail="GitHub PUT returned unexpected payload")

    existing_pr = _find_existing_pr(token, upstream, fork_owner, branch)
    pr_url = (
        existing_pr.get("html_url")
        if isinstance(existing_pr, dict)
        and isinstance(existing_pr.get("html_url"), str)
        else None
    )

    upstream_owner = upstream.split("/", 1)[0]
    compare_url = (
        f"https://github.com/{upstream}/compare/{upstream_branch}..."
        f"{fork_owner}:{branch}"
    )
    if upstream_owner == fork_owner:
        # Same-owner forks can't be linked via the OWNER:BRANCH syntax.
        compare_url = f"https://github.com/{upstream}/compare/{upstream_branch}...{branch}"

    return EditResponse(
        branch=branch,
        commit_sha=commit_sha,
        parent_sha=new_sha,
        fork_repo=fork,
        compare_url=compare_url,
        pr_url=pr_url,
        frontmatter=merged_fm,
        body_markdown=req.body,
    )


@router.post(
    "/{collection}/{uuid}/pr",
    response_model=OpenPrResponse,
    summary="Open (or look up) a PR from the edit branch against upstream",
)
async def open_pr(
    request: Request, collection: str, uuid: str,
) -> OpenPrResponse:
    user_session = _session(request)
    state: AppState = request.app.state.bkk
    upstream = _require_upstream(state)
    _require_collection(collection)

    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Expected JSON request body") from exc
    req = OpenPrRequest.model_validate(payload)

    _type_name, _rel_path, display_label = _lookup_record(state, collection, uuid)

    token = user_session.access_token
    fork_owner = user_session.login
    head = f"{fork_owner}:{req.branch}"
    base = state.config.core_pr_base

    title = req.title or f"Edit {collection}/{display_label or uuid}"
    body = req.body or ""

    try:
        result = _github_json(
            "POST",
            f"/repos/{upstream}/pulls",
            token,
            json={"title": title, "head": head, "base": base, "body": body},
        )
    except HTTPException as exc:
        if _github_status(exc) in (422,):
            existing = _find_existing_pr(token, upstream, fork_owner, req.branch)
            if existing is not None:
                pr_url = existing.get("html_url")
                pr_number = existing.get("number")
                if isinstance(pr_url, str) and isinstance(pr_number, int):
                    return OpenPrResponse(
                        pr_url=pr_url, pr_number=pr_number, already_existed=True,
                    )
        raise

    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="GitHub PR create returned no payload")
    pr_url = result.get("html_url")
    pr_number = result.get("number")
    if not isinstance(pr_url, str) or not isinstance(pr_number, int):
        raise HTTPException(status_code=502, detail="GitHub PR payload missing url/number")
    return OpenPrResponse(pr_url=pr_url, pr_number=pr_number, already_existed=False)
