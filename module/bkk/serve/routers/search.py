"""Corpus index query endpoint at ``/search``."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Literal

from fastapi import APIRouter, Query, Request

from bkk.index.ir import Hit, IndexSummary

from .. import _examples as ex
from .. import errors
from .._hits import hit_out
from ..resolver import CorpusSnapshot
from ..schemas import (
    SearchDateFacets,
    SearchFacets,
    SearchFacetValue,
    SearchOverview,
    SearchResponse,
    SearchTextidsResponse,
    TrigramExtension,
)


Sort = Literal["match", "textid", "reverse_prematch", "date", "closeness"]


@dataclass(frozen=True)
class _CatalogMeta:
    textid: str
    title: str | None = None
    section_code: str | None = None
    section_title: str | None = None
    index_date: int | None = None


_SECTION_RE = re.compile(r"^(KR\d+[a-z]+)")


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
    meta: dict[str, _CatalogMeta],
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
        def date_key(h: Hit) -> tuple:
            year = meta.get(h.textid).index_date if h.textid in meta else None
            if year is None:
                return (float("inf"), h.textid, h.juan_seq, h.master_offset)
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
            "SELECT b.textid, b.title, b.section_code, s.title AS section_title, b.index_date "
            "FROM catalog_bundle b "
            "LEFT JOIN catalog_section s ON s.code = b.section_code"
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
            section_title=r["section_title"],
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
            section_title=None,
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
    textid_exclude: set[str],
    categories: set[str],
    category_exclude: set[str],
    category_descendants: bool = True,
    date_before: int | None = None,
    date_after: int | None = None,
    witness_exclude: set[str],
    voice_exclude: set[str],
    left_char: set[str],
    left_char_exclude: set[str],
    right_char: set[str],
    right_char_exclude: set[str],
    left_bigram: set[str],
    left_bigram_exclude: set[str],
    right_bigram: set[str],
    right_bigram_exclude: set[str],
    around_binom: set[str],
    around_binom_exclude: set[str],
) -> list[Hit]:
    out: list[Hit] = []
    for h in hits:
        if textids and h.textid not in textids:
            continue
        if textid_exclude and h.textid in textid_exclude:
            continue
        m = meta.get(h.textid)
        if not _category_matches(m.section_code if m else None, categories, category_descendants):
            continue
        if category_exclude and _category_matches(
            m.section_code if m else None,
            category_exclude,
            category_descendants,
        ):
            continue
        index_date = m.index_date if m else None
        if date_before is not None and (index_date is None or index_date >= date_before):
            continue
        if date_after is not None and (index_date is None or index_date <= date_after):
            continue
        if witness_exclude and h.matched_via in witness_exclude:
            continue
        if voice_exclude and h.voice in voice_exclude:
            continue
        if left_char and _hit_left_char(h) not in left_char:
            continue
        if left_char_exclude and _hit_left_char(h) in left_char_exclude:
            continue
        if right_char and _hit_right_char(h) not in right_char:
            continue
        if right_char_exclude and _hit_right_char(h) in right_char_exclude:
            continue
        if left_bigram and _hit_left_bigram(h) not in left_bigram:
            continue
        if left_bigram_exclude and _hit_left_bigram(h) in left_bigram_exclude:
            continue
        if right_bigram and _hit_right_bigram(h) not in right_bigram:
            continue
        if right_bigram_exclude and _hit_right_bigram(h) in right_bigram_exclude:
            continue
        if around_binom and _hit_around_binom(h) not in around_binom:
            continue
        if around_binom_exclude and _hit_around_binom(h) in around_binom_exclude:
            continue
        out.append(h)
    return out


def _unique_textids(hits: Iterable[Hit]) -> list[str]:
    return sorted({h.textid for h in hits})


def _textids_in_hit_order(hits: Iterable[Hit]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for h in hits:
        if h.textid in seen:
            continue
        seen.add(h.textid)
        out.append(h.textid)
    return out


def _textids_by_hit_count(hits: Iterable[Hit]) -> tuple[list[str], Counter[str]]:
    counts: Counter[str] = Counter(h.textid for h in hits)
    ids = sorted(counts, key=lambda textid: (-counts[textid], textid))
    return ids, counts


def _facet_values(
    counts: Counter[str],
    selected: set[str],
    *,
    excluded: set[str] | None = None,
    labels: dict[str, str | None] | None = None,
    limit: int = 12,
) -> list[SearchFacetValue]:
    excluded = excluded or set()
    labels = labels or {}
    values = sorted(
        counts.items(),
        key=lambda kv: (0 if kv[0] in selected or kv[0] in excluded else 1, -kv[1], kv[0]),
    )
    forced = selected | excluded
    if forced:
        forced_missing = [(v, 0) for v in sorted(forced - set(counts))]
        values = forced_missing + values
    return [
        SearchFacetValue(
            value=value,
            label=labels.get(value),
            count=count,
            selected=value in selected,
            excluded=value in excluded,
        )
        for value, count in values[:limit]
    ]


def _build_facets(
    hits: list[Hit],
    *,
    meta: dict[str, _CatalogMeta],
    selected_textid: str | None,
    excluded_textids: set[str],
    selected_categories: set[str],
    excluded_categories: set[str],
    selected_witnesses: set[str],
    excluded_witnesses: set[str],
    selected_voices: set[str],
    excluded_voices: set[str],
    selected_left_char: set[str],
    excluded_left_char: set[str],
    selected_right_char: set[str],
    excluded_right_char: set[str],
    selected_left_bigram: set[str],
    excluded_left_bigram: set[str],
    selected_right_bigram: set[str],
    excluded_right_bigram: set[str],
    selected_around_binom: set[str],
    excluded_around_binom: set[str],
    date_before: int | None,
    date_after: int | None,
    pivot_textid: str | None,
    facet_limit: int,
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
    category_labels = {
        m.section_code: m.section_title
        for m in meta.values()
        if m.section_code and m.section_title
    }
    dates = [
        m.index_date
        for h in hits
        if (m := meta.get(h.textid)) is not None and m.index_date is not None
    ]
    pivot_date = meta.get(pivot_textid).index_date if pivot_textid in meta else None
    return SearchFacets(
        textid=_facet_values(text_counts, {selected_textid} if selected_textid else set(), excluded=excluded_textids, labels=labels, limit=facet_limit),
        category=_facet_values(category_counts, selected_categories, excluded=excluded_categories, labels=category_labels, limit=facet_limit),
        witness=_facet_values(witness_counts, selected_witnesses, excluded=excluded_witnesses, limit=facet_limit),
        voice=_facet_values(voice_counts, selected_voices, excluded=excluded_voices, limit=facet_limit),
        left_char=_facet_values(left_char_counts, selected_left_char, excluded=excluded_left_char, limit=facet_limit),
        right_char=_facet_values(right_char_counts, selected_right_char, excluded=excluded_right_char, limit=facet_limit),
        left_bigram=_facet_values(left_bigram_counts, selected_left_bigram, excluded=excluded_left_bigram, limit=facet_limit),
        right_bigram=_facet_values(right_bigram_counts, selected_right_bigram, excluded=excluded_right_bigram, limit=facet_limit),
        around_binom=_facet_values(around_counts, selected_around_binom, excluded=excluded_around_binom, limit=facet_limit),
        date=SearchDateFacets(
            min=min(dates) if dates else None,
            max=max(dates) if dates else None,
            current_textid=pivot_textid,
            current_text_date=pivot_date,
            before_count=sum(1 for d in dates if date_before is not None and d < date_before),
            after_count=sum(1 for d in dates if date_after is not None and d > date_after),
        ),
    )


def _overview_response(
    *,
    q: str,
    sort: "Sort",
    offset: int,
    limit: int,
    facet_limit: int,
    summary: IndexSummary,
    meta: dict[str, _CatalogMeta],
    cap: int,
    selected_textid: str | None,
    excluded_textids: set[str],
    selected_categories: set[str],
    excluded_categories: set[str],
    selected_witnesses: set[str],
    excluded_witnesses: set[str],
    selected_voices: set[str],
    excluded_voices: set[str],
    date_before: int | None,
    date_after: int | None,
    pivot_textid: str | None,
    kwic_filters_ignored: bool,
) -> SearchResponse:
    """Assemble a SearchResponse in overview mode from an IndexSummary.

    No Hit objects are constructed. SQL-aggregable facets (textid,
    category, witness, date) are built from the summary's roll-ups; KWIC
    facets come back empty so the SPA's facet groups collapse.
    """
    text_counts = Counter(summary.by_textid)
    category_counts: Counter[str] = Counter()
    for tid, c in summary.by_textid.items():
        m = meta.get(tid)
        if m and m.section_code:
            category_counts[m.section_code] += c
    witness_counts = Counter(summary.by_witness_label)
    labels = {tid: m.title for tid, m in meta.items() if m.title}
    category_labels = {
        m.section_code: m.section_title
        for m in meta.values()
        if m.section_code and m.section_title
    }
    dates: list[int] = []
    for tid, c in summary.by_textid.items():
        m = meta.get(tid)
        if m and m.index_date is not None:
            dates.extend([m.index_date] * c)
    pivot_date = meta.get(pivot_textid).index_date if pivot_textid in meta else None
    facets = SearchFacets(
        textid=_facet_values(
            text_counts,
            {selected_textid} if selected_textid else set(),
            excluded=excluded_textids,
            labels=labels,
            limit=facet_limit,
        ),
        category=_facet_values(
            category_counts,
            selected_categories,
            excluded=excluded_categories,
            labels=category_labels,
            limit=facet_limit,
        ),
        witness=_facet_values(
            witness_counts,
            selected_witnesses,
            excluded=excluded_witnesses,
            limit=facet_limit,
        ),
        voice=[],
        left_char=[],
        right_char=[],
        left_bigram=[],
        right_bigram=[],
        around_binom=[],
        date=SearchDateFacets(
            min=min(dates) if dates else None,
            max=max(dates) if dates else None,
            current_textid=pivot_textid,
            current_text_date=pivot_date,
            before_count=sum(1 for d in dates if date_before is not None and d < date_before),
            after_count=sum(1 for d in dates if date_after is not None and d > date_after),
        ),
    )
    overview = SearchOverview(
        approximate=len(q) > 2,
        threshold=cap,
        trigram_left=[TrigramExtension(gram=g, count=c) for g, c in summary.trigram_left],
        trigram_right=[TrigramExtension(gram=g, count=c) for g, c in summary.trigram_right],
        kwic_filters_ignored=kwic_filters_ignored,
    )
    return SearchResponse(
        query=q,
        total=summary.total,
        offset=offset,
        limit=limit,
        sort=sort,
        facets=facets,
        hits=[],
        overview=overview,
    )


router = APIRouter(tags=["search"])


def _search_hits(
    request: Request,
    *,
    q: str,
    textid: str | None,
    textids: list[str] | None,
    textid_not: list[str] | None = None,
    witness: list[str] | None = None,
    witness_not: list[str] | None = None,
    voice: list[str] | None = None,
    voice_not: list[str] | None = None,
    category: list[str] | None = None,
    category_not: list[str] | None = None,
    category_descendants: bool,
    date_before: int | None,
    date_after: int | None,
    left_char: list[str] | None = None,
    left_char_not: list[str] | None = None,
    right_char: list[str] | None = None,
    right_char_not: list[str] | None = None,
    left_bigram: list[str] | None = None,
    left_bigram_not: list[str] | None = None,
    right_bigram: list[str] | None = None,
    right_bigram_not: list[str] | None = None,
    around_binom: list[str] | None = None,
    around_binom_not: list[str] | None = None,
    sort: Sort = "match",
    context: int = 20,
    master_only: bool = False,
    max_results: int | None = None,
) -> tuple[list[Hit], dict[str, _CatalogMeta], set[str] | None, set[str], set[str] | None, set[str], set[str], set[str], set[str], set[str], set[str], set[str], set[str], set[str], set[str], set[str], set[str], set[str], CorpusSnapshot | None, IndexSummary | None]:
    state = request.app.state.bkk
    ix = state.open_index()
    if ix is None:
        raise errors.index_unavailable(state._index_error or "index not built")
    cap = max_results if max_results is not None else state.config.max_search_hits

    witnesses = set(witness) if witness else None
    voices = set(voice) if voice else None
    scoped_textids = set(textids or [])
    if textid:
        scoped_textids = {textid} if not scoped_textids else scoped_textids & {textid}
    excluded_textids = _selected(textid_not)
    categories = _selected(category)
    excluded_categories = _selected(category_not)
    excluded_witnesses = _selected(witness_not)
    excluded_voices = _selected(voice_not)
    selected_left_char = _selected(left_char)
    excluded_left_char = _selected(left_char_not)
    selected_right_char = _selected(right_char)
    excluded_right_char = _selected(right_char_not)
    selected_left_bigram = _selected(left_bigram)
    excluded_left_bigram = _selected(left_bigram_not)
    selected_right_bigram = _selected(right_bigram)
    excluded_right_bigram = _selected(right_bigram_not)
    selected_around_binom = _selected(around_binom)
    excluded_around_binom = _selected(around_binom_not)
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
        candidates, _raw_total = ix.candidates_and_total(q)
        # Cap check uses the scope-aware summary so a tightly-scoped query
        # whose unscoped total is huge still falls back to the normal hit
        # path when the scoped subset fits under the limit.
        summary = ix.summarise(
            q,
            candidates=candidates,
            textids=scoped_textids or None,
            witnesses=witnesses,
            master_only=master_only,
        )
        if summary.total > cap:
            empty_hits: list[Hit] = []
            meta = _catalog_meta(request, None)
            return (
                empty_hits,
                meta,
                witnesses,
                excluded_witnesses,
                voices,
                excluded_voices,
                categories,
                excluded_categories,
                selected_left_char,
                excluded_left_char,
                selected_right_char,
                excluded_right_char,
                selected_left_bigram,
                excluded_left_bigram,
                selected_right_bigram,
                excluded_right_bigram,
                selected_around_binom,
                excluded_around_binom,
                scoped_textids,
                excluded_textids,
                None,
                summary,
            )
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
                    candidates=candidates,
                )
            ]
        else:
            all_hits = list(ix.search(
                q,
                context=context,
                witnesses=witnesses,
                textid=textid,
                voices=voices,
                candidates=candidates,
            ))
    finally:
        ix.close()

    if master_only:
        all_hits = [h for h in all_hits if h.matched_via == "master"]

    snap = None
    meta = _catalog_meta(request, None)
    filtered_hits = _apply_hit_filters(
        all_hits,
        meta=meta,
        textids=scoped_textids,
        textid_exclude=excluded_textids,
        categories=categories,
        category_exclude=excluded_categories,
        category_descendants=category_descendants,
        date_before=date_before,
        date_after=date_after,
        witness_exclude=excluded_witnesses,
        voice_exclude=excluded_voices,
        left_char=selected_left_char,
        left_char_exclude=excluded_left_char,
        right_char=selected_right_char,
        right_char_exclude=excluded_right_char,
        left_bigram=selected_left_bigram,
        left_bigram_exclude=excluded_left_bigram,
        right_bigram=selected_right_bigram,
        right_bigram_exclude=excluded_right_bigram,
        around_binom=selected_around_binom,
        around_binom_exclude=excluded_around_binom,
    )
    sorted_hits = _sort_hits(filtered_hits, sort, q, meta)
    return (
        sorted_hits,
        meta,
        witnesses,
        excluded_witnesses,
        voices,
        excluded_voices,
        categories,
        excluded_categories,
        selected_left_char,
        excluded_left_char,
        selected_right_char,
        excluded_right_char,
        selected_left_bigram,
        excluded_left_bigram,
        selected_right_bigram,
        excluded_right_bigram,
        selected_around_binom,
        excluded_around_binom,
        scoped_textids,
        excluded_textids,
        snap,
        None,
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
    textid_not: list[str] | None = Query(None),
    witness: list[str] | None = Query(
        None,
        description="restrict witness-side matches to these edition shorts (repeatable); "
                    "master matches are always returned",
        openapi_examples=ex.WITNESS_LIST,
    ),
    witness_not: list[str] | None = Query(None),
    voice: list[str] | None = Query(
        None,
        description="restrict to hits fully contained in a voice range of the "
                    "given name(s), e.g. 'root' or 'commentary' (repeatable). "
                    "Hits nested inside multiple ranges qualify under any of "
                    "their names. Omit to return all hits.",
        openapi_examples=ex.VOICE_LIST,
    ),
    voice_not: list[str] | None = Query(None),
    category: list[str] | None = Query(
        None,
        description="restrict to catalog/KR section codes (repeatable)",
    ),
    category_not: list[str] | None = Query(None),
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
    left_char_not: list[str] | None = Query(None),
    right_char: list[str] | None = Query(None),
    right_char_not: list[str] | None = Query(None),
    left_bigram: list[str] | None = Query(None),
    left_bigram_not: list[str] | None = Query(None),
    right_bigram: list[str] | None = Query(None),
    right_bigram_not: list[str] | None = Query(None),
    around_binom: list[str] | None = Query(None),
    around_binom_not: list[str] | None = Query(None),
    sort: Sort = Query(
        "match",
        description=(
            "result ordering: 'match' = forward from match position; "
            "'textid' = natural reading order; "
            "'reverse_prematch' = reversed left-context (classical reverse concordance); "
            "'date' = catalog index date of bundle; "
            "'closeness' = greedy chain over pairwise KWIC character-overlap"
        ),
    ),
    context: int = Query(20, ge=0, le=200, description="KWIC context window each side"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    facet_limit: int = Query(12, ge=1, le=200),
    master_only: bool = Query(
        False,
        description="when true, drop witness-side hits and count only master matches against the cap",
    ),
    max_results: int | None = Query(
        None,
        ge=1,
        le=200000,
        description="override the configured overview cap for this request; "
                    "defaults to the server's max_search_hits",
    ),
) -> SearchResponse:
    (
        sorted_hits,
        meta,
        witnesses,
        excluded_witnesses,
        voices,
        excluded_voices,
        categories,
        excluded_categories,
        selected_left_char,
        excluded_left_char,
        selected_right_char,
        excluded_right_char,
        selected_left_bigram,
        excluded_left_bigram,
        selected_right_bigram,
        excluded_right_bigram,
        selected_around_binom,
        excluded_around_binom,
        _scoped_textids,
        excluded_textids,
        snap,
        summary,
    ) = _search_hits(
        request,
        q=q,
        textid=textid,
        textids=textids,
        textid_not=textid_not,
        witness=witness,
        witness_not=witness_not,
        voice=voice,
        voice_not=voice_not,
        category=category,
        category_not=category_not,
        category_descendants=category_descendants,
        date_before=date_before,
        date_after=date_after,
        left_char=left_char,
        left_char_not=left_char_not,
        right_char=right_char,
        right_char_not=right_char_not,
        left_bigram=left_bigram,
        left_bigram_not=left_bigram_not,
        right_bigram=right_bigram,
        right_bigram_not=right_bigram_not,
        around_binom=around_binom,
        around_binom_not=around_binom_not,
        sort=sort,
        context=context,
        master_only=master_only,
        max_results=max_results,
    )
    if summary is not None:
        cap = max_results if max_results is not None else request.app.state.bkk.config.max_search_hits
        kwic_filters_ignored = bool(
            selected_left_char or excluded_left_char
            or selected_right_char or excluded_right_char
            or selected_left_bigram or excluded_left_bigram
            or selected_right_bigram or excluded_right_bigram
            or selected_around_binom or excluded_around_binom
        )
        return _overview_response(
            q=q,
            sort=sort,
            offset=offset,
            limit=limit,
            facet_limit=facet_limit,
            summary=summary,
            meta=meta,
            cap=cap,
            selected_textid=textid,
            excluded_textids=excluded_textids,
            selected_categories=categories,
            excluded_categories=excluded_categories,
            selected_witnesses=witnesses or set(),
            excluded_witnesses=excluded_witnesses,
            selected_voices=voices or set(),
            excluded_voices=excluded_voices,
            date_before=date_before,
            date_after=date_after,
            pivot_textid=pivot_textid,
            kwic_filters_ignored=kwic_filters_ignored,
        )
    facets = _build_facets(
        sorted_hits,
        meta=meta,
        selected_textid=textid,
        excluded_textids=excluded_textids,
        selected_categories=categories,
        excluded_categories=excluded_categories,
        selected_witnesses=witnesses or set(),
        excluded_witnesses=excluded_witnesses,
        selected_voices=voices or set(),
        excluded_voices=excluded_voices,
        selected_left_char=selected_left_char,
        excluded_left_char=excluded_left_char,
        selected_right_char=selected_right_char,
        excluded_right_char=excluded_right_char,
        selected_left_bigram=selected_left_bigram,
        excluded_left_bigram=excluded_left_bigram,
        selected_right_bigram=selected_right_bigram,
        excluded_right_bigram=excluded_right_bigram,
        selected_around_binom=selected_around_binom,
        excluded_around_binom=excluded_around_binom,
        date_before=date_before,
        date_after=date_after,
        pivot_textid=pivot_textid,
        facet_limit=facet_limit,
    )

    page = sorted_hits[offset:offset + limit]
    return SearchResponse(
        query=q,
        total=len(sorted_hits),
        offset=offset,
        limit=limit,
        sort=sort,
        facets=facets,
        hits=[hit_out(h.textid, h) for h in page],
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
    textid_not: list[str] | None = Query(None),
    witness: list[str] | None = Query(None, openapi_examples=ex.WITNESS_LIST),
    witness_not: list[str] | None = Query(None),
    voice: list[str] | None = Query(None, openapi_examples=ex.VOICE_LIST),
    voice_not: list[str] | None = Query(None),
    category: list[str] | None = Query(None),
    category_not: list[str] | None = Query(None),
    category_descendants: bool = Query(True),
    date_before: int | None = Query(None),
    date_after: int | None = Query(None),
    left_char: list[str] | None = Query(None),
    left_char_not: list[str] | None = Query(None),
    right_char: list[str] | None = Query(None),
    right_char_not: list[str] | None = Query(None),
    left_bigram: list[str] | None = Query(None),
    left_bigram_not: list[str] | None = Query(None),
    right_bigram: list[str] | None = Query(None),
    right_bigram_not: list[str] | None = Query(None),
    around_binom: list[str] | None = Query(None),
    around_binom_not: list[str] | None = Query(None),
    sort: Sort = Query("textid"),
    context: int = Query(20, ge=0, le=200),
) -> SearchTextidsResponse:
    sorted_hits, meta, *_ = _search_hits(
        request,
        q=q,
        textid=textid,
        textids=textids,
        textid_not=textid_not,
        witness=witness,
        witness_not=witness_not,
        voice=voice,
        voice_not=voice_not,
        category=category,
        category_not=category_not,
        category_descendants=category_descendants,
        date_before=date_before,
        date_after=date_after,
        left_char=left_char,
        left_char_not=left_char_not,
        right_char=right_char,
        right_char_not=right_char_not,
        left_bigram=left_bigram,
        left_bigram_not=left_bigram_not,
        right_bigram=right_bigram,
        right_bigram_not=right_bigram_not,
        around_binom=around_binom,
        around_binom_not=around_binom_not,
        sort=sort,
        context=context,
    )
    ids, counts = _textids_by_hit_count(sorted_hits)
    return SearchTextidsResponse(
        query=q,
        hit_count=len(sorted_hits),
        text_count=len(ids),
        textids=ids,
        entries=[
            {
                "textid": textid,
                "hit_count": counts[textid],
                "title": meta.get(textid).title if textid in meta else None,
            }
            for textid in ids
        ],
    )
