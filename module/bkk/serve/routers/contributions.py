"""Read-path endpoint for the live Bluesky contributions feed.

The feed is populated by ``bkk.serve.contributions_feed.ContributionFeed``, a
Jetstream subscriber attached to ``AppState.contributions`` by ``app.py``'s
lifespan. When the subscriber is disabled (e.g. ``BKK_DISABLE_CONTRIBUTIONS_POLL=1``
or tests), this endpoint returns an empty list.

Items carry a ``kind`` discriminator (``annotation`` / ``comment`` /
``translation``); the SPA branches on kind to render each shape.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from bkk.annotations.harvest import (
    COMMENT_NSID,
    TRANSLATION_NSID,
    comment_archive_path,
    juan_seq_from_marker_id,
    translation_archive_path,
)
from bkk.importer.write.annotations import (
    juan_archive_path,
    write_records_jsonl,
)

from .. import selection
from ..atproto import ANNOTATION_NSID, LEGACY_ANNOTATION_NSID
from ..state import AppState
from .annotations import read_raw_records
from .auth import SESSION_COOKIE


router = APIRouter(tags=["contributions"])


VALID_CURATION_STATES = ("proposed", "accepted", "rejected", "superseded")


class ContributionParentRef(BaseModel):
    uri: str
    cid: str | None = None


class ContributionOut(BaseModel):
    kind: Literal["annotation", "comment", "translation"]
    did: str
    cid: str
    uri: str
    text_id: str
    created_at: str | None = None
    time_us: int

    # Anchor: present for all annotations + translations, optional for comments.
    edition: str | None = None
    marker_id: str | None = None
    offset: int | None = None
    length: int | None = None
    end_marker_id: str | None = None
    end_length: int | None = None

    # Annotation-only.
    payload: dict[str, Any] | None = None
    source_role: str | None = None

    # Comment-only.
    body: str | None = None
    parent: ContributionParentRef | None = None

    # Translation-only.
    translation_id: str | None = None
    text: str | None = None

    # Shared between comment + translation.
    lang: str | None = None

    # Server-enriched location fields. Resolved at read-time; None when the
    # bundle or marker can't be found (unknown textid, unparseable marker_id,
    # marker not present in the juan).
    title: str | None = None
    juan_seq: int | None = None
    bucket: str | None = None
    master_offset: int | None = None

    # Server-side curation gate. None until the harvester or a curator has
    # written a value.
    curation_state: str | None = None

    # Author profile resolved from Bluesky AppView; None when not yet cached.
    handle: str | None = None
    display_name: str | None = None
    avatar_url: str | None = None


class ContributionsResponse(BaseModel):
    items: list[ContributionOut]
    truncated: bool


def _juan_marker_index(
    state: AppState,
    textid: str,
    seq: int,
    cache: dict[tuple[str, int], dict[str, tuple[str, int]] | None],
) -> dict[str, tuple[str, int]] | None:
    """Return ``{marker_id: (bucket, master_offset)}`` for one juan.

    Returns ``None`` (cached) when the bundle or juan can't be loaded.
    """
    key = (textid, seq)
    if key in cache:
        return cache[key]
    try:
        _, juan = selection.load_juan(state.corpus_root, textid, seq)
    except Exception:
        cache[key] = None
        return None
    index: dict[str, tuple[str, int]] = {}
    for bucket in ("body", "front", "back"):
        node = juan.get(bucket)
        if not isinstance(node, dict):
            continue
        for marker in node.get("markers") or ():
            if not isinstance(marker, dict):
                continue
            mid = marker.get("id")
            moff = marker.get("offset")
            if isinstance(mid, str) and isinstance(moff, int):
                index[mid] = (bucket, moff)
    cache[key] = index
    return index


def _enrich(state: AppState, items: list[dict[str, Any]]) -> None:
    """Annotate buffer entries in-place with title, location, and author profile fields."""
    feed = state.contributions
    if feed is not None:
        dids = list({item["did"] for item in items if isinstance(item.get("did"), str)})
        feed.ensure_profiles(dids)
        for item in items:
            did = item.get("did")
            if isinstance(did, str):
                profile = feed.get_cached_profile(did)
                if profile:
                    item["handle"] = profile.get("handle")
                    item["display_name"] = profile.get("displayName")
                    item["avatar_url"] = profile.get("avatar")

    juan_cache: dict[tuple[str, int], dict[str, tuple[str, int]] | None] = {}
    for item in items:
        textid = item.get("text_id")
        if isinstance(textid, str):
            rec = state.lookup_bundle(textid)
            if rec is not None:
                item["title"] = rec.title
        marker_id = item.get("marker_id")
        if not isinstance(marker_id, str) or not isinstance(textid, str):
            continue
        seq = juan_seq_from_marker_id(marker_id)
        if seq is None:
            continue
        item["juan_seq"] = seq
        index = _juan_marker_index(state, textid, seq, juan_cache)
        if index is None:
            continue
        hit = index.get(marker_id)
        if hit is None:
            continue
        bucket, marker_off = hit
        item["bucket"] = bucket
        anchor_off = item.get("offset")
        item["master_offset"] = marker_off + (anchor_off if isinstance(anchor_off, int) else 0)


@router.get(
    "/contributions",
    response_model=ContributionsResponse,
    summary="Most recent BKK records (annotations, comments, translations) seen on Bluesky",
)
def list_contributions(
    request: Request,
    limit: int = Query(200, ge=1, le=500),
) -> ContributionsResponse:
    state: AppState = request.app.state.bkk
    feed = state.contributions
    if feed is None:
        return ContributionsResponse(items=[], truncated=False)
    items, truncated = feed.snapshot(limit)
    enriched = [dict(item) for item in items]
    _enrich(state, enriched)
    return ContributionsResponse(
        items=[ContributionOut(**i) for i in enriched],
        truncated=truncated,
    )


class CurationStatePatch(BaseModel):
    uri: str
    state: Literal["proposed", "accepted", "rejected", "superseded"]


class CurationStateResponse(BaseModel):
    uri: str
    text_id: str
    juan_seq: int | None
    curation_state: str


def _archive_path_for(
    state: AppState, *, kind: str, text_id: str, juan_seq: int | None,
) -> Path | None:
    """Return the JSONL file backing one contribution.

    Mirrors the harvest CLI's default sibling-dir convention for comments and
    translations (their roots aren't separately exposed in ``ServeConfig``).
    """
    annotations_root = state.annotations_root
    if annotations_root is None:
        return None
    if kind == "annotation" and juan_seq is not None:
        return juan_archive_path(annotations_root, text_id, juan_seq)
    if kind == "comment":
        comments_root = annotations_root.parent / "bkk-comments"
        return comment_archive_path(comments_root, text_id, juan_seq)
    if kind == "translation" and juan_seq is not None:
        translations_root = annotations_root.parent / "bkk-translations"
        return translation_archive_path(translations_root, text_id, juan_seq)
    return None


@router.patch(
    "/annotations/curation-state",
    response_model=CurationStateResponse,
    summary="Set a contribution's curation_state (editor only)",
)
def patch_curation_state(
    request: Request, body: CurationStatePatch,
) -> CurationStateResponse:
    state: AppState = request.app.state.bkk

    session_id = request.cookies.get(SESSION_COOKIE)
    user = state.sessions.get(session_id)
    if not session_id or user is None:
        raise HTTPException(status_code=401, detail="Not logged in")
    if not user.is_editor:
        raise HTTPException(status_code=403, detail="Editor role required")

    feed = state.contributions
    entry = feed.find(body.uri) if feed is not None else None
    if entry is None:
        raise HTTPException(status_code=404, detail="Contribution not in live buffer")

    text_id = entry.get("text_id")
    cid = entry.get("cid")
    if not isinstance(text_id, str) or not isinstance(cid, str):
        raise HTTPException(status_code=500, detail="Buffer entry missing text_id/cid")

    marker_id = entry.get("marker_id")
    seq = juan_seq_from_marker_id(marker_id) if isinstance(marker_id, str) else None
    kind = entry.get("kind")
    if not isinstance(kind, str):
        raise HTTPException(status_code=500, detail="Buffer entry missing kind")

    _collection_from_uri(body.uri)  # validate URI shape early; raises 400 otherwise
    archive_path = _archive_path_for(
        state, kind=kind, text_id=text_id, juan_seq=seq,
    )
    if archive_path is None:
        raise HTTPException(
            status_code=409,
            detail="Archive path unavailable for this contribution kind",
        )
    if not archive_path.exists():
        raise HTTPException(status_code=404, detail=f"Archive file missing: {archive_path}")

    records = list(read_raw_records(archive_path))
    hit_idx = next(
        (i for i, r in enumerate(records)
         if isinstance(r.get("provenance"), dict)
         and r["provenance"].get("cid") == cid),
        None,
    )
    if hit_idx is None:
        raise HTTPException(status_code=404, detail="Record not found in archive")

    records[hit_idx]["curation_state"] = body.state
    write_records_jsonl(archive_path, records, sort=(kind == "annotation"))
    feed.set_curation_state(body.uri, body.state)

    return CurationStateResponse(
        uri=body.uri, text_id=text_id, juan_seq=seq, curation_state=body.state,
    )


def _collection_from_uri(uri: str) -> str:
    try:
        _, _, _, collection, _ = uri.split("/", 4)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Bad URI: {uri}") from exc
    if collection not in (ANNOTATION_NSID, LEGACY_ANNOTATION_NSID, COMMENT_NSID, TRANSLATION_NSID):
        raise HTTPException(status_code=400, detail=f"Unknown collection: {collection}")
    return collection


__all__ = ["router"]
