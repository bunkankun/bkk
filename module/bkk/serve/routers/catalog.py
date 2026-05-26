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
from bkk.serve.catalog import FILTERS, CatalogService, _kr_categories
from bkk.serve.state import AppState
from bkk.serve.schemas import (
    CatalogMatchOut,
    CatalogResponse,
    RecipePin,
)

router = APIRouter(prefix="/catalog", tags=["catalog"])


class CategoryNode(BaseModel):
    code: str
    label: str
    zh: str
    bundle_count: int
    subcategories: list["CategoryNode"] = Field(default_factory=list)


class CategoriesResponse(BaseModel):
    categories: list[CategoryNode]


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
    counts, counts_are_descendant = _category_counts(state)

    yaml_data = _load_kr_categories()
    catalog_sections = _catalog_sections(state)
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
) -> tuple[dict[str, list[str]], int, int]:
    filters: dict[str, list[str]] = {}
    limit = 50
    offset = 0
    for key, raw in params:
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
    return filters, limit, offset


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

    filters, limit, offset = _parse_query(list(request.query_params.multi_items()))
    bad = service.validate_keys(list(filters.keys()))
    if bad:
        raise errors.bad_request(
            "unknown_filter_keys",
            unknown=bad,
            allowed=service.whitelist(),
        )

    indexed = _browse_catalog_index(state, filters, limit=limit, offset=offset)
    if indexed is not None:
        return indexed

    page = service.query(filters, limit=limit, offset=offset)

    matches: list[CatalogMatchOut] = []
    pins: list[RecipePin] = []
    for m in page.matches:
        rec = m.record
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
        total=page.total,
        offset=offset,
        limit=limit,
        next_offset=page.next_offset,
        filters_applied=filters,
        matches=matches,
        recipe={"pins": [p.model_dump(exclude_none=True) for p in pins]},
    )


def _category_counts(state: AppState) -> tuple[Counter[str], bool]:
    conn = state.open_catalog()
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT code, descendant_bundle_count FROM catalog_section"
            ).fetchall()
            return Counter({code: count for code, count in rows}), True
        except sqlite3.DatabaseError:
            pass
        finally:
            conn.close()

    snap = state.cache.get()
    counts: Counter[str] = Counter()
    for rec in snap.records:
        for code in _kr_categories(rec):
            counts[code] += 1
    return counts, False


def _browse_catalog_index(
    state: AppState,
    filters: dict[str, list[str]],
    *,
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
        params: list[Any] = []
        where = ""
        if wanted:
            placeholders = ",".join("?" for _ in wanted)
            where = f"WHERE section_code IN ({placeholders})"
            params.extend(sorted(wanted))

        total = conn.execute(
            f"SELECT COUNT(*) FROM catalog_bundle {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM catalog_bundle "
            f"{where} "
            "ORDER BY index_date, textid LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
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
        filters_applied=filters,
        matches=matches,
        recipe={"pins": [p.model_dump(exclude_none=True) for p in pins]},
    )
