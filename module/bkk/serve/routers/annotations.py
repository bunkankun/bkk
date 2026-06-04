"""Annotation endpoints: per-juan list of offset-pinned annotations.

Annotations live in the ``bkk-annotations`` archive, separately from the
text bundles. The archive is configured via ``serve.annotations_root`` in
.bkkrc (or ``BKK_ANNOTATIONS_ROOT``). When unconfigured or empty, the
endpoint returns an empty list so the frontend has a single happy path.

On-disk shape: see ``docs/bkk-annotations/README.md``. One JSON object per
line, sorted by ``(bucket, bucket_offset, id)``.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from fastapi import APIRouter, Path as PathParam, Request
from pydantic import BaseModel

from .. import _examples as ex
from .. import errors
from ..state import AppState
from ..schemas import (
    AnnotationForm,
    AnnotationOut,
    AnnotationSense,
    AnnotationTranslation,
    MultipleChoicesResponse,
)
from .. import selection
from . import bundles as bundles_router
from . import texts as texts_router


router = APIRouter(tags=["annotations"])


def _ann_path(state: AppState, textid: str, seq: int) -> Path | None:
    """Return the bkk-annotations JSONL path for ``(textid, seq)`` if any."""
    root = state.annotations_root
    if root is None:
        return None
    rec = state.lookup_bundle(textid)
    if rec is None:
        raise errors.bundle_not_found(textid)
    candidate = root / textid / f"{textid}_{seq:03d}.ann.jsonl"
    return candidate if candidate.exists() else None


def _coerce_form(raw: Any) -> AnnotationForm | None:
    if not isinstance(raw, dict):
        return None
    form = AnnotationForm(
        orig=raw.get("orig"),
        orth=raw.get("orth"),
        pron=raw.get("pron"),
    )
    if form.orig is None and form.orth is None and form.pron is None:
        return None
    return form


def _coerce_sense(raw: Any) -> AnnotationSense | None:
    if not isinstance(raw, dict):
        return None
    sense = AnnotationSense(
        id=raw.get("id"),
        pos=raw.get("pos"),
        syn_func=raw.get("syn_func"),
        sem_feat=raw.get("sem_feat"),
        def_=raw.get("def"),
        usage=raw.get("usage") if isinstance(raw.get("usage"), dict) else None,
    )
    if all(
        v is None
        for v in (sense.id, sense.pos, sense.syn_func, sense.sem_feat, sense.def_, sense.usage)
    ):
        return None
    return sense


def _coerce_translation(raw: Any) -> AnnotationTranslation | None:
    if not isinstance(raw, dict):
        return None
    tr = AnnotationTranslation(
        text=raw.get("text"),
        title=raw.get("title"),
        src=raw.get("src"),
    )
    if tr.text is None and tr.title is None and tr.src is None:
        return None
    return tr


def _coerce_record(raw: dict[str, Any]) -> AnnotationOut | None:
    bucket_offset = raw.get("bucket_offset")
    if not isinstance(bucket_offset, int):
        return None
    anchor = raw.get("anchor") or {}
    payload = raw.get("payload") or {}
    marker_id = anchor.get("marker_id") if isinstance(anchor, dict) else None
    length = anchor.get("length") if isinstance(anchor, dict) else None
    return AnnotationOut(
        id=raw.get("id"),
        offset=bucket_offset,
        bucket=raw.get("bucket") if isinstance(raw.get("bucket"), str) else None,
        length=length if isinstance(length, int) else None,
        marker_id=marker_id if isinstance(marker_id, str) else None,
        concept=payload.get("concept"),
        concept_id=payload.get("concept_id"),
        form=_coerce_form(payload.get("form")),
        sense=_coerce_sense(payload.get("sense")),
        translation=_coerce_translation(payload.get("translation")),
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None,
    )


def read_raw_records(path: Path) -> list[dict[str, Any]]:
    """Return one dict per JSONL line, skipping blanks and decode errors.

    Shared by the harvester and the read endpoint so both see the same view.
    """
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict):
                out.append(raw)
    return out


def _load_annotations(path: Path) -> list[AnnotationOut]:
    out: list[AnnotationOut] = []
    for raw in read_raw_records(path):
        ann = _coerce_record(raw)
        if ann is not None:
            out.append(ann)
    out.sort(key=lambda a: a.offset)
    return out


class BySenseLocation(BaseModel):
    text_id: str
    seq: int
    text_title: str | None = None
    marker_id: str | None
    offset: int | None
    bucket: str | None
    length: int | None
    id: str | None
    concept: str | None = None
    concept_id: str | None = None
    orth: str | None
    pron: str | None
    sense_def: str | None = None
    note: str | None
    translation_title: str | None = None
    translation_text: str | None = None
    resp: str | None = None
    curation_state: str | None = None
    context_left: str | None = None
    context_match: str | None = None
    context_right: str | None = None


class BySenseResponse(BaseModel):
    sense_uuid: str
    total: int
    locations: list[BySenseLocation]


class BySenseCountsRequest(BaseModel):
    sense_uuids: list[str]


class BySenseCountsResponse(BaseModel):
    counts: dict[str, int]


def _sense_uuid_variants(sense_uuid: str) -> tuple[str, ...]:
    if sense_uuid.startswith("uuid-"):
        bare = sense_uuid[5:]
        return (sense_uuid, bare)
    return (sense_uuid, f"uuid-{sense_uuid}")


def _canonical_sense_uuid(sense_uuid: str) -> str:
    return sense_uuid[5:] if sense_uuid.startswith("uuid-") else sense_uuid


def _ann_root_locations(state: AppState, sense_uuid: str) -> list[BySenseLocation]:
    root = state.annotations_root
    if root is None or not root.is_dir():
        return []
    variants = set(_sense_uuid_variants(sense_uuid))
    out: list[BySenseLocation] = []
    for jsonl_path in sorted(root.glob("*/*.ann.jsonl")):
        text_id = jsonl_path.parent.name
        stem = jsonl_path.name.removesuffix(".ann.jsonl")
        try:
            seq = int(stem.rsplit("_", 1)[-1])
        except ValueError:
            continue
        for raw in read_raw_records(jsonl_path):
            payload = raw.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            sense = payload.get("sense")
            if (
                not isinstance(sense, dict)
                or not isinstance(sense.get("id"), str)
                or sense.get("id") not in variants
            ):
                continue
            anchor = raw.get("anchor") if isinstance(raw.get("anchor"), dict) else {}
            form = payload.get("form") if isinstance(payload.get("form"), dict) else {}
            metadata = (
                payload.get("metadata")
                if isinstance(payload.get("metadata"), dict)
                else {}
            )
            translation = (
                payload.get("translation")
                if isinstance(payload.get("translation"), dict)
                else {}
            )
            out.append(BySenseLocation(
                text_id=text_id,
                seq=seq,
                marker_id=anchor.get("marker_id") if isinstance(anchor.get("marker_id"), str) else None,
                offset=raw.get("bucket_offset") if isinstance(raw.get("bucket_offset"), int) else None,
                bucket=raw.get("bucket") if isinstance(raw.get("bucket"), str) else None,
                length=anchor.get("length") if isinstance(anchor.get("length"), int) else None,
                id=raw.get("id") if isinstance(raw.get("id"), str) else None,
                concept=payload.get("concept") if isinstance(payload.get("concept"), str) else None,
                concept_id=payload.get("concept_id") if isinstance(payload.get("concept_id"), str) else None,
                orth=form.get("orth") if isinstance(form.get("orth"), str) else None,
                pron=form.get("pron") if isinstance(form.get("pron"), str) else None,
                sense_def=sense.get("def") if isinstance(sense.get("def"), str) else None,
                note=metadata.get("note") if isinstance(metadata.get("note"), str) else payload.get("note") if isinstance(payload.get("note"), str) else None,
                translation_title=translation.get("title") if isinstance(translation.get("title"), str) else None,
                translation_text=translation.get("text") if isinstance(translation.get("text"), str) else None,
                resp=metadata.get("resp") if isinstance(metadata.get("resp"), str) else None,
                curation_state=raw.get("curation_state") if isinstance(raw.get("curation_state"), str) else None,
            ))
    out.sort(key=lambda loc: (loc.text_id, loc.seq, loc.offset if loc.offset is not None else -1, loc.id or ""))
    return out


def _ann_root_counts(state: AppState, sense_uuids: list[str]) -> dict[str, int]:
    root = state.annotations_root
    requested = {_canonical_sense_uuid(s) for s in sense_uuids}
    counts = {s: 0 for s in requested}
    if not requested or root is None or not root.is_dir():
        return counts
    for jsonl_path in sorted(root.glob("*/*.ann.jsonl")):
        for raw in read_raw_records(jsonl_path):
            if raw.get("curation_state") in {"rejected", "superseded"}:
                continue
            payload = raw.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            sense = payload.get("sense")
            if not isinstance(sense, dict) or not isinstance(sense.get("id"), str):
                continue
            key = _canonical_sense_uuid(sense["id"])
            if key in counts:
                counts[key] += 1
    return counts


def _ann_index_locations(state: AppState, sense_uuid: str) -> list[BySenseLocation] | None:
    conn = state.open_annotations_index()
    if conn is None:
        return None
    variants = _sense_uuid_variants(sense_uuid)
    placeholders = ",".join("?" * len(variants))
    try:
        rows = conn.execute(
            f"""
            SELECT text_id, juan_seq, marker_id, bucket_offset, bucket, length,
                   annotation_id, concept, concept_id, orth, pron, sense_def, note,
                   translation_title, translation_text, resp, curation_state
            FROM annotation_location
            WHERE sense_uuid IN ({placeholders})
            ORDER BY text_id, juan_seq, bucket_offset, annotation_id
            """,
            variants,
        ).fetchall()
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()
    return [
        BySenseLocation(
            text_id=str(row[0]),
            seq=int(row[1]),
            marker_id=row[2],
            offset=int(row[3]) if row[3] is not None else None,
            bucket=row[4],
            length=int(row[5]) if row[5] is not None else None,
            id=row[6],
            concept=row[7],
            concept_id=row[8],
            orth=row[9],
            pron=row[10],
            sense_def=row[11],
            note=row[12],
            translation_title=row[13],
            translation_text=row[14],
            resp=row[15],
            curation_state=row[16],
        )
        for row in rows
    ]


def _ann_index_counts(state: AppState, sense_uuids: list[str]) -> dict[str, int] | None:
    requested = sorted({_canonical_sense_uuid(s) for s in sense_uuids})
    counts = {s: 0 for s in requested}
    if not requested:
        return counts
    conn = state.open_annotations_index()
    if conn is None:
        return None
    variants: list[str] = []
    variant_to_key: dict[str, str] = {}
    for key in requested:
        for variant in _sense_uuid_variants(key):
            variants.append(variant)
            variant_to_key[variant] = key
    placeholders = ",".join("?" * len(variants))
    try:
        rows = conn.execute(
            f"""
            SELECT sense_uuid, COUNT(*)
            FROM annotation_location
            WHERE sense_uuid IN ({placeholders})
            GROUP BY sense_uuid
            """,
            variants,
        ).fetchall()
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()
    for sense_uuid, count in rows:
        key = variant_to_key.get(str(sense_uuid))
        if key is not None:
            counts[key] += int(count)
    return counts


def _enrich_text_context(state: AppState, locs: list[BySenseLocation]) -> list[BySenseLocation]:
    juan_cache: dict[tuple[str, int], tuple[str | None, dict[str, Any] | None]] = {}
    out: list[BySenseLocation] = []
    for loc in locs:
        title: str | None = None
        juan: dict[str, Any] | None = None
        key = (loc.text_id, loc.seq)
        if key in juan_cache:
            title, juan = juan_cache[key]
        else:
            try:
                rec = state.lookup_bundle(loc.text_id)
                if rec is not None:
                    metadata = rec.manifest.get("metadata") or {}
                    title = metadata.get("title") if isinstance(metadata.get("title"), str) else None
                    juan = selection.load_juan_file(
                        rec.bundle_dir, rec.manifest, rec.textid, loc.seq,
                    )
            except Exception:
                juan = None
            juan_cache[key] = (title, juan)

        left: str | None = None
        match: str | None = None
        right: str | None = None
        if (
            juan is not None
            and loc.bucket is not None
            and loc.offset is not None
        ):
            bucket = juan.get(loc.bucket)
            text = bucket.get("text") if isinstance(bucket, dict) else None
            if isinstance(text, str) and 0 <= loc.offset < len(text):
                start = loc.offset
                end = min(len(text), start + max(1, loc.length or 1))
                left = text[max(0, start - 7):start]
                match = text[start:end]
                right = text[end:min(len(text), end + 7)]

        out.append(
            loc.model_copy(update={
                "text_title": title,
                "context_left": left,
                "context_match": match,
                "context_right": right,
            })
        )
    return out


@router.post(
    "/annotations/by-senses/counts",
    response_model=BySenseCountsResponse,
    summary="Count annotation locations for multiple sense UUIDs",
)
def annotations_by_senses_counts(
    request: Request,
    body: BySenseCountsRequest,
) -> BySenseCountsResponse:
    state = request.app.state.bkk
    counts = _ann_index_counts(state, body.sense_uuids)
    if counts is None:
        counts = _ann_root_counts(state, body.sense_uuids)
    return BySenseCountsResponse(counts=counts)


@router.get(
    "/annotations/by-sense/{sense_uuid}",
    response_model=BySenseResponse,
    response_model_exclude_none=True,
    summary="List annotation locations whose payload.sense.id matches this sense",
)
def annotations_by_sense(
    request: Request,
    sense_uuid: str = PathParam(...),
) -> BySenseResponse:
    state = request.app.state.bkk
    locs = _ann_index_locations(state, sense_uuid)
    if locs is None:
        locs = _ann_root_locations(state, sense_uuid)
    locs = _enrich_text_context(state, locs)
    return BySenseResponse(sense_uuid=sense_uuid, total=len(locs), locations=locs)


@router.get(
    "/bundles/{textid}/juan/{seq}/annotations",
    response_model=list[AnnotationOut],
    response_model_exclude_none=True,
    summary="Annotations pinned to offsets within this juan (empty list if none)",
)
def get_juan_annotations(
    request: Request,
    textid: str = PathParam(..., openapi_examples=ex.TEXTID),
    seq: int = PathParam(..., ge=0, openapi_examples=ex.SEQ),
) -> list[AnnotationOut]:
    state = request.app.state.bkk
    path = _ann_path(state, textid, seq)
    if path is None:
        return []
    return _load_annotations(path)


@router.get(
    "/texts/{identifier}/juan/{seq}/annotations",
    response_model=list[AnnotationOut],
    response_model_exclude_none=True,
    responses={300: {"model": MultipleChoicesResponse}},
    summary="Annotations for a juan, by any identifier in metadata.identifiers",
)
def get_text_juan_annotations(
    request: Request,
    identifier: str = PathParam(..., openapi_examples=ex.IDENTIFIER),
    seq: int = PathParam(..., ge=0, openapi_examples=ex.SEQ),
):
    ref, multi = texts_router._resolve_or_respond(request, identifier)
    if multi is not None:
        return multi
    return get_juan_annotations(request, textid=ref.textid, seq=seq)


__all__ = ["router", "get_juan_annotations"]

# Silence "imported but unused" — bundles_router is imported for side-effect
# routing parity (kept to mirror the bundles/texts pair if we add aliases).
_ = bundles_router
