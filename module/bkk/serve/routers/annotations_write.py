"""Write-path endpoints for annotations, comments, and translation segments.

All state lives in the in-memory ``UserSession`` (see ``state.py``). The
backend exchanges the user's app password for an atproto session, signs
``com.atproto.repo.createRecord`` calls with the resulting JWT, and never
persists Bluesky credentials to disk. A server restart logs the user out of
Bluesky (not GitHub) — documented in the panel UI.

This module is one half of the "two-place rule": every conversion from the
SPA's snake_case archive shape to the camelCase Bluesky wire shape lives in
``_*_archive_to_wire`` below. The matching wire→archive direction lives in
``bkk.annotations.harvest``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..atproto import (
    ANNOTATION_NSID,
    COMMENT_NSID,
    DEFAULT_PDS,
    TRANSLATION_NSID,
    create_record,
    create_session,
    get_profiles,
)
from ..state import AppState, BlueskySession, UserSession
from .auth import SESSION_COOKIE


router = APIRouter(tags=["annotations-write"])


def _state(request: Request) -> AppState:
    return request.app.state.bkk


def _require_bluesky_enabled(request: Request) -> None:
    if not _state(request).config.bluesky_enabled:
        raise HTTPException(
            status_code=403,
            detail="Bluesky integration is disabled; set BKK_BLUESKY_ENABLE=True to enable it.",
        )


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
    avatar_url: str | None = None


@router.get(
    "/annotations/bluesky/session",
    response_model=BlueskyStatus,
    summary="Current Bluesky connection status (no tokens returned)",
)
def get_bluesky_session(request: Request) -> BlueskyStatus:
    _require_bluesky_enabled(request)
    _, user = _require_user(request)
    if user.bluesky is None:
        return BlueskyStatus()
    return BlueskyStatus(
        handle=user.bluesky.handle,
        did=user.bluesky.did,
        avatar_url=user.bluesky.avatar_url,
    )


@router.post(
    "/annotations/bluesky/session",
    response_model=BlueskyStatus,
    summary="Exchange a Bluesky app password for an in-memory session",
)
def post_bluesky_session(request: Request, body: BlueskyLoginRequest) -> BlueskyStatus:
    _require_bluesky_enabled(request)
    session_id, _ = _require_user(request)
    handle = body.handle.lstrip("@").strip()
    result = create_session(handle, body.app_password)
    did = result.get("did")
    access_jwt = result.get("accessJwt")
    refresh_jwt = result.get("refreshJwt")
    if not isinstance(did, str) or not isinstance(access_jwt, str) or not isinstance(refresh_jwt, str):
        raise HTTPException(status_code=502, detail="atproto createSession returned an unexpected payload")
    avatar_url: str | None = None
    try:
        profile = get_profiles([did]).get(did)
        if profile is not None:
            avatar = profile.get("avatar")
            if isinstance(avatar, str):
                avatar_url = avatar
    except HTTPException:
        pass
    bluesky = BlueskySession(
        did=did,
        handle=handle,
        access_jwt=access_jwt,
        refresh_jwt=refresh_jwt,
        service_endpoint=DEFAULT_PDS,
        avatar_url=avatar_url,
    )
    if not _state(request).sessions.attach_bluesky(session_id, bluesky):
        raise HTTPException(status_code=401, detail="Session expired")
    return BlueskyStatus(handle=handle, did=did, avatar_url=avatar_url)


@router.delete(
    "/annotations/bluesky/session",
    summary="Forget the in-memory Bluesky session",
)
def delete_bluesky_session(request: Request) -> dict[str, bool]:
    _require_bluesky_enabled(request)
    session_id, _ = _require_user(request)
    _state(request).sessions.detach_bluesky(session_id)
    return {"ok": True}


# ── Shared request pieces ────────────────────────────────────────────────


class AnchorIn(BaseModel):
    marker_id: str = Field(..., min_length=1)
    offset: int = Field(..., ge=0)
    length: int = Field(..., ge=0)
    end_marker_id: str | None = None
    end_length: int | None = Field(default=None, ge=0)


class StrongRefIn(BaseModel):
    uri: str = Field(..., min_length=1)
    cid: str = Field(..., min_length=1)


def _anchor_to_wire(anchor: AnchorIn) -> dict[str, Any]:
    wire: dict[str, Any] = {
        "markerId": anchor.marker_id,
        "offset": anchor.offset,
        "length": anchor.length,
    }
    if anchor.end_marker_id is not None:
        wire["endMarkerId"] = anchor.end_marker_id
    if anchor.end_length is not None:
        wire["endLength"] = anchor.end_length
    return wire


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ── Annotation ───────────────────────────────────────────────────────────


class AnnotationPostRequest(BaseModel):
    text_id: str = Field(..., min_length=1)
    edition: str = Field(..., min_length=1)
    anchor: AnchorIn
    payload: dict[str, Any] = Field(default_factory=dict)
    source_role: str | None = None
    supersedes: str | None = None


class PostResponse(BaseModel):
    uri: str
    cid: str
    did: str


# Backwards-compatible alias — older imports/types referred to this name.
AnnotationPostResponse = PostResponse


def _annotation_archive_to_wire(req: AnnotationPostRequest) -> dict[str, Any]:
    record: dict[str, Any] = {
        "$type": ANNOTATION_NSID,
        "textId": req.text_id,
        "edition": req.edition,
        "anchor": _anchor_to_wire(req.anchor),
        "payload": req.payload,
        "createdAt": _now_iso(),
    }
    record["sourceRole"] = req.source_role or f"bsky:{ANNOTATION_NSID}"
    if req.supersedes is not None:
        record["supersedes"] = req.supersedes
    return record


@router.post(
    "/annotations",
    response_model=PostResponse,
    summary=f"Post an annotation under {ANNOTATION_NSID}",
)
def post_annotation(
    request: Request, body: AnnotationPostRequest,
) -> PostResponse:
    return _post_record(
        request, ANNOTATION_NSID, _annotation_archive_to_wire(body),
    )


# ── Comment ──────────────────────────────────────────────────────────────


class CommentPostRequest(BaseModel):
    text_id: str = Field(..., min_length=1)
    edition: str | None = None
    anchor: AnchorIn | None = None
    parent: StrongRefIn | None = None
    root: StrongRefIn | None = None
    body: str = Field(..., min_length=1)
    lang: str = "en"
    supersedes: str | None = None

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        has_anchor = self.anchor is not None
        has_parent = self.parent is not None
        if has_anchor == has_parent:
            raise ValueError(
                "comment requires exactly one of `anchor` or `parent`",
            )
        if has_anchor and not self.edition:
            raise ValueError("comment with `anchor` requires `edition`")


def _comment_archive_to_wire(req: CommentPostRequest) -> dict[str, Any]:
    record: dict[str, Any] = {
        "$type": COMMENT_NSID,
        "textId": req.text_id,
        "body": req.body,
        "lang": req.lang,
        "format": "markdown",
        "createdAt": _now_iso(),
    }
    if req.edition is not None:
        record["edition"] = req.edition
    if req.anchor is not None:
        record["anchor"] = _anchor_to_wire(req.anchor)
    if req.parent is not None:
        record["parent"] = {"uri": req.parent.uri, "cid": req.parent.cid}
    if req.root is not None:
        record["root"] = {"uri": req.root.uri, "cid": req.root.cid}
    if req.supersedes is not None:
        record["supersedes"] = req.supersedes
    return record


@router.post(
    "/comments",
    response_model=PostResponse,
    summary=f"Post a comment under {COMMENT_NSID}",
)
def post_comment(
    request: Request, body: CommentPostRequest,
) -> PostResponse:
    return _post_record(request, COMMENT_NSID, _comment_archive_to_wire(body))


# ── Translation segment ──────────────────────────────────────────────────


class TranslationPostRequest(BaseModel):
    text_id: str = Field(..., min_length=1)
    edition: str = Field(..., min_length=1)
    anchor: AnchorIn
    translation_id: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    lang: str = Field(..., min_length=2)
    title: str | None = None
    note: str | None = None
    supersedes: str | None = None


def _translation_archive_to_wire(req: TranslationPostRequest) -> dict[str, Any]:
    record: dict[str, Any] = {
        "$type": TRANSLATION_NSID,
        "textId": req.text_id,
        "edition": req.edition,
        "anchor": _anchor_to_wire(req.anchor),
        "translationId": req.translation_id,
        "text": req.text,
        "lang": req.lang,
        "format": "markdown",
        "createdAt": _now_iso(),
    }
    if req.title is not None:
        record["title"] = req.title
    if req.note is not None:
        record["note"] = req.note
    if req.supersedes is not None:
        record["supersedes"] = req.supersedes
    return record


@router.post(
    "/translations",
    response_model=PostResponse,
    summary=f"Post a translation segment under {TRANSLATION_NSID}",
)
def post_translation(
    request: Request, body: TranslationPostRequest,
) -> PostResponse:
    return _post_record(
        request, TRANSLATION_NSID, _translation_archive_to_wire(body),
    )


# ── Shared post helper ───────────────────────────────────────────────────


def _post_record(
    request: Request, collection: str, record: dict[str, Any],
) -> PostResponse:
    _require_bluesky_enabled(request)
    session_id, user = _require_user(request)
    bluesky = _require_bluesky(user)
    result, new_tokens = create_record(
        service=bluesky.service_endpoint,
        access_jwt=bluesky.access_jwt,
        refresh_jwt=bluesky.refresh_jwt,
        repo=bluesky.did,
        collection=collection,
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
        raise HTTPException(
            status_code=502, detail="atproto createRecord returned no uri/cid",
        )
    return PostResponse(uri=uri, cid=cid, did=bluesky.did)


__all__ = ["router"]
