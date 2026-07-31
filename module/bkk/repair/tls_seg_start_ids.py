"""Repair duplicated TLS typed-segment run start marker IDs."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.marker_assets import (
    VALID_BUCKETS,
    load_marker_asset,
    marker_asset_entry_for_seq,
    marker_asset_hash,
)


def repair_tls_seg_start_ids(
    bundle_dir: Path | str,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Rename old duplicated ``tls:seg-start`` ids in a bundle.

    Older TLS imports assigned the synthetic ``tls:seg-start`` marker the same
    id as the first member ``tls:seg`` marker in the run. New imports assign
    ``{first_member_id}_start``. This repair applies that convention to
    existing inline and external marker lists.
    """
    bundle_dir = Path(bundle_dir).resolve()
    if not bundle_dir.is_dir():
        raise FileNotFoundError(f"not a directory: {bundle_dir}")
    text_id = bundle_dir.name

    scopes: list[tuple[Path, str | None, Path]] = [
        (bundle_dir, None, bundle_dir / f"{text_id}.manifest.yaml"),
    ]
    editions = bundle_dir / "editions"
    if editions.is_dir():
        for sub in sorted(editions.iterdir()):
            if sub.is_dir():
                scopes.append((sub, sub.name, sub / f"{text_id}-{sub.name}.manifest.yaml"))

    results = []
    for root, short, manifest_path in scopes:
        if manifest_path.exists():
            results.append(
                _repair_scope(root, short, manifest_path, dry_run=dry_run),
            )
    return {"bundle_dir": str(bundle_dir), "dry_run": dry_run, "scopes": results}


def _repair_scope(
    root: Path,
    edition_short: str | None,
    manifest_path: Path,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(manifest, dict):
        raise RuntimeError(f"{manifest_path.name}: manifest top level is not a mapping")

    renamed_total = 0
    juans_changed: dict[int, str] = {}
    marker_assets_changed: dict[int, tuple[str, str]] = {}
    lines: list[str] = []

    for part in (manifest.get("assets") or {}).get("parts") or []:
        if not isinstance(part, dict):
            continue
        seq = part.get("seq")
        filename = part.get("filename")
        if not isinstance(seq, int) or not isinstance(filename, str):
            continue

        juan_path = root / filename
        juan = yaml.safe_load(juan_path.read_text(encoding="utf-8")) or {}
        if not isinstance(juan, dict):
            continue

        renamed_by_place: dict[str, int] = {}
        juan_changed = False
        for bucket_name in VALID_BUCKETS:
            bucket = juan.get(bucket_name)
            if not isinstance(bucket, dict):
                continue
            markers = bucket.get("markers")
            if not isinstance(markers, list):
                continue
            fixed, renamed = _repair_marker_list(markers)
            if renamed:
                bucket["markers"] = [
                    marker_to_flow(marker) if isinstance(marker, dict) else marker
                    for marker in fixed
                ]
                renamed_by_place[f"{bucket_name} inline"] = renamed
                renamed_total += renamed
                juan_changed = True

        if juan_changed:
            juan["hash"] = _juan_self_hash(juan)
            juans_changed[seq] = juan["hash"]
            if not dry_run:
                juan_path.write_text(dump(juan), encoding="utf-8")

        entry = marker_asset_entry_for_seq(manifest, seq)
        marker_filename = (
            entry.get("filename")
            if isinstance(entry, dict) and isinstance(entry.get("filename"), str)
            else None
        )
        marker_asset = load_marker_asset(root, manifest, seq) if marker_filename else None
        if marker_asset is not None:
            marker_asset_changed = False
            markers_obj = marker_asset.get("markers")
            if isinstance(markers_obj, dict):
                for bucket_name in VALID_BUCKETS:
                    markers = markers_obj.get(bucket_name)
                    if not isinstance(markers, list):
                        continue
                    fixed, renamed = _repair_marker_list(markers)
                    if renamed:
                        markers_obj[bucket_name] = [
                            marker_to_flow(marker) if isinstance(marker, dict) else marker
                            for marker in fixed
                        ]
                        renamed_by_place[f"{bucket_name} asset"] = renamed
                        renamed_total += renamed
                        marker_asset_changed = True
            if marker_asset_changed and marker_filename is not None:
                marker_asset["hash"] = marker_asset_hash(marker_asset)
                marker_assets_changed[seq] = (marker_filename, marker_asset["hash"])
                if not dry_run:
                    (root / marker_filename).write_text(
                        dump(marker_asset), encoding="utf-8",
                    )

        if renamed_by_place:
            lines.append(
                f"juan {seq:03d}: renamed "
                + ", ".join(
                    f"{count} {place}" for place, count in renamed_by_place.items()
                )
            )

    if not dry_run and (juans_changed or marker_assets_changed):
        _patch_manifest(manifest, juans_changed, marker_assets_changed)
        manifest_path.write_text(dump(manifest), encoding="utf-8")

    return {
        "edition": edition_short or "bkk",
        "manifest": manifest_path.name,
        "renamed": renamed_total,
        "juans_changed": sorted(juans_changed),
        "marker_assets_changed": sorted(marker_assets_changed),
        "lines": lines,
    }


def _repair_marker_list(markers: list[Any]) -> tuple[list[Any], int]:
    typed = [dict(marker) if isinstance(marker, dict) else marker for marker in markers]
    occupied = {
        marker.get("id")
        for marker in typed
        if isinstance(marker, dict)
        and isinstance(marker.get("id"), str)
        and marker.get("id")
    }
    renamed = 0
    for marker in typed:
        if not isinstance(marker, dict):
            continue
        if marker.get("type") != "tls:seg-start":
            continue
        marker_id = marker.get("id")
        if not isinstance(marker_id, str) or not marker_id:
            continue
        member_ids = marker.get("member_ids")
        if not isinstance(member_ids, list) or not member_ids:
            continue
        first_member = member_ids[0]
        if marker_id != first_member or not isinstance(first_member, str):
            continue
        occupied.discard(marker_id)
        marker["id"] = _unique_marker_id(f"{first_member}_start", occupied)
        occupied.add(marker["id"])
        renamed += 1
    return typed, renamed


def _unique_marker_id(base: str, occupied: set[Any]) -> str:
    if base not in occupied:
        return base
    index = 2
    while f"{base}_dup{index}" in occupied:
        index += 1
    return f"{base}_dup{index}"


def _juan_self_hash(juan_dict: dict[str, Any]) -> str:
    data = copy.deepcopy(juan_dict)
    data["hash"] = ZERO_HASH
    return sha256_jcs(data)


def _patch_manifest(
    manifest: dict[str, Any],
    juans_changed: dict[int, str],
    marker_assets_changed: dict[int, tuple[str, str]],
) -> None:
    assets = manifest.setdefault("assets", {})
    parts_out = []
    for entry in assets.get("parts") or []:
        if not isinstance(entry, dict):
            parts_out.append(entry)
            continue
        seq = entry.get("seq")
        if isinstance(seq, int) and seq in juans_changed:
            entry = dict(entry)
            entry["hash"] = juans_changed[seq]
        parts_out.append(marker_to_flow(entry))
    assets["parts"] = parts_out

    markers_out = []
    for entry in assets.get("markers") or []:
        if not isinstance(entry, dict):
            markers_out.append(entry)
            continue
        seq = entry.get("seq")
        if isinstance(seq, int) and seq in marker_assets_changed:
            filename, hash_value = marker_assets_changed[seq]
            entry = dict(entry)
            entry["filename"] = filename
            entry["hash"] = hash_value
        markers_out.append(marker_to_flow(entry))
    if markers_out:
        assets["markers"] = markers_out
    manifest["hash"] = manifest_hash(manifest)
