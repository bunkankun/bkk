"""Catalog browsing under ``/catalog``.

The response body is itself a recipe — every match is a pin with role
``match``. This is the "results are recipes" pattern from bunkankun.md
("Catalog browsing"): the client can submit the same recipe back to
``/recipes:fulfil`` to materialize the matched bundles deterministically.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from bkk.serve import _examples as ex, errors
from bkk.serve.catalog import FILTERS, CatalogService
from bkk.serve.schemas import (
    CatalogMatchOut,
    CatalogResponse,
    RecipePin,
)

router = APIRouter(prefix="/catalog", tags=["catalog"])


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
