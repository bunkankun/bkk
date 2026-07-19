"""Variant-aware KWIC search over BKK bundles.

Reads canonical ``<textid>_NNN.yaml`` files from a bundle directory and
produces a portable SQLite artifact (``<textid>.bkkx``) that powers the
:class:`Index` query API. Substring queries are matched against the
established master text *and* against per-witness derived texts, so a
character that appears only as a witness reading still finds the master
position; KWIC results display the master window and overlay any variant
readings that intersect it.
"""

from .build import build_index
from .annotations import build_annotation_index
from .catalog import build_catalog_index
from .ir import Hit, VariantOverlay
from .merge import merge_bundles
from .parallel import (
    ParallelCluster,
    ParallelLocation,
    discover_parallel_passages,
    write_parallel_report,
)
from .parallel_fuzzy_from_scan import discover_fuzzy_from_scan
from .parallel_lookup import (
    ParallelLookup,
    ParallelLookupBuildStats,
    ParallelLookupStaleError,
    build_parallel_lookup,
    default_parallel_lookup_path,
)
from .parallel_scan import ParallelScanStats, discover_parallel_passages_scan
from .query import Index
from .translation import build_translation_index, merge_translations

__all__ = [
    "Index",
    "Hit",
    "VariantOverlay",
    "ParallelCluster",
    "ParallelLocation",
    "ParallelLookup",
    "ParallelLookupBuildStats",
    "ParallelLookupStaleError",
    "ParallelScanStats",
    "build_index",
    "build_annotation_index",
    "build_catalog_index",
    "build_translation_index",
    "merge_bundles",
    "merge_translations",
    "discover_parallel_passages",
    "discover_parallel_passages_scan",
    "discover_fuzzy_from_scan",
    "build_parallel_lookup",
    "default_parallel_lookup_path",
    "write_parallel_report",
]
