"""UUID normalization for bkk-core references.

Canonical UUIDs throughout bkk (YAML files, SQLite index, API responses)
are bare (no prefix). The ``uuid-`` form survives only as TEI/XML source
notation and must be stripped at the boundary.
"""

from __future__ import annotations


def strip_uuid_prefix(value: str) -> str:
    """Remove a leading ``uuid-`` prefix; return ``value`` unchanged otherwise."""
    return value[5:] if value.startswith("uuid-") else value
