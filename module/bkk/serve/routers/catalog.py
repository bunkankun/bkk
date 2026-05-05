"""Catalog browsing under ``/catalog``.

The response body is itself a recipe — every match is a pin with role
``match``. This is the "results are recipes" pattern from bunkankun.md
("Catalog browsing"): the client can submit the same recipe back to
``/recipes:fulfil`` to materialize the matched bundles deterministically.
"""

from __future__ import annotations

import functools
import re
from collections import Counter
from importlib.resources import files
from typing import Any

import yaml
from fastapi import APIRouter, Request
from pydantic import BaseModel

from bkk.serve import _examples as ex, errors
from bkk.serve.catalog import FILTERS, CatalogService, _kr_categories
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


class TopCategory(CategoryNode):
    subcategories: list[CategoryNode]


class CategoriesResponse(BaseModel):
    categories: list[TopCategory]


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
    snap = state.cache.get()

    counts: Counter[str] = Counter()
    for rec in snap.records:
        for code in _kr_categories(rec):
            counts[code] += 1

    yaml_data = _load_kr_categories()
    grouped: dict[str, list[str]] = {}
    top_codes: list[str] = []
    for code in yaml_data:
        m = _TOP_RE.match(code)
        if not m:
            continue
        top, suffix = m.group(1), m.group(2)
        if not suffix:
            top_codes.append(code)
            grouped.setdefault(top, [])
        else:
            grouped.setdefault(top, []).append(code)

    out: list[TopCategory] = []
    for top in sorted(top_codes, key=_natural_key):
        info = yaml_data[top]
        sub_codes = sorted(grouped.get(top, []), key=_natural_key)
        sub_list = [
            CategoryNode(
                code=sub,
                label=yaml_data[sub].get("label", sub),
                zh=yaml_data[sub].get("zh", sub),
                bundle_count=counts.get(sub, 0),
            )
            for sub in sub_codes
        ]
        descendant_count = counts.get(top, 0) + sum(n.bundle_count for n in sub_list)
        out.append(
            TopCategory(
                code=top,
                label=info.get("label", top),
                zh=info.get("zh", top),
                bundle_count=descendant_count,
                subcategories=sub_list,
            )
        )
    return CategoriesResponse(categories=out)


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
