"""GitHub-backed user workspace file API."""

from __future__ import annotations

import base64
import binascii
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request

from .auth import SESSION_COOKIE, _github_json
from ..state import UserSession

router = APIRouter(prefix="/workspace", tags=["workspace"])

ALLOWED_ROOTS = ("settings/", "notes/", "searches/", "lists/")


def _session(request: Request) -> UserSession:
    session_id = request.cookies.get(SESSION_COOKIE)
    user_session = request.app.state.bkk.sessions.get(session_id)
    if user_session is None:
        raise HTTPException(status_code=401, detail="Login required")
    return user_session


def _normalize_workspace_path(path: str) -> str:
    path = path.strip()
    if path.startswith("/"):
        raise HTTPException(status_code=400, detail="Workspace path must be relative")
    parts = path.split("/")
    if not path or any(part in ("", ".", "..") for part in parts):
        raise HTTPException(status_code=400, detail="Invalid workspace path")
    normalized = "/".join(parts)
    if not any(normalized.startswith(root) for root in ALLOWED_ROOTS):
        raise HTTPException(
            status_code=400,
            detail=(
                "Workspace path must be under one of: "
                + ", ".join(ALLOWED_ROOTS)
            ),
        )
    return normalized


def _normalize_prefix(prefix: str) -> str:
    if prefix == "":
        return prefix
    path = prefix.strip()
    if path.startswith("/"):
        raise HTTPException(status_code=400, detail="Workspace prefix must be relative")
    parts = path.split("/")
    if any(part in (".", "..") for part in parts if part):
        raise HTTPException(status_code=400, detail="Invalid workspace prefix")
    if not path.endswith("/"):
        path += "/"
    if path and not any(root.startswith(path) or path.startswith(root) for root in ALLOWED_ROOTS):
        raise HTTPException(
            status_code=400,
            detail=(
                "Workspace prefix must be under one of: "
                + ", ".join(ALLOWED_ROOTS)
            ),
        )
    return path


def _contents_path(path: str) -> str:
    return quote(path, safe="/")


def _github_404(exc: HTTPException) -> bool:
    detail = exc.detail
    return (
        exc.status_code == 502
        and isinstance(detail, dict)
        and detail.get("github_status") == 404
    )


def _github_409(exc: HTTPException) -> bool:
    detail = exc.detail
    return (
        exc.status_code == 502
        and isinstance(detail, dict)
        and detail.get("github_status") in (409, 422)
    )


def _get_content(user_session: UserSession, path: str) -> Any:
    workspace = user_session.workspace
    repo = workspace["repo"]
    branch = workspace["branch"]
    return _github_json(
        "GET",
        f"/repos/{repo}/contents/{_contents_path(path)}?ref={quote(branch, safe='')}",
        user_session.access_token,
    )


def _file_payload(user_session: UserSession, path: str) -> dict[str, Any] | None:
    try:
        payload = _get_content(user_session, path)
    except HTTPException as exc:
        if _github_404(exc):
            return None
        raise
    if isinstance(payload, list) or payload.get("type") != "file":
        raise HTTPException(status_code=400, detail="Workspace path is not a file")
    return payload


@router.get("/files", summary="List workspace files under an allowlisted prefix")
def list_files(
    request: Request,
    prefix: str = Query("", description="Workspace folder prefix, e.g. settings/"),
) -> dict[str, Any]:
    user_session = _session(request)
    normalized = _normalize_prefix(prefix)
    if normalized == "":
        entries: list[dict[str, Any]] = []
        for root in ALLOWED_ROOTS:
            try:
                payload = _get_content(user_session, root.rstrip("/"))
            except HTTPException as exc:
                if _github_404(exc):
                    continue
                raise
            if isinstance(payload, list):
                entries.extend(payload)
        payload = entries
    else:
        try:
            payload = _get_content(user_session, normalized.rstrip("/"))
        except HTTPException as exc:
            if _github_404(exc):
                return {"prefix": normalized, "files": []}
            raise

    if not isinstance(payload, list):
        raise HTTPException(status_code=400, detail="Workspace prefix is not a folder")
    files = [
        {
            "path": item.get("path"),
            "name": item.get("name"),
            "type": item.get("type"),
            "sha": item.get("sha"),
            "size": item.get("size"),
        }
        for item in payload
        if isinstance(item, dict)
        and isinstance(item.get("path"), str)
        and any(item["path"].startswith(root) for root in ALLOWED_ROOTS)
    ]
    return {"prefix": normalized, "files": files}


@router.get("/files/{path:path}", summary="Read one workspace file")
def read_file(request: Request, path: str) -> dict[str, Any]:
    user_session = _session(request)
    normalized = _normalize_workspace_path(path)
    payload = _file_payload(user_session, normalized)
    if payload is None:
        raise HTTPException(status_code=404, detail="Workspace file not found")
    content = payload.get("content")
    if not isinstance(content, str):
        raise HTTPException(status_code=502, detail="GitHub file payload has no content")
    try:
        text = base64.b64decode(content, validate=False).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Workspace file content is not valid UTF-8",
        ) from exc
    return {
        "path": normalized,
        "sha": payload.get("sha"),
        "content": text,
        "encoding": "utf-8",
    }


@router.put("/files/{path:path}", summary="Create or update one workspace file")
async def write_file(request: Request, path: str) -> dict[str, Any]:
    user_session = _session(request)
    normalized = _normalize_workspace_path(path)
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Expected JSON request body") from exc
    content = body.get("content") if isinstance(body, dict) else None
    expected_sha = body.get("sha") if isinstance(body, dict) else None
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="Workspace file content must be a string")
    if expected_sha is not None and not isinstance(expected_sha, str):
        raise HTTPException(status_code=400, detail="Workspace file sha must be a string")

    current = _file_payload(user_session, normalized)
    payload: dict[str, Any] = {
        "message": (
            f"Update BKK workspace file: {normalized}"
            if current is not None
            else f"Create BKK workspace file: {normalized}"
        ),
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": user_session.workspace["branch"],
    }
    if current is not None:
        current_sha = current.get("sha")
        if not isinstance(current_sha, str):
            raise HTTPException(status_code=502, detail="GitHub file payload has no sha")
        if expected_sha != current_sha:
            raise HTTPException(
                status_code=409,
                detail="Workspace file changed remotely; reload before saving",
            )
        payload["sha"] = current_sha

    try:
        result = _github_json(
            "PUT",
            f"/repos/{user_session.workspace['repo']}/contents/{_contents_path(normalized)}",
            user_session.access_token,
            json=payload,
        )
    except HTTPException as exc:
        if _github_409(exc):
            raise HTTPException(
                status_code=409,
                detail="Workspace file changed remotely; reload before saving",
            ) from exc
        raise

    content_result = result.get("content") if isinstance(result, dict) else None
    return {
        "path": normalized,
        "sha": content_result.get("sha") if isinstance(content_result, dict) else None,
        "commit": result.get("commit") if isinstance(result, dict) else None,
    }


@router.delete("/files/{path:path}", summary="Delete one workspace file")
def delete_file(
    request: Request,
    path: str,
    sha: str | None = Query(None, description="Expected current GitHub blob sha"),
) -> dict[str, Any]:
    user_session = _session(request)
    normalized = _normalize_workspace_path(path)
    current = _file_payload(user_session, normalized)
    if current is None:
        raise HTTPException(status_code=404, detail="Workspace file not found")
    current_sha = current.get("sha")
    if not isinstance(current_sha, str):
        raise HTTPException(status_code=502, detail="GitHub file payload has no sha")
    if sha != current_sha:
        raise HTTPException(
            status_code=409,
            detail="Workspace file changed remotely; reload before deleting",
        )
    payload = {
        "message": f"Delete BKK workspace file: {normalized}",
        "sha": current_sha,
        "branch": user_session.workspace["branch"],
    }
    try:
        result = _github_json(
            "DELETE",
            f"/repos/{user_session.workspace['repo']}/contents/{_contents_path(normalized)}",
            user_session.access_token,
            json=payload,
        )
    except HTTPException as exc:
        if _github_409(exc):
            raise HTTPException(
                status_code=409,
                detail="Workspace file changed remotely; reload before deleting",
            ) from exc
        raise
    return {"path": normalized, "commit": result.get("commit") if isinstance(result, dict) else None}
