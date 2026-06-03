"""Write-path endpoints for annotations: Bluesky auth + record creation.

All state lives in the in-memory ``UserSession`` (see ``state.py``). The
backend exchanges the user's app password for an atproto session, signs
``com.atproto.repo.createRecord`` calls with the resulting JWT, and never
persists Bluesky credentials to disk. A server restart logs the user out of
Bluesky (not GitHub) — documented in the panel UI.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..atproto import (
    ANNOTATION_NSID,
    DEFAULT_PDS,
    create_record,
    create_session,
)
from ..state import AppState, BlueskySession, UserSession
from .auth import SESSION_COOKIE


router = APIRouter(tags=["annotations-write"])


def _state(request: Request) -> AppState:
    return request.app.state.bkk


def _require_user(request: Request) -> tuple[str, UserSession]:
    session_id = request.cookies.get(SESSION_COOKIE)
    user = _state(request).sessions.get(session_id)
    if not session_id or user is None:
        raise HTTPException(status_code=401, detail="Not logged in")
    return session_id, user


def _require_bluesky(user: UserSession) -> BlueskySession:
    if user.bluesky is None:
        raise HTTPException(
            status_code=400,
            detail="No Bluesky session attached; POST /annotations/bluesky/session first.",
        )
    return user.bluesky


class BlueskyLoginRequest(BaseModel):
    handle: str = Field(..., min_length=3)
    app_password: str = Field(..., min_length=8)


class BlueskyStatus(BaseModel):
    handle: str | None = None
    did: str | None = None


@router.get(
    "/annotations/bluesky/session",
    response_model=BlueskyStatus,
    summary="Current Bluesky connection status (no tokens returned)",
)
def get_bluesky_session(request: Request) -> BlueskyStatus:
    _, user = _require_user(request)
    if user.bluesky is None:
        return BlueskyStatus()
    return BlueskyStatus(handle=user.bluesky.handle, did=user.bluesky.did)


@router.post(
    "/annotations/bluesky/session",
    response_model=BlueskyStatus,
    summary="Exchange a Bluesky app password for an in-memory session",
)
def post_bluesky_session(request: Request, body: BlueskyLoginRequest) -> BlueskyStatus:
    session_id, _ = _require_user(request)
    handle = body.handle.lstrip("@").strip()
    result = create_session(handle, body.app_password)
    did = result.get("did")
    access_jwt = result.get("accessJwt")
    refresh_jwt = result.get("refreshJwt")
    if not isinstance(did, str) or not isinstance(access_jwt, str) or not isinstance(refresh_jwt, str):
        raise HTTPException(status_code=502, detail="atproto createSession returned an unexpected payload")
    bluesky = BlueskySession(
        did=did,
        handle=handle,
        access_jwt=access_jwt,
        refresh_jwt=refresh_jwt,
        service_endpoint=DEFAULT_PDS,
    )
    if not _state(request).sessions.attach_bluesky(session_id, bluesky):
        raise HTTPException(status_code=401, detail="Session expired")
    return BlueskyStatus(handle=handle, did=did)


@router.delete(
    "/annotations/bluesky/session",
    summary="Forget the in-memory Bluesky session",
)
def delete_bluesky_session(request: Request) -> dict[str, bool]:
    session_id, _ = _require_user(request)
    _state(request).sessions.detach_bluesky(session_id)
    return {"ok": True}


class AnchorIn(BaseModel):
    marker_id: str = Field(..., min_length=1)
    offset: int = Field(..., ge=0)
    length: int = Field(..., ge=0)
    end_marker_id: str | None = None
    end_length: int | None = Field(default=None, ge=0)


class AnnotationPostRequest(BaseModel):
    text_id: str = Field(..., min_length=1)
    edition: str = Field(..., min_length=1)
    anchor: AnchorIn
    payload: dict[str, Any] = Field(default_factory=dict)
    source_role: str | None = None
    supersedes: str | None = None


class AnnotationPostResponse(BaseModel):
    uri: str
    cid: str
    did: str


def _archive_to_wire(req: AnnotationPostRequest) -> dict[str, Any]:
    """Translate the BKK archive shape (snake_case) to lexicon shape (camelCase)."""
    anchor: dict[str, Any] = {
        "markerId": req.anchor.marker_id,
        "offset": req.anchor.offset,
        "length": req.anchor.length,
    }
    if req.anchor.end_marker_id is not None:
        anchor["endMarkerId"] = req.anchor.end_marker_id
    if req.anchor.end_length is not None:
        anchor["endLength"] = req.anchor.end_length

    record: dict[str, Any] = {
        "$type": ANNOTATION_NSID,
        "textId": req.text_id,
        "edition": req.edition,
        "anchor": anchor,
        "payload": req.payload,
        "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }
    record["sourceRole"] = req.source_role or f"bsky:{ANNOTATION_NSID}"
    if req.supersedes is not None:
        record["supersedes"] = req.supersedes
    return record


@router.post(
    "/annotations",
    response_model=AnnotationPostResponse,
    summary="Post an annotation as a Bluesky record under org.bunkankun.annotation",
)
def post_annotation(
    request: Request, body: AnnotationPostRequest,
) -> AnnotationPostResponse:
    session_id, user = _require_user(request)
    bluesky = _require_bluesky(user)
    record = _archive_to_wire(body)
    result, new_tokens = create_record(
        service=bluesky.service_endpoint,
        access_jwt=bluesky.access_jwt,
        refresh_jwt=bluesky.refresh_jwt,
        repo=bluesky.did,
        collection=ANNOTATION_NSID,
        record=record,
    )
    if new_tokens is not None:
        _state(request).sessions.update_bluesky_tokens(
            session_id,
            access_jwt=new_tokens["access_jwt"],
            refresh_jwt=new_tokens["refresh_jwt"],
        )
    uri = result.get("uri")
    cid = result.get("cid")
    if not isinstance(uri, str) or not isinstance(cid, str):
        raise HTTPException(status_code=502, detail="atproto createRecord returned no uri/cid")
    return AnnotationPostResponse(uri=uri, cid=cid, did=bluesky.did)


__all__ = ["router"]
