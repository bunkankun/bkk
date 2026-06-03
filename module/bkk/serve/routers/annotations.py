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
from typing import Any

from fastapi import APIRouter, Path as PathParam, Request

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


def _load_annotations(path: Path) -> list[AnnotationOut]:
    out: list[AnnotationOut] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            ann = _coerce_record(raw)
            if ann is not None:
                out.append(ann)
    out.sort(key=lambda a: a.offset)
    return out


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
