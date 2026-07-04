"""Catalog browsing under ``/catalog``.

The response body is itself a recipe — every match is a pin with role
``match``. This is the "results are recipes" pattern from bunkankun.md
("Catalog browsing"): the client can submit the same recipe back to
``/recipes:fulfil`` to materialize the matched bundles deterministically.
"""

from __future__ import annotations

import functools
import re
import sqlite3
from collections import Counter
from importlib.resources import files
from typing import Any

import yaml
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from bkk.serve import _examples as ex, errors
from bkk.index.catalog import MISSING_INDEX_DATE, normalize_search_text
from bkk.serve.catalog import FILTERS, CatalogService, _kr_categories
from bkk.serve.state import AppState
from bkk.serve.schemas import (
    CatalogMatchOut,
    CatalogResponse,
    RecipePin,
)
from .auth import SESSION_COOKIE


def _request_owner(request: Request) -> str | None:
    cookies = getattr(request, "cookies", {})
    session = request.app.state.bkk.sessions.get(cookies.get(SESSION_COOKIE))
    return session.login if session else None

router = APIRouter(prefix="/catalog", tags=["catalog"])


class CategoryNode(BaseModel):
    code: str
    label: str
    zh: str
    bundle_count: int
    subcategories: list["CategoryNode"] = Field(default_factory=list)


class CategoriesResponse(BaseModel):
    categories: list[CategoryNode]


class TimelineBucket(BaseModel):
    key: str
    label: str
    start: int
    end: int
    bundle_count: int


class TimelineResponse(BaseModel):
    buckets: list[TimelineBucket]


@functools.lru_cache(maxsize=1)
def _load_kr_categories() -> dict[str, dict[str, str]]:
    raw = yaml.safe_load(
        files("bkk.data").joinpath("kr_categories.yaml").read_text("utf-8")
    )
    return {k: v for k, v in raw.items() if k != "_provenance" and isinstance(v, dict)}


_TOP_RE = re.compile(r"^(KR\d+)([a-z]+)?$")


def _natural_key(code: str) -> tuple[int, str]:
    m = _TOP_RE.match(code)
    if not m:
        return (10**6, code)
    base = int(m.group(1)[2:])
    return (base, m.group(2) or "")


@router.get(
    "/categories",
    response_model=CategoriesResponse,
    summary="KR taxonomy with per-leaf bundle counts",
    description=(
        "Returns the Kanripo classification (top categories KR1–KR6 and their "
        "subcategories) with bilingual labels and the number of bundles in the "
        "current corpus tagged with each code. Top-level `bundle_count` sums "
        "all descendants. Use `/catalog?tags.kr-categories=<code>` to fetch "
        "the bundles for a given code."
    ),
)
def categories(request: Request) -> CategoriesResponse:
    state = request.app.state.bkk
    owner = _request_owner(request)
    counts, counts_are_descendant = _category_counts(state, owner)

    yaml_data = dict(_load_kr_categories())
    catalog_sections = _catalog_sections(state)
    if owner and state.user_text_records(owner):
        catalog_sections.setdefault(
            "KR9", {"label": "User Texts", "zh": "其他"},
        )
        catalog_sections.setdefault(
            "KR9a", {"label": "User Texts", "zh": "其他"},
        )
    for code, info in catalog_sections.items():
        yaml_data.setdefault(code, info)
    children_by_parent: dict[str | None, list[str]] = {}
    top_codes: list[str] = []
    all_codes = set(yaml_data)
    for code in yaml_data:
        m = _TOP_RE.match(code)
        if not m:
            continue
        if not m.group(2):
            top_codes.append(code)
        parent = _parent_category_code(code, all_codes)
        children_by_parent.setdefault(parent, []).append(code)

    def build_node(code: str) -> CategoryNode:
        info = yaml_data[code]
        child_codes = sorted(children_by_parent.get(code, []), key=_natural_key)
        children = [build_node(child) for child in child_codes]
        if counts_are_descendant:
            bundle_count = counts.get(code, sum(n.bundle_count for n in children))
        else:
            bundle_count = counts.get(code, 0) + sum(n.bundle_count for n in children)
        return CategoryNode(
            code=code,
            label=info.get("label", code),
            zh=info.get("zh", code),
            bundle_count=bundle_count,
            subcategories=children,
        )

    out = [build_node(top) for top in sorted(top_codes, key=_natural_key)]
    return CategoriesResponse(categories=out)


def _catalog_sections(state: AppState) -> dict[str, dict[str, str]]:
    conn = state.open_catalog()
    if conn is None:
        return {}
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT code, title, title_english, title_pinyin FROM catalog_section"
        ).fetchall()
    except sqlite3.DatabaseError:
        return {}
    finally:
        conn.close()

    out: dict[str, dict[str, str]] = {}
    for row in rows:
        title = row["title"] or row["code"]
        label = row["title_english"] or row["title_pinyin"] or title
        out[row["code"]] = {"label": label, "zh": title}
    return out


def _parent_category_code(code: str, codes: set[str]) -> str | None:
    candidates = [
        other
        for other in codes
        if other != code and len(other) < len(code) and code.startswith(other)
    ]
    if not candidates:
        return None
    return max(candidates, key=len)


def _parse_query(
    params: list[tuple[str, str]],
) -> tuple[dict[str, list[str]], str | None, str | None, int, int]:
    filters: dict[str, list[str]] = {}
    q: str | None = None
    century: str | None = None
    limit = 50
    offset = 0
    for key, raw in params:
        if key == "q":
            q = raw.strip() or None
            continue
        if key == "century":
            century = raw.strip() or None
            continue
        if key == "limit":
            try:
                limit = max(1, min(int(raw), 500))
            except (TypeError, ValueError):
                raise errors.bad_request("bad_limit", value=raw)
            continue
        if key == "offset":
            try:
                offset = max(0, int(raw))
            except (TypeError, ValueError):
                raise errors.bad_request("bad_offset", value=raw)
            continue
        filters.setdefault(key, []).append(raw)
    return filters, q, century, limit, offset


@router.get(
    "",
    response_model=CatalogResponse,
    summary="Browse the corpus with curated metadata filters; returns a recipe",
    description=(
        "Filter the corpus by curated metadata fields. Repeated keys are "
        "OR-combined within a field; multiple keys are AND-combined across "
        "fields. " + ex.CATALOG_HINT
    ),
)
def browse(request: Request) -> CatalogResponse:
    state = request.app.state.bkk
    service = CatalogService(state.cache)

    filters, q, century, limit, offset = _parse_query(
        list(request.query_params.multi_items())
    )
    bad = service.validate_keys(list(filters.keys()))
    if bad:
        raise errors.bad_request(
            "unknown_filter_keys",
            unknown=bad,
            allowed=service.whitelist(),
    )
    owner = _request_owner(request)
    private_records = state.user_text_records(owner) if owner else []
    private_wanted = {
        value.strip()
        for value in filters.get("tags.kr-categories", [])
        if value and value.strip()
    }
    if private_records and private_wanted and all(
        code.startswith("KR9") for code in private_wanted
    ):
        return _browse_visible_records(
            private_records,
            private_textids={rec.textid for rec in private_records},
            filters=filters,
            q=q,
            century=century,
            limit=limit,
            offset=offset,
            state=state,
            owner=owner,
        )

    indexed = _browse_catalog_index(
        state, filters, q=q, century=century, limit=limit, offset=offset
    )
    if indexed is not None:
        return indexed
    if q or century:
        raise errors.index_unavailable(
            "catalog index is required for catalog search and timeline browsing; "
            "rebuild _catalog.bkkc with `bkk index catalog`"
        )

    if q or century:
        snap = state.cache.get()
        records = snap.records
        for key, wanted_raw in filters.items():
            wanted = {w.strip() for w in wanted_raw if w and w.strip()}
            if wanted:
                records = [
                    rec for rec in records
                    if FILTERS[key](rec) & wanted
                ]
        records.sort(key=lambda r: r.textid)
    else:
        page = service.query(filters, limit=limit, offset=offset)
        records = [m.record for m in page.matches]
    if q:
        records = _filter_snapshot_query(records, q)
    if century:
        start, end = _century_range_from_key(century)
        records = [
            rec for rec in records
            if _snapshot_index_date(rec) is not None
            and start <= _snapshot_index_date(rec) <= end
        ]
    total = len(records) if q or century else page.total
    if q or century:
        records = records[offset:offset + limit]
        next_offset = offset + limit if offset + limit < total else None
    else:
        next_offset = page.next_offset

    matches: list[CatalogMatchOut] = []
    pins: list[RecipePin] = []
    for rec in records:
        echo: dict[str, Any] = {}
        for key, values in filters.items():
            if not values:
                continue
            present = sorted(FILTERS[key](rec))
            if present:
                echo[key] = present
        matches.append(
            CatalogMatchOut(
                textid=rec.textid,
                canonical_identifier=rec.canonical_identifier,
                title=rec.title,
                edition_short=rec.edition_short,
                base_edition=rec.base_edition,
                metadata=echo,
            )
        )
        pins.append(
            RecipePin(
                role="match",
                canonical_identifier=rec.canonical_identifier,
                textid=rec.textid,
                hash=rec.manifest_hash,
                metadata={
                    "title": rec.title,
                    "edition_short": rec.edition_short,
                    "base_edition": rec.base_edition,
                },
            )
        )

    return CatalogResponse(
        total=total,
        offset=offset,
        limit=limit,
        next_offset=next_offset,
        filters_applied=_filters_applied(filters, q=q, century=century),
        matches=matches,
        recipe={"pins": [p.model_dump(exclude_none=True) for p in pins]},
    )


@router.get(
    "/timeline",
    response_model=TimelineResponse,
    summary="Calendar-century catalog buckets",
)
def timeline(request: Request) -> TimelineResponse:
    state = request.app.state.bkk
    conn = state.open_catalog()
    if conn is None:
        raise errors.index_unavailable(
            "catalog index is required for timeline browsing; "
            "rebuild _catalog.bkkc with `bkk index catalog`"
        )
    try:
        rows = conn.execute(
            "SELECT index_date, COUNT(*) FROM catalog_bundle "
            "WHERE index_date != ? GROUP BY index_date",
            (MISSING_INDEX_DATE,),
        ).fetchall()
    except sqlite3.DatabaseError:
        raise errors.index_unavailable(
            "catalog index is unreadable; rebuild _catalog.bkkc with "
            "`bkk index catalog`"
        )
    finally:
        conn.close()
    return _timeline_from_year_counts(rows)


def _category_counts(
    state: AppState, owner: str | None = None,
) -> tuple[Counter[str], bool]:
    private = state.user_text_records(owner) if owner else []
    conn = state.open_catalog()
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT code, descendant_bundle_count FROM catalog_section"
            ).fetchall()
            counts = Counter({code: count for code, count in rows})
            for rec in private:
                code = _record_category(rec)
                if code:
                    counts[code] += 1
                    if code.startswith("KR9") and code != "KR9":
                        counts["KR9"] += 1
            return counts, True
        except sqlite3.DatabaseError:
            pass
        finally:
            conn.close()

    counts: Counter[str] = Counter()
    for rec in private:
        for code in _kr_categories(rec):
            counts[code] += 1
        if not _kr_categories(rec):
            code = _record_category(rec)
            if code:
                counts[code] += 1
    return counts, False


def _record_category(rec) -> str | None:
    categories = sorted(_kr_categories(rec), key=len, reverse=True)
    if categories:
        return categories[0]
    match = re.match(r"^(KR\d+[a-z]+)", rec.textid)
    return match.group(1) if match else None


def _record_filter_values(key: str, rec) -> set[str]:
    values = FILTERS[key](rec)
    if key == "tags.kr-categories" and not values:
        category = _record_category(rec)
        return {category} if category else set()
    return values


def _browse_visible_records(
    records,
    *,
    private_textids: set[str],
    filters: dict[str, list[str]],
    q: str | None,
    century: str | None,
    limit: int,
    offset: int,
    state: AppState,
    owner: str,
) -> CatalogResponse:
    visible = list(records)
    for key, wanted_raw in filters.items():
        wanted = {value.strip() for value in wanted_raw if value.strip()}
        if wanted:
            visible = [
                rec for rec in visible
                if _record_filter_values(key, rec) & wanted
            ]
    if q:
        visible = _filter_snapshot_query(visible, q)
    else:
        visible.sort(key=lambda rec: rec.textid)
    if century:
        start, end = _century_range_from_key(century)
        visible = [
            rec for rec in visible
            if (year := _snapshot_index_date(rec)) is not None
            and start <= year <= end
        ]
    total = len(visible)
    page = visible[offset:offset + limit]
    matches: list[CatalogMatchOut] = []
    pins: list[RecipePin] = []
    for rec in page:
        metadata: dict[str, Any] = {}
        for key in filters:
            present = sorted(_record_filter_values(key, rec))
            if present:
                metadata[key] = present
        if rec.textid in private_textids:
            status = state.user_text_status(owner, rec.textid)
            metadata.update({
                "source": "user",
                "index_status": status.get(
                    "index_status",
                    "ready"
                    if (rec.bundle_dir / f"{rec.textid}.bkkx").is_file()
                    else "pending",
                ),
                "sync_status": status.get("sync_status", "ready"),
                "repository_url": status.get("repository_url"),
            })
        matches.append(CatalogMatchOut(
            textid=rec.textid,
            canonical_identifier=rec.canonical_identifier,
            title=rec.title,
            edition_short=rec.edition_short,
            base_edition=rec.base_edition,
            metadata=metadata,
        ))
        pins.append(RecipePin(
            role="match",
            canonical_identifier=rec.canonical_identifier,
            textid=rec.textid,
            hash=rec.manifest_hash,
            metadata={"title": rec.title, "edition_short": rec.edition_short},
        ))
    return CatalogResponse(
        total=total,
        offset=offset,
        limit=limit,
        next_offset=offset + limit if offset + limit < total else None,
        filters_applied=_filters_applied(filters, q=q, century=century),
        matches=matches,
        recipe={"pins": [pin.model_dump(exclude_none=True) for pin in pins]},
    )


def _browse_catalog_index(
    state: AppState,
    filters: dict[str, list[str]],
    *,
    q: str | None = None,
    century: str | None = None,
    limit: int,
    offset: int,
) -> CatalogResponse | None:
    if any(key != "tags.kr-categories" for key in filters):
        return None
    wanted = {
        value.strip()
        for value in filters.get("tags.kr-categories", [])
        if value and value.strip()
    }

    conn = state.open_catalog()
    if conn is None:
        return None
    conn.row_factory = sqlite3.Row
    try:
        if (q or century) and not _catalog_index_has_bundle_search(conn, q=bool(q)):
            return None
        where_params: list[Any] = []
        rank_params: list[Any] = []
        where_clauses: list[str] = []
        if wanted:
            placeholders = ",".join("?" for _ in wanted)
            where_clauses.append(f"section_code IN ({placeholders})")
            where_params.extend(sorted(wanted))
        if century:
            start, end = _century_range_from_key(century)
            where_clauses.append("index_date BETWEEN ? AND ?")
            where_params.extend([start, end])
        rank_sql = "index_date"
        if q:
            query, query_norm = _catalog_query_terms(q)
            like = f"%{query}%"
            like_norm = f"%{query_norm}%"
            where_clauses.append(
                "("
                "lower(COALESCE(title, '')) LIKE ? OR "
                "COALESCE(title_pinyin_search, '') LIKE ? OR "
                "lower(COALESCE(title_english, '')) LIKE ? OR "
                "EXISTS ("
                "  SELECT 1 FROM catalog_identifier ci "
                "  WHERE ci.textid = catalog_bundle.textid "
                "  AND ci.value_search LIKE ?"
                ")"
                ")"
            )
            where_params.extend([like, like_norm, like, like_norm])
            rank_sql = (
                "CASE "
                "WHEN EXISTS ("
                "  SELECT 1 FROM catalog_identifier ci "
                "  WHERE ci.textid = catalog_bundle.textid "
                "  AND ci.value_search = ?"
                ") THEN 0 "
                "WHEN EXISTS ("
                "  SELECT 1 FROM catalog_identifier ci "
                "  WHERE ci.textid = catalog_bundle.textid "
                "  AND ci.value_search LIKE ?"
                ") THEN 1 "
                "WHEN EXISTS ("
                "  SELECT 1 FROM catalog_identifier ci "
                "  WHERE ci.textid = catalog_bundle.textid "
                "  AND ci.value_search LIKE ?"
                ") THEN 2 "
                "ELSE 3 END"
            )
            rank_params.extend([query_norm, f"{query_norm}%", like_norm])
        where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        total = conn.execute(
            f"SELECT COUNT(*) FROM catalog_bundle {where}", where_params
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM catalog_bundle "
            f"{where} "
            f"ORDER BY {rank_sql}, index_date, textid LIMIT ? OFFSET ?",
            [*where_params, *rank_params, limit, offset],
        ).fetchall()
        alt_ids_by_textid: dict[str, list[str]] = {}
        if rows:
            textids = [row["textid"] for row in rows]
            placeholders = ",".join("?" for _ in textids)
            for tid, value in conn.execute(
                "SELECT textid, value FROM catalog_identifier "
                f"WHERE kind = 'alt_id' AND textid IN ({placeholders}) "
                "ORDER BY textid, rowid",
                textids,
            ):
                alt_ids_by_textid.setdefault(tid, []).append(value)
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()

    matches: list[CatalogMatchOut] = []
    pins: list[RecipePin] = []
    for row in rows:
        title = row["title"]
        canonical_identifier = row["canonical_identifier"]
        manifest_hash = row["manifest_hash"]
        metadata = {
            "tags.kr-categories": [row["section_code"]],
            "section_code": row["section_code"],
            "title_pinyin": row["title_pinyin"],
            "title_english": row["title_english"],
            "not_before": row["not_before"],
            "not_after": row["not_after"],
            "dzt_date": row["dzt_date"],
            "index_date": row["index_date"],
            "index_date_source": row["index_date_source"],
            "alt_id": alt_ids_by_textid.get(row["textid"]) or None,
        }
        matches.append(
            CatalogMatchOut(
                textid=row["textid"],
                canonical_identifier=canonical_identifier,
                title=title,
                metadata={k: v for k, v in metadata.items() if v is not None},
            )
        )
        pins.append(
            RecipePin(
                role="match",
                canonical_identifier=canonical_identifier,
                textid=row["textid"],
                hash=manifest_hash,
                metadata={
                    "title": title,
                    "index_date": row["index_date"],
                },
            )
        )

    next_off = offset + limit if offset + limit < total else None
    return CatalogResponse(
        total=total,
        offset=offset,
        limit=limit,
        next_offset=next_off,
        filters_applied=_filters_applied(filters, q=q, century=century),
        matches=matches,
        recipe={"pins": [p.model_dump(exclude_none=True) for p in pins]},
    )


def _catalog_query_terms(q: str) -> tuple[str, str]:
    query = q.strip().lower()
    query_norm = normalize_search_text(query) or query
    return query, query_norm


def _catalog_index_has_bundle_search(
    conn: sqlite3.Connection, *, q: bool
) -> bool:
    bundle_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(catalog_bundle)").fetchall()
    }
    if "index_date" not in bundle_cols:
        return False
    if q and "title_pinyin_search" not in bundle_cols:
        return False
    if q:
        identifier_table = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'catalog_identifier'"
        ).fetchone()
        if identifier_table is None:
            return False
    return True


def _filters_applied(
    filters: dict[str, list[str]], *, q: str | None, century: str | None
) -> dict[str, list[str]]:
    out = dict(filters)
    if q:
        out["q"] = [q]
    if century:
        out["century"] = [century]
    return out


def _filter_snapshot_query(records, q: str):
    query, query_norm = _catalog_query_terms(q)

    def rank(rec) -> int | None:
        ids = [rec.textid]
        if rec.canonical_identifier:
            ids.append(rec.canonical_identifier)
        for value in rec.identifiers.values():
            if isinstance(value, list):
                ids.extend(str(v) for v in value if isinstance(v, (str, int)))
            elif isinstance(value, (str, int)):
                ids.append(str(value))
        id_terms = [normalize_search_text(v) or "" for v in ids]
        if query_norm in id_terms:
            return 0
        if any(v.startswith(query_norm) for v in id_terms):
            return 1
        if any(query_norm in v for v in id_terms):
            return 2
        title_terms = [
            rec.title or "",
            *(rec.alt_titles or []),
        ]
        if any(query in t.lower() for t in title_terms):
            return 3
        return None

    ranked = [(r, rec) for rec in records if (r := rank(rec)) is not None]
    ranked.sort(key=lambda item: (item[0], item[1].textid))
    return [rec for _, rec in ranked]


def _snapshot_index_date(rec) -> int | None:
    year = _leading_year(rec.composition_period)
    return year


def _leading_year(raw: Any) -> int | None:
    if not isinstance(raw, str):
        return None
    m = re.search(r"-?\d+", raw)
    return int(m.group(0)) if m else None


def _timeline_from_snapshot(state: AppState) -> TimelineResponse:
    counts: Counter[int] = Counter()
    for rec in state.cache.get().records:
        year = _snapshot_index_date(rec)
        if year is not None:
            counts[year] += 1
    return _timeline_from_year_counts(counts.items())


def _timeline_from_year_counts(rows) -> TimelineResponse:
    bucket_counts: Counter[str] = Counter()
    bucket_meta: dict[str, tuple[int, int, str]] = {}
    for year, count in rows:
        key, label, start, end = _century_bucket(int(year))
        bucket_counts[key] += int(count)
        bucket_meta[key] = (start, end, label)
    buckets = [
        TimelineBucket(
            key=key,
            label=bucket_meta[key][2],
            start=bucket_meta[key][0],
            end=bucket_meta[key][1],
            bundle_count=count,
        )
        for key, count in bucket_counts.items()
    ]
    buckets.sort(key=lambda b: (b.start, b.end))
    return TimelineResponse(buckets=buckets)


def _century_bucket(year: int) -> tuple[str, str, int, int]:
    if year <= 0:
        century = max(1, ((abs(year) - 1) // 100) + 1)
        start = -(century * 100)
        end = 0 if century == 1 else -((century - 1) * 100 + 1)
        return (
            f"bce-{century:02d}",
            f"{_ordinal(century)} c. BCE",
            start,
            end,
        )
    century = ((year - 1) // 100) + 1
    start = ((century - 1) * 100) + 1
    end = century * 100
    return f"ce-{century:02d}", f"{_ordinal(century)} c. CE", start, end


def _century_range_from_key(key: str) -> tuple[int, int]:
    m = re.fullmatch(r"(bce|ce)-(\d+)", key)
    if not m:
        raise errors.bad_request("bad_century", value=key)
    era, raw_century = m.groups()
    century = int(raw_century)
    if century < 1:
        raise errors.bad_request("bad_century", value=key)
    if era == "bce":
        return -(century * 100), 0 if century == 1 else -((century - 1) * 100 + 1)
    return ((century - 1) * 100) + 1, century * 100


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
