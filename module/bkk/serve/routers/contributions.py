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
from pydantic import BaseModel, model_validator

from bkk.importer.write.annotations import (
    juan_archive_path,
    write_records_jsonl,
)

from .. import selection
from ..atproto import (
    ANNOTATION_NSID,
    COMMENT_NSID,
    CURATION_NSID,
    LEGACY_ANNOTATION_NSID,
    TRANSLATION_NSID,
    create_record,
)
from ..contributions_feed import _created_at_us
from ..curation import Judgment, SELF_ALLOWED_STATES
from ..state import AppState
from .annotations import read_raw_records
from .annotations_write import _now_iso, _require_bluesky, _require_user


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
    rating: int | None = None

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
        from bkk.annotations.harvest import juan_seq_from_marker_id

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
    state: Literal["proposed", "accepted", "rejected", "superseded"] | None = None
    rating: int | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "CurationStatePatch":
        if self.state is None and self.rating is None:
            raise ValueError("at least one of `state` or `rating` must be set")
        if self.rating is not None and self.rating not in (0, 1, 2):
            raise ValueError("rating must be 0, 1, or 2")
        return self


class CurationStateResponse(BaseModel):
    uri: str
    text_id: str
    juan_seq: int | None
    curation_state: str
    rating: int
    curation_uri: str


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
    from bkk.annotations.harvest import comment_archive_path, translation_archive_path

    if kind == "annotation" and juan_seq is not None:
        return juan_archive_path(annotations_root, text_id, juan_seq)
    if kind == "comment":
        comments_root = annotations_root.parent / "bkk-comments"
        return comment_archive_path(comments_root, text_id, juan_seq)
    if kind == "translation" and juan_seq is not None:
        translations_root = annotations_root.parent / "bkk-translations"
        return translation_archive_path(translations_root, text_id, juan_seq)
    return None


def _parse_uri(uri: str) -> tuple[str, str]:
    """Return ``(target_author_did, collection)`` parsed from an at-URI."""
    try:
        _, _, did, collection, _ = uri.split("/", 4)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Bad URI: {uri}") from exc
    if collection not in (ANNOTATION_NSID, LEGACY_ANNOTATION_NSID, COMMENT_NSID, TRANSLATION_NSID):
        raise HTTPException(status_code=400, detail=f"Unknown collection: {collection}")
    return did, collection


@router.patch(
    "/annotations/curation-state",
    response_model=CurationStateResponse,
    summary="Curate a contribution: set state and/or rating (editor only)",
)
def patch_curation_state(
    request: Request, body: CurationStatePatch,
) -> CurationStateResponse:
    state: AppState = request.app.state.bkk

    session_id, user = _require_user(request)
    if not user.is_editor:
        raise HTTPException(status_code=403, detail="Editor role required")

    feed = state.contributions
    if feed is None:
        raise HTTPException(status_code=503, detail="Contributions feed disabled")
    entry = feed.find(body.uri)
    if entry is None:
        raise HTTPException(status_code=404, detail="Contribution not in live buffer")

    text_id = entry.get("text_id")
    cid = entry.get("cid")
    if not isinstance(text_id, str) or not isinstance(cid, str):
        raise HTTPException(status_code=500, detail="Buffer entry missing text_id/cid")

    from bkk.annotations.harvest import juan_seq_from_marker_id

    marker_id = entry.get("marker_id")
    seq = juan_seq_from_marker_id(marker_id) if isinstance(marker_id, str) else None
    kind = entry.get("kind")
    if not isinstance(kind, str):
        raise HTTPException(status_code=500, detail="Buffer entry missing kind")

    target_author_did, _collection = _parse_uri(body.uri)

    # Require Bluesky only once we're sure the request is otherwise actionable.
    bluesky = _require_bluesky(user)

    admin_dids = set(state.config.annotation_admin_dids)
    if (
        body.state is not None
        and body.state not in SELF_ALLOWED_STATES
        and bluesky.did == target_author_did
        and bluesky.did not in admin_dids
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "Authors may only set their own records to "
                f"{sorted(SELF_ALLOWED_STATES)}"
            ),
        )

    current_state, current_rating = feed.resolver.get(body.uri)
    new_state = body.state if body.state is not None else current_state
    new_rating = body.rating if body.rating is not None else current_rating

    created_at = _now_iso()
    record = {
        "$type": CURATION_NSID,
        "target": {"uri": body.uri, "cid": cid},
        "state": new_state,
        "rating": new_rating,
        "createdAt": created_at,
    }
    result, new_tokens = create_record(
        service=bluesky.service_endpoint,
        access_jwt=bluesky.access_jwt,
        refresh_jwt=bluesky.refresh_jwt,
        repo=bluesky.did,
        collection=CURATION_NSID,
        record=record,
    )
    if new_tokens is not None:
        state.sessions.update_bluesky_tokens(
            session_id,
            access_jwt=new_tokens["access_jwt"],
            refresh_jwt=new_tokens["refresh_jwt"],
        )
    curation_uri = result.get("uri")
    curation_cid = result.get("cid")
    if not isinstance(curation_uri, str) or not isinstance(curation_cid, str):
        raise HTTPException(
            status_code=502, detail="atproto createRecord returned no uri/cid",
        )

    try:
        _, _, _did, _coll, rkey = curation_uri.split("/", 4)
    except ValueError as exc:
        raise HTTPException(
            status_code=502, detail=f"atproto returned malformed uri: {curation_uri}",
        ) from exc

    feed.resolver.apply(Judgment(
        did=bluesky.did,
        rkey=rkey,
        cid=curation_cid,
        target_uri=body.uri,
        target_cid=cid,
        state=new_state,
        rating=new_rating,
        created_at_us=_created_at_us(created_at),
    ))
    resolved_state, resolved_rating = feed.resolver.get(body.uri)

    archive_path = _archive_path_for(
        state, kind=kind, text_id=text_id, juan_seq=seq,
    )
    if archive_path is not None:
        records = (
            list(read_raw_records(archive_path)) if archive_path.exists() else []
        )
        hit_idx = next(
            (i for i, r in enumerate(records)
             if isinstance(r.get("provenance"), dict)
             and r["provenance"].get("cid") == cid),
            None,
        )
        if hit_idx is not None:
            records[hit_idx]["curation_state"] = resolved_state
            records[hit_idx]["rating"] = resolved_rating
            provenance = records[hit_idx].get("provenance")
            if isinstance(provenance, dict) and not provenance.get("uri"):
                provenance["uri"] = body.uri
            write_records_jsonl(archive_path, records, sort=(kind == "annotation"))
        else:
            # Record arrived via the live feed but hasn't been harvested to
            # disk yet. Materialize from the cached wire record so the
            # curation state is durable and surfaces to the CLI deleter.
            wire = entry.get("_wire")
            wire_collection = entry.get("_collection")
            if isinstance(wire, dict) and isinstance(wire_collection, str):
                from bkk.annotations.harvest import materialize_archive_record
                archive = materialize_archive_record(
                    wire=wire, collection=wire_collection,
                    did=target_author_did, cid=cid, uri=body.uri,
                    corpus_root=state.corpus_root,
                )
                if archive is not None:
                    archive["curation_state"] = resolved_state
                    archive["rating"] = resolved_rating
                    records.append(archive)
                    write_records_jsonl(
                        archive_path, records, sort=(kind == "annotation"),
                    )

    feed.set_curation(body.uri, resolved_state, resolved_rating)

    return CurationStateResponse(
        uri=body.uri,
        text_id=text_id,
        juan_seq=seq,
        curation_state=resolved_state,
        rating=resolved_rating,
        curation_uri=curation_uri,
    )


__all__ = ["router"]
