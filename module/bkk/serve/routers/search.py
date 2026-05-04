"""Corpus index query endpoint at ``/search``."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from .. import _examples as ex
from .. import errors
from ..schemas import HitOut, SearchResponse, VariantOverlayOut


def _hit_recipe(textid: str, hit) -> dict:
    """One-pin recipe pinning the hit's master span; re-submittable to /recipes:fulfil."""
    return {
        "pins": [
            {
                "role": "hit",
                "textid": textid,
                "selection": {
                    "juan": hit.juan_seq,
                    "bucket": hit.bucket,
                    "offset": hit.master_offset,
                    "length": hit.master_length,
                },
            }
        ]
    }

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResponse, summary="KWIC search across the corpus")
def search(
    request: Request,
    q: str = Query(
        ...,
        min_length=1,
        description="substring query (NFC-normalized server-side)",
        openapi_examples=ex.QUERY,
    ),
    textid: str | None = Query(
        None,
        description="restrict to one bundle's textid",
        openapi_examples=ex.TEXTID,
    ),
    witness: list[str] | None = Query(
        None,
        description="restrict witness-side matches to these edition shorts (repeatable); "
                    "master matches are always returned",
        openapi_examples=ex.WITNESS_LIST,
    ),
    context: int = Query(20, ge=0, le=200, description="KWIC context window each side"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> SearchResponse:
    state = request.app.state.bkk
    ix = state.open_index()
    if ix is None:
        raise errors.index_unavailable(state._index_error or "index not built")

    witnesses = set(witness) if witness else None
    try:
        all_hits = list(ix.search(q, context=context, witnesses=witnesses, textid=textid))
    finally:
        ix.close()

    page = all_hits[offset:offset + limit]
    return SearchResponse(
        query=q,
        total=len(all_hits),
        offset=offset,
        limit=limit,
        hits=[
            HitOut(
                textid=h.textid,
                juan_seq=h.juan_seq,
                bucket=h.bucket,
                master_offset=h.master_offset,
                master_length=h.master_length,
                matched_via=h.matched_via,
                matched_text=h.matched_text,
                left=h.left,
                match=h.match,
                right=h.right,
                overlays=[
                    VariantOverlayOut(
                        master_offset=o.master_offset,
                        length=o.length,
                        content=o.content,
                        witness=o.witness,
                        witness_form=o.witness_form,
                    )
                    for o in h.overlays
                ],
                toc_label=h.toc_label,
                recipe=_hit_recipe(h.textid, h),
            )
            for h in page
        ],
    )
