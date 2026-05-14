"""Annotation endpoints: per-juan list of offset-pinned annotations.

Annotations live in a sibling ``*.ann.yaml`` file next to each juan YAML.
The file is optional — when missing, the endpoint returns an empty list so
the frontend has a single happy path. The on-disk shape is TLS-derived and
includes ``concept``, ``form``, ``sense.def``, ``translation``, and an
``offset`` into the master body text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
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
    """Return the first existing ``*_{seq:03d}.ann.yaml`` for the bundle."""
    rec = state.cache.lookup(textid)
    if rec is None:
        raise errors.bundle_not_found(textid)
    bundle = rec.bundle_dir
    seq_str = f"{seq:03d}"
    direct = bundle / f"{textid}_{seq_str}.ann.yaml"
    if direct.exists():
        return direct
    matches = sorted(bundle.glob(f"*_{seq_str}.ann.yaml"))
    return matches[0] if matches else None


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


def _coerce(raw: dict[str, Any]) -> AnnotationOut | None:
    offset = raw.get("offset")
    if not isinstance(offset, int):
        return None
    return AnnotationOut(
        id=raw.get("id"),
        offset=offset,
        length=raw.get("length") if isinstance(raw.get("length"), int) else None,
        concept=raw.get("concept"),
        concept_id=raw.get("concept_id"),
        seg_id=raw.get("seg_id"),
        pos=raw.get("pos") if isinstance(raw.get("pos"), int) else None,
        form=_coerce_form(raw.get("form")),
        sense=_coerce_sense(raw.get("sense")),
        translation=_coerce_translation(raw.get("translation")),
        metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else None,
    )


def _load_annotations(path: Path) -> list[AnnotationOut]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_list = doc.get("annotations") or []
    out: list[AnnotationOut] = []
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        ann = _coerce(raw)
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
