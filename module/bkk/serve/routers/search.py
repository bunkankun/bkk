"""Corpus index query endpoint at ``/search``."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Literal

from fastapi import APIRouter, Query, Request

from bkk.index.ir import Hit

from .. import _examples as ex
from .. import errors
from ..resolver import CorpusSnapshot
from ..schemas import (
    HitOut,
    SearchDateFacets,
    SearchFacets,
    SearchFacetValue,
    SearchResponse,
    SearchTextidsResponse,
    VariantOverlayOut,
)


Sort = Literal["match", "textid", "reverse_prematch", "date", "closeness"]


@dataclass(frozen=True)
class _CatalogMeta:
    textid: str
    title: str | None = None
    section_code: str | None = None
    index_date: int | None = None


_SECTION_RE = re.compile(r"^(KR\d+[a-z]+)")


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


def _parse_period_year_int(period: str | None) -> int | None:
    year = _parse_period_year(period)
    if year == float("inf"):
        return None
    return int(year)


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


def _hit_left_char(h: Hit) -> str:
    return _nfc(h.left[-1:]) if h.left else ""


def _hit_right_char(h: Hit) -> str:
    return _nfc(h.right[:1]) if h.right else ""


def _hit_left_bigram(h: Hit) -> str:
    return _nfc(h.left[-2:]) if len(h.left) >= 2 else ""


def _hit_right_bigram(h: Hit) -> str:
    return _nfc(h.right[:2]) if len(h.right) >= 2 else ""


def _hit_around_binom(h: Hit) -> str:
    left = _hit_left_char(h)
    right = _hit_right_char(h)
    return left + right if left and right else ""


def _selected(values: list[str] | None) -> set[str]:
    return {v for v in (values or []) if v}


def _catalog_meta_from_index(request: Request) -> dict[str, _CatalogMeta]:
    state = request.app.state.bkk
    conn = state.open_catalog()
    if conn is None:
        return {}
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT textid, title, section_code, index_date FROM catalog_bundle"
        ).fetchall()
    except sqlite3.DatabaseError:
        return {}
    finally:
        conn.close()
    return {
        r["textid"]: _CatalogMeta(
            textid=r["textid"],
            title=r["title"],
            section_code=r["section_code"],
            index_date=r["index_date"],
        )
        for r in rows
    }


def _catalog_meta(request: Request, snap: CorpusSnapshot | None) -> dict[str, _CatalogMeta]:
    indexed = _catalog_meta_from_index(request)
    if indexed:
        return indexed
    if snap is None:
        snap = request.app.state.bkk.cache.get()
    out: dict[str, _CatalogMeta] = {}
    for rec in snap.records:
        m = _SECTION_RE.match(rec.textid)
        section = m.group(1) if m else None
        out[rec.textid] = _CatalogMeta(
            textid=rec.textid,
            title=rec.title,
            section_code=section,
            index_date=_parse_period_year_int(rec.composition_period),
        )
    return out


def _category_matches(section: str | None, categories: set[str], descendants: bool) -> bool:
    if not categories:
        return True
    if section is None:
        return False
    if descendants:
        return any(section.startswith(c) for c in categories)
    return section in categories


def _apply_hit_filters(
    hits: Iterable[Hit],
    *,
    meta: dict[str, _CatalogMeta],
    textids: set[str],
    categories: set[str],
    category_descendants: bool,
    date_before: int | None,
    date_after: int | None,
    left_char: set[str],
    right_char: set[str],
    left_bigram: set[str],
    right_bigram: set[str],
    around_binom: set[str],
) -> list[Hit]:
    out: list[Hit] = []
    for h in hits:
        if textids and h.textid not in textids:
            continue
        m = meta.get(h.textid)
        if not _category_matches(m.section_code if m else None, categories, category_descendants):
            continue
        index_date = m.index_date if m else None
        if date_before is not None and (index_date is None or index_date >= date_before):
            continue
        if date_after is not None and (index_date is None or index_date <= date_after):
            continue
        if left_char and _hit_left_char(h) not in left_char:
            continue
        if right_char and _hit_right_char(h) not in right_char:
            continue
        if left_bigram and _hit_left_bigram(h) not in left_bigram:
            continue
        if right_bigram and _hit_right_bigram(h) not in right_bigram:
            continue
        if around_binom and _hit_around_binom(h) not in around_binom:
            continue
        out.append(h)
    return out


def _unique_textids(hits: Iterable[Hit]) -> list[str]:
    return sorted({h.textid for h in hits})


def _facet_values(
    counts: Counter[str],
    selected: set[str],
    *,
    labels: dict[str, str | None] | None = None,
    limit: int = 12,
) -> list[SearchFacetValue]:
    labels = labels or {}
    values = sorted(
        counts.items(),
        key=lambda kv: (0 if kv[0] in selected else 1, -kv[1], kv[0]),
    )
    if selected:
        selected_missing = [(v, 0) for v in sorted(selected - set(counts))]
        values = selected_missing + values
    return [
        SearchFacetValue(
            value=value,
            label=labels.get(value),
            count=count,
            selected=value in selected,
        )
        for value, count in values[:limit]
    ]


def _build_facets(
    hits: list[Hit],
    *,
    meta: dict[str, _CatalogMeta],
    selected_textid: str | None,
    selected_categories: set[str],
    selected_witnesses: set[str],
    selected_voices: set[str],
    selected_left_char: set[str],
    selected_right_char: set[str],
    selected_left_bigram: set[str],
    selected_right_bigram: set[str],
    selected_around_binom: set[str],
    date_before: int | None,
    date_after: int | None,
    pivot_textid: str | None,
) -> SearchFacets:
    text_counts: Counter[str] = Counter(h.textid for h in hits)
    category_counts: Counter[str] = Counter(
        m.section_code
        for h in hits
        if (m := meta.get(h.textid)) is not None and m.section_code
    )
    witness_counts: Counter[str] = Counter(h.matched_via for h in hits)
    voice_counts: Counter[str] = Counter(
        h.voice for h in hits if h.voice and h.voice != "none"
    )
    left_char_counts: Counter[str] = Counter(v for h in hits if (v := _hit_left_char(h)))
    right_char_counts: Counter[str] = Counter(v for h in hits if (v := _hit_right_char(h)))
    left_bigram_counts: Counter[str] = Counter(v for h in hits if (v := _hit_left_bigram(h)))
    right_bigram_counts: Counter[str] = Counter(v for h in hits if (v := _hit_right_bigram(h)))
    around_counts: Counter[str] = Counter(v for h in hits if (v := _hit_around_binom(h)))
    labels = {
        textid: m.title
        for textid, m in meta.items()
        if m.title
    }
    dates = [
        m.index_date
        for h in hits
        if (m := meta.get(h.textid)) is not None and m.index_date is not None
    ]
    pivot_date = meta.get(pivot_textid).index_date if pivot_textid in meta else None
    return SearchFacets(
        textid=_facet_values(text_counts, {selected_textid} if selected_textid else set(), labels=labels),
        category=_facet_values(category_counts, selected_categories),
        witness=_facet_values(witness_counts, selected_witnesses),
        voice=_facet_values(voice_counts, selected_voices),
        left_char=_facet_values(left_char_counts, selected_left_char),
        right_char=_facet_values(right_char_counts, selected_right_char),
        left_bigram=_facet_values(left_bigram_counts, selected_left_bigram),
        right_bigram=_facet_values(right_bigram_counts, selected_right_bigram),
        around_binom=_facet_values(around_counts, selected_around_binom),
        date=SearchDateFacets(
            min=min(dates) if dates else None,
            max=max(dates) if dates else None,
            current_textid=pivot_textid,
            current_text_date=pivot_date,
            before_count=sum(1 for d in dates if date_before is not None and d < date_before),
            after_count=sum(1 for d in dates if date_after is not None and d > date_after),
        ),
    )


router = APIRouter(tags=["search"])


def _search_hits(
    request: Request,
    *,
    q: str,
    textid: str | None,
    textids: list[str] | None,
    witness: list[str] | None,
    voice: list[str] | None,
    category: list[str] | None,
    category_descendants: bool,
    date_before: int | None,
    date_after: int | None,
    left_char: list[str] | None,
    right_char: list[str] | None,
    left_bigram: list[str] | None,
    right_bigram: list[str] | None,
    around_binom: list[str] | None,
    sort: Sort,
    context: int,
) -> tuple[list[Hit], dict[str, _CatalogMeta], set[str] | None, set[str] | None, set[str], set[str], set[str], set[str], set[str], set[str], set[str], CorpusSnapshot | None]:
    state = request.app.state.bkk
    ix = state.open_index()
    if ix is None:
        raise errors.index_unavailable(state._index_error or "index not built")

    witnesses = set(witness) if witness else None
    voices = set(voice) if voice else None
    scoped_textids = set(textids or [])
    if textid:
        scoped_textids = {textid} if not scoped_textids else scoped_textids & {textid}
    categories = _selected(category)
    selected_left_char = _selected(left_char)
    selected_right_char = _selected(right_char)
    selected_left_bigram = _selected(left_bigram)
    selected_right_bigram = _selected(right_bigram)
    selected_around_binom = _selected(around_binom)
    if voices is not None:
        available = set(ix.available_voices())
        unknown = voices - available
        if unknown:
            ix.close()
            raise errors.bad_request(
                "unknown_voice",
                unknown=sorted(unknown),
                available=sorted(available),
            )
    try:
        if scoped_textids:
            all_hits = [
                hit
                for tid in sorted(scoped_textids)
                for hit in ix.search(
                    q,
                    context=context,
                    witnesses=witnesses,
                    textid=tid,
                    voices=voices,
                )
            ]
        else:
            all_hits = list(ix.search(
                q,
                context=context,
                witnesses=witnesses,
                textid=textid,
                voices=voices,
            ))
    finally:
        ix.close()

    snap = state.cache.get() if sort == "date" else None
    meta = _catalog_meta(request, snap)
    filtered_hits = _apply_hit_filters(
        all_hits,
        meta=meta,
        textids=scoped_textids,
        categories=categories,
        category_descendants=category_descendants,
        date_before=date_before,
        date_after=date_after,
        left_char=selected_left_char,
        right_char=selected_right_char,
        left_bigram=selected_left_bigram,
        right_bigram=selected_right_bigram,
        around_binom=selected_around_binom,
    )
    sorted_hits = _sort_hits(filtered_hits, sort, q, snap)
    return (
        sorted_hits,
        meta,
        witnesses,
        voices,
        categories,
        selected_left_char,
        selected_right_char,
        selected_left_bigram,
        selected_right_bigram,
        selected_around_binom,
        scoped_textids,
        snap,
    )


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
    textids: list[str] | None = Query(
        None,
        description="restrict to these bundle textids (repeatable)",
    ),
    witness: list[str] | None = Query(
        None,
        description="restrict witness-side matches to these edition shorts (repeatable); "
                    "master matches are always returned",
        openapi_examples=ex.WITNESS_LIST,
    ),
    voice: list[str] | None = Query(
        None,
        description="restrict to hits fully contained in a voice range of the "
                    "given name(s), e.g. 'root' or 'commentary' (repeatable). "
                    "Hits nested inside multiple ranges qualify under any of "
                    "their names. Omit to return all hits.",
        openapi_examples=ex.VOICE_LIST,
    ),
    category: list[str] | None = Query(
        None,
        description="restrict to catalog/KR section codes (repeatable)",
    ),
    category_descendants: bool = Query(
        True,
        description="when true, a category filter includes descendant section codes",
    ),
    date_before: int | None = Query(
        None,
        description="restrict to catalog index_date values strictly before this year",
    ),
    date_after: int | None = Query(
        None,
        description="restrict to catalog index_date values strictly after this year",
    ),
    pivot_textid: str | None = Query(
        None,
        description="open/current textid used to expose a date-pivot hint in facets",
    ),
    left_char: list[str] | None = Query(None),
    right_char: list[str] | None = Query(None),
    left_bigram: list[str] | None = Query(None),
    right_bigram: list[str] | None = Query(None),
    around_binom: list[str] | None = Query(None),
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
    (
        sorted_hits,
        meta,
        witnesses,
        voices,
        categories,
        selected_left_char,
        selected_right_char,
        selected_left_bigram,
        selected_right_bigram,
        selected_around_binom,
        _scoped_textids,
        snap,
    ) = _search_hits(
        request,
        q=q,
        textid=textid,
        textids=textids,
        witness=witness,
        voice=voice,
        category=category,
        category_descendants=category_descendants,
        date_before=date_before,
        date_after=date_after,
        left_char=left_char,
        right_char=right_char,
        left_bigram=left_bigram,
        right_bigram=right_bigram,
        around_binom=around_binom,
        sort=sort,
        context=context,
    )
    facets = _build_facets(
        sorted_hits,
        meta=meta,
        selected_textid=textid,
        selected_categories=categories,
        selected_witnesses=witnesses or set(),
        selected_voices=voices or set(),
        selected_left_char=selected_left_char,
        selected_right_char=selected_right_char,
        selected_left_bigram=selected_left_bigram,
        selected_right_bigram=selected_right_bigram,
        selected_around_binom=selected_around_binom,
        date_before=date_before,
        date_after=date_after,
        pivot_textid=pivot_textid,
    )

    page = sorted_hits[offset:offset + limit]
    return SearchResponse(
        query=q,
        total=len(sorted_hits),
        offset=offset,
        limit=limit,
        sort=sort,
        facets=facets,
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
                witness_left=h.witness_left,
                witness_right=h.witness_right,
                witness_left_variant_offset=h.witness_left_variant_offset,
                witness_right_variant_end=h.witness_right_variant_end,
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
                voice=h.voice,
                voice_stack=list(h.voice_stack),
                recipe=_hit_recipe(h.textid, h),
            )
            for h in page
        ],
    )


@router.get(
    "/search/textids",
    response_model=SearchTextidsResponse,
    summary="Unique textids matching a KWIC search",
)
def search_textids(
    request: Request,
    q: str = Query(..., min_length=1, openapi_examples=ex.QUERY),
    textid: str | None = Query(None, openapi_examples=ex.TEXTID),
    textids: list[str] | None = Query(None),
    witness: list[str] | None = Query(None, openapi_examples=ex.WITNESS_LIST),
    voice: list[str] | None = Query(None, openapi_examples=ex.VOICE_LIST),
    category: list[str] | None = Query(None),
    category_descendants: bool = Query(True),
    date_before: int | None = Query(None),
    date_after: int | None = Query(None),
    left_char: list[str] | None = Query(None),
    right_char: list[str] | None = Query(None),
    left_bigram: list[str] | None = Query(None),
    right_bigram: list[str] | None = Query(None),
    around_binom: list[str] | None = Query(None),
    sort: Sort = Query("textid"),
    context: int = Query(20, ge=0, le=200),
) -> SearchTextidsResponse:
    sorted_hits, *_ = _search_hits(
        request,
        q=q,
        textid=textid,
        textids=textids,
        witness=witness,
        voice=voice,
        category=category,
        category_descendants=category_descendants,
        date_before=date_before,
        date_after=date_after,
        left_char=left_char,
        right_char=right_char,
        left_bigram=left_bigram,
        right_bigram=right_bigram,
        around_binom=around_binom,
        sort=sort,
        context=context,
    )
    ids = _unique_textids(sorted_hits)
    return SearchTextidsResponse(
        query=q,
        hit_count=len(sorted_hits),
        text_count=len(ids),
        textids=ids,
    )
