"""Corpus index query endpoint at ``/search``."""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Literal

from fastapi import APIRouter, Query, Request

from bkk.index.ir import Hit

from .. import _examples as ex
from .. import errors
from ..resolver import CorpusSnapshot
from ..schemas import HitOut, SearchResponse, VariantOverlayOut


Sort = Literal["match", "textid", "reverse_prematch", "date", "closeness"]


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


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


_YEAR_RE = re.compile(r"-?\d+")
_BCE_TOKENS = ("前", "BC", "B.C.", "BCE", "B.C.E.")


def _parse_period_year(period: str | None) -> float:
    """Parse a leading year from a free-form composition_period string.

    Returns ``+inf`` for missing/unparseable values so they sort to the end.
    Treats periods containing BCE markers (前, BC, BCE) as negative.
    """
    if not period:
        return float("inf")
    m = _YEAR_RE.search(period)
    if m is None:
        return float("inf")
    year = int(m.group(0))
    if year > 0 and any(tok in period for tok in _BCE_TOKENS):
        year = -year
    return float(year)


def _natural_key(h: Hit) -> tuple:
    return (h.textid, h.juan_seq, h.master_offset)


def _kwic_chars(h: Hit, q_chars: frozenset[str]) -> frozenset[str]:
    """Set of KWIC chars (left + right) excluding the query chars."""
    return frozenset(_nfc(h.left) + _nfc(h.right)) - q_chars


def _sort_closeness(hits: list[Hit], query: str) -> list[Hit]:
    """Greedy chain over pairwise KWIC character-overlap.

    Head = hit with maximum summed overlap to all others; chain extends by
    appending the unvisited hit with greatest overlap with the most recently
    appended hit. Outliers (low overall overlap) drift to the end.
    """
    if len(hits) <= 1:
        return list(hits)
    q_chars = frozenset(_nfc(query))
    sets = [_kwic_chars(h, q_chars) for h in hits]
    n = len(hits)
    totals = [sum(len(sets[i] & sets[j]) for j in range(n) if j != i) for i in range(n)]

    # head = max total overlap; tiebreak by natural key (min wins).
    head = min(range(n), key=lambda i: (-totals[i], _natural_key(hits[i])))
    visited = [False] * n
    visited[head] = True
    chain = [head]
    while len(chain) < n:
        cur = chain[-1]
        candidates = [i for i in range(n) if not visited[i]]
        nxt = min(
            candidates,
            key=lambda i: (-len(sets[cur] & sets[i]), -totals[i], _natural_key(hits[i])),
        )
        chain.append(nxt)
        visited[nxt] = True
    return [hits[i] for i in chain]


def _sort_hits(
    hits: Iterable[Hit],
    sort: Sort,
    query: str,
    snap: CorpusSnapshot | None,
) -> list[Hit]:
    items = list(hits)
    if sort == "textid":
        return sorted(items, key=_natural_key)
    if sort == "match":
        return sorted(
            items,
            key=lambda h: (_nfc(h.match) + _nfc(h.right), h.textid, h.juan_seq, h.master_offset),
        )
    if sort == "reverse_prematch":
        return sorted(
            items,
            key=lambda h: (_nfc(h.left)[::-1], _nfc(h.match), h.textid, h.juan_seq, h.master_offset),
        )
    if sort == "date":
        by_textid = snap.by_textid if snap is not None else {}

        def date_key(h: Hit) -> tuple:
            rec = by_textid.get(h.textid)
            year = _parse_period_year(rec.composition_period) if rec is not None else float("inf")
            return (year, h.textid, h.juan_seq, h.master_offset)

        return sorted(items, key=date_key)
    if sort == "closeness":
        return _sort_closeness(items, query)
    raise ValueError(f"unknown sort: {sort}")


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
    sort: Sort = Query(
        "match",
        description=(
            "result ordering: 'match' = forward from match position; "
            "'textid' = natural reading order; "
            "'reverse_prematch' = reversed left-context (classical reverse concordance); "
            "'date' = composition_period of bundle; "
            "'closeness' = greedy chain over pairwise KWIC character-overlap"
        ),
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

    snap = state.cache.get() if sort == "date" else None
    sorted_hits = _sort_hits(all_hits, sort, q, snap)

    page = sorted_hits[offset:offset + limit]
    return SearchResponse(
        query=q,
        total=len(sorted_hits),
        offset=offset,
        limit=limit,
        sort=sort,
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
