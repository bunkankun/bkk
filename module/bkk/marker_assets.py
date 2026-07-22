"""Helpers for BKK external marker asset files.

Bundles may keep marker lists inline in juan bucket objects, or move bulky
markers into per-juan assets declared by ``manifest.assets.markers``. This
module provides the shared compatibility layer: callers ask for effective
bucket markers and get inline + external markers in one list.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from bkk.importer.hashing import ZERO_HASH, sha256_jcs
from bkk.importer.write.yaml_writer import marker_to_flow

_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

VALID_BUCKETS = ("front", "body", "back")

STRUCTURAL_MARKER_TYPES = {
    "head",
    "tls:head",
    "tls:div-start",
    "tls:div-end",
    "cbeta:juan-start",
    "cbeta:juan-end",
    "cbeta:mulu",
}


def marker_asset_filename(text_id: str, seq: int, edition_short: str | None) -> str:
    stem = f"{text_id}_{seq:03d}"
    if edition_short:
        stem = f"{stem}-{edition_short}"
    return f"assets/{stem}.markers.yaml"


def marker_asset_canonical_identifier(
    text_id: str, seq: int, edition_short: str | None,
) -> str:
    slug = edition_short if edition_short is not None else "bkk"
    return f"bkk:krp/{text_id}/{slug}/v1/markers/{seq}"


def marker_asset_hash(asset: dict[str, Any]) -> str:
    data = copy.deepcopy(asset)
    data["hash"] = ZERO_HASH
    return sha256_jcs(data)


def build_marker_asset(
    text_id: str,
    seq: int,
    edition_short: str | None,
    markers_by_bucket: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    markers = {
        bucket: [
            marker_to_flow(dict(m))
            for m in markers_by_bucket.get(bucket, [])
            if isinstance(m, dict)
        ]
        for bucket in VALID_BUCKETS
    }
    asset: dict[str, Any] = {
        "canonical_identifier": marker_asset_canonical_identifier(
            text_id, seq, edition_short,
        ),
        "seq": seq,
        "markers": markers,
        "hash": ZERO_HASH,
    }
    asset["hash"] = marker_asset_hash(asset)
    return asset


def split_inline_external_markers(
    markers: list[dict[str, Any]],
    *,
    keep_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split markers into physical-juan inline markers and marker assets."""
    keep_ids = keep_ids or set()
    inline: list[dict[str, Any]] = []
    external: list[dict[str, Any]] = []
    for marker in markers:
        if not isinstance(marker, dict):
            continue
        mid = marker.get("id")
        if (
            marker.get("type") in STRUCTURAL_MARKER_TYPES
            or (isinstance(mid, str) and mid in keep_ids)
        ):
            inline.append(marker)
        else:
            external.append(marker)
    return inline, external


def marker_asset_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    assets = manifest.get("assets") or {}
    entries = assets.get("markers") or []
    return [e for e in entries if isinstance(e, dict)]


def marker_asset_entry_for_seq(
    manifest: dict[str, Any], seq: int,
) -> dict[str, Any] | None:
    for entry in marker_asset_entries(manifest):
        if entry.get("seq") == seq:
            return entry
    return None


def load_marker_asset(
    manifest_dir: Path,
    manifest: dict[str, Any],
    seq: int,
) -> dict[str, Any] | None:
    entry = marker_asset_entry_for_seq(manifest, seq)
    if entry is None:
        return None
    filename = entry.get("filename")
    if not isinstance(filename, str):
        return None
    path = manifest_dir / filename
    if not path.exists():
        return None
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=_YAML_LOADER) or {}
    return data if isinstance(data, dict) else None


def external_markers_for_bucket(
    marker_asset: dict[str, Any] | None,
    bucket_name: str,
) -> list[dict[str, Any]]:
    if marker_asset is None:
        return []
    markers_obj = marker_asset.get("markers") or {}
    if not isinstance(markers_obj, dict):
        return []
    markers = markers_obj.get(bucket_name) or []
    if not isinstance(markers, list):
        return []
    return [m for m in markers if isinstance(m, dict)]


def inline_markers_for_bucket(
    juan: dict[str, Any],
    bucket_name: str,
) -> list[dict[str, Any]]:
    bucket = juan.get(bucket_name)
    if not isinstance(bucket, dict):
        return []
    markers = bucket.get("markers") or []
    if not isinstance(markers, list):
        return []
    return [m for m in markers if isinstance(m, dict)]


def effective_markers_for_bucket(
    juan: dict[str, Any],
    bucket_name: str,
    marker_asset: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return inline + external markers for one bucket.

    Sorting preserves each source's order for same-offset markers, with
    external markers preceding inline markers at the same offset. That matches
    the usual writer order where extracted layout markers precede structural
    markers such as ``tls:head`` at a shared offset.
    """
    combined: list[tuple[int, int, dict[str, Any]]] = []
    for i, marker in enumerate(inline_markers_for_bucket(juan, bucket_name)):
        combined.append((1, i, marker))
    base = len(combined)
    for i, marker in enumerate(external_markers_for_bucket(marker_asset, bucket_name)):
        combined.append((0, base + i, marker))
    combined.sort(key=lambda p: (p[2].get("offset", 0), p[0], p[1]))
    return [dict(marker) for _, _, marker in combined]


def hydrate_juan_markers(
    juan: dict[str, Any],
    marker_asset: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a copy of ``juan`` whose buckets contain effective markers."""
    out = copy.deepcopy(juan)
    for bucket_name in VALID_BUCKETS:
        bucket = out.get(bucket_name)
        if not isinstance(bucket, dict):
            continue
        markers = effective_markers_for_bucket(juan, bucket_name, marker_asset)
        if markers:
            bucket["markers"] = [marker_to_flow(m) for m in markers]
        else:
            bucket.pop("markers", None)
    return out


def toc_marker_ids(manifest: dict[str, Any], seq: int | None = None) -> set[str]:
    out: set[str] = set()
    for entry in manifest.get("table_of_contents") or []:
        if not isinstance(entry, dict):
            continue
        ref = entry.get("ref")
        if not isinstance(ref, dict):
            continue
        if seq is not None and ref.get("seq") != seq:
            continue
        mid = ref.get("marker_id")
        if isinstance(mid, str) and mid:
            out.add(mid)
    return out
