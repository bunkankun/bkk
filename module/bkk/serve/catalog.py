"""Catalog browsing: filter the corpus snapshot, page, return a recipe.

Per the design spec (bunkankun.md, "Catalog browsing"), a catalog response is
itself a recipe: each match is a pin with role ``match``, carrying the
canonical_identifier and the manifest hash so the client can re-fetch the
exact same set of bundles deterministically.

The filter surface is a curated whitelist (validated with the user). Unknown
filter keys are an error rather than silently ignored, so callers learn the
shape of the API instead of relying on accidental matches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .resolver import BundleRecord, CorpusCache


# Whitelist of filter keys -> extractor returning the *set of normalized values*
# present on a bundle for that key. A request matches a key if the requested
# value is in the bundle's set; multiple requested values for the same key are
# OR-combined (set intersection non-empty); multiple keys are AND-combined.
def _str_or_none(v: Any) -> set[str]:
    return {str(v).strip()} if isinstance(v, (str, int)) and str(v).strip() else set()


def _list_of_str(v: Any) -> set[str]:
    if not isinstance(v, list):
        return set()
    return {str(x).strip() for x in v if isinstance(x, (str, int)) and str(x).strip()}


def _identifier(rec: BundleRecord, key: str) -> set[str]:
    v = rec.identifiers.get(key)
    if isinstance(v, list):
        return _list_of_str(v)
    return _str_or_none(v)


def _kr_categories(rec: BundleRecord) -> set[str]:
    v = rec.tags.get("kr-categories")
    return _list_of_str(v) if isinstance(v, list) else _str_or_none(v)


def _author_names(rec: BundleRecord) -> set[str]:
    out: set[str] = set()
    for a in rec.authors:
        name = a.get("name")
        if isinstance(name, str) and name.strip():
            out.add(name.strip())
    return out


def _source(rec: BundleRecord) -> set[str]:
    s = rec.source
    if isinstance(s, str):
        return _str_or_none(s)
    if isinstance(s, dict):
        out: set[str] = set()
        for key in ("name", "id", "short"):
            out |= _str_or_none(s.get(key))
        return out
    return set()


FILTERS: dict[str, Callable[[BundleRecord], set[str]]] = {
    "title": lambda r: _str_or_none(r.title),
    "alt_titles": lambda r: set(r.alt_titles),
    "edition.short": lambda r: _str_or_none(r.edition_short),
    "base_edition": lambda r: _str_or_none(r.base_edition),
    "tags.kr-categories": _kr_categories,
    "authors.name": _author_names,
    "composition_period": lambda r: _str_or_none(r.composition_period),
    "source": _source,
    "metadata.identifiers.krp": lambda r: _identifier(r, "krp"),
    "metadata.identifiers.cbeta": lambda r: _identifier(r, "cbeta"),
    "metadata.identifiers.slug": lambda r: _identifier(r, "slug"),
}


@dataclass(frozen=True)
class CatalogMatch:
    record: BundleRecord


@dataclass(frozen=True)
class CatalogPage:
    matches: list[CatalogMatch]
    total: int
    next_offset: int | None


class CatalogService:
    """Filter + page over the shared :class:`CorpusCache` snapshot."""

    def __init__(self, cache: CorpusCache):
        self._cache = cache

    @staticmethod
    def whitelist() -> list[str]:
        return list(FILTERS.keys())

    @staticmethod
    def validate_keys(keys: list[str]) -> list[str]:
        """Return any keys that are NOT in the whitelist."""
        return [k for k in keys if k not in FILTERS]

    def query(
        self,
        filters: dict[str, list[str]],
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> CatalogPage:
        snap = self._cache.get()
        records = snap.records
        for key, wanted_raw in filters.items():
            wanted = {w.strip() for w in wanted_raw if w and w.strip()}
            if not wanted:
                continue
            extractor = FILTERS[key]
            records = [r for r in records if extractor(r) & wanted]
        records.sort(key=lambda r: r.textid)
        total = len(records)
        page = records[offset:offset + limit]
        next_off = offset + limit if offset + limit < total else None
        return CatalogPage(
            matches=[CatalogMatch(record=r) for r in page],
            total=total,
            next_offset=next_off,
        )
