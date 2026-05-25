"""Migrate inline juan markers into per-juan marker assets."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.marker_assets import (
    VALID_BUCKETS,
    build_marker_asset,
    effective_markers_for_bucket,
    load_marker_asset,
    marker_asset_filename,
    split_inline_external_markers,
    toc_marker_ids,
)


def externalize_markers(bundle_dir: Path, *, dry_run: bool = False) -> dict[str, Any]:
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
                _externalize_scope(
                    text_id, root, short, manifest_path, dry_run=dry_run,
                )
            )
    return {"bundle_dir": str(bundle_dir), "dry_run": dry_run, "scopes": results}


def _externalize_scope(
    text_id: str,
    root: Path,
    edition_short: str | None,
    manifest_path: Path,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(manifest, dict):
        raise RuntimeError(f"{manifest_path.name}: manifest top level is not a mapping")

    keep_ids_by_seq: dict[int, set[str]] = {}
    for part in (manifest.get("assets") or {}).get("parts") or []:
        if isinstance(part, dict) and isinstance(part.get("seq"), int):
            keep_ids_by_seq[part["seq"]] = toc_marker_ids(manifest, part["seq"])

    moved_total = 0
    kept_total = 0
    juan_updates: dict[int, str] = {}
    marker_entries: list[tuple[int, str, str]] = []
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
        old_asset = load_marker_asset(root, manifest, seq)
        keep_ids = keep_ids_by_seq.get(seq, set())

        external_by_bucket: dict[str, list[dict[str, Any]]] = {}
        kept_by_bucket: dict[str, int] = {}
        moved_by_bucket: dict[str, int] = {}
        changed = False

        for bucket_name in VALID_BUCKETS:
            bucket = juan.get(bucket_name)
            if not isinstance(bucket, dict):
                continue
            effective = effective_markers_for_bucket(juan, bucket_name, old_asset)
            inline, external = split_inline_external_markers(
                effective, keep_ids=keep_ids,
            )
            external_by_bucket[bucket_name] = external
            kept_by_bucket[bucket_name] = len(inline)
            moved_by_bucket[bucket_name] = len(external)
            if inline:
                new_inline = [marker_to_flow(m) for m in inline]
                if bucket.get("markers") != new_inline:
                    bucket["markers"] = new_inline
                    changed = True
            elif "markers" in bucket:
                bucket.pop("markers", None)
                changed = True

        marker_hash: str | None = None
        marker_filename: str | None = None
        if any(external_by_bucket.values()):
            marker_asset = build_marker_asset(
                text_id, seq, edition_short, external_by_bucket,
            )
            marker_filename = marker_asset_filename(text_id, seq, edition_short)
            marker_hash = marker_asset["hash"]
            marker_entries.append((seq, marker_filename, marker_hash))
            old_hash = (old_asset or {}).get("hash")
            if old_hash != marker_hash:
                changed = True
            if not dry_run:
                (root / "assets").mkdir(parents=True, exist_ok=True)
                (root / marker_filename).write_text(
                    dump(marker_asset), encoding="utf-8",
                )

        if changed:
            juan_hash = _juan_self_hash(juan)
            juan["hash"] = juan_hash
            juan_updates[seq] = juan_hash
            if not dry_run:
                juan_path.write_text(dump(juan), encoding="utf-8")

        moved = sum(moved_by_bucket.values())
        kept = sum(kept_by_bucket.values())
        moved_total += moved
        kept_total += kept
        lines.append(
            f"juan {seq:03d}: moved {moved}, kept {kept}"
            + (f", asset {marker_filename}" if marker_filename else "")
        )

    if not dry_run:
        _patch_manifest(manifest, marker_entries, juan_updates)
        manifest_path.write_text(dump(manifest), encoding="utf-8")

    return {
        "edition": edition_short or "bkk",
        "manifest": manifest_path.name,
        "moved": moved_total,
        "kept": kept_total,
        "juans_changed": sorted(juan_updates),
        "lines": lines,
    }


def _juan_self_hash(juan_dict: dict[str, Any]) -> str:
    data = copy.deepcopy(juan_dict)
    data["hash"] = ZERO_HASH
    return sha256_jcs(data)


def _patch_manifest(
    manifest: dict[str, Any],
    marker_entries: list[tuple[int, str, str]],
    juan_updates: dict[int, str],
) -> None:
    assets = manifest.setdefault("assets", {})
    parts_out = []
    for entry in assets.get("parts") or []:
        if not isinstance(entry, dict):
            parts_out.append(entry)
            continue
        seq = entry.get("seq")
        if isinstance(seq, int) and seq in juan_updates:
            entry = dict(entry)
            entry["hash"] = juan_updates[seq]
        parts_out.append(marker_to_flow(entry))
    assets["parts"] = parts_out
    if marker_entries:
        assets["markers"] = [
            marker_to_flow({
                "seq": seq,
                "role": "markers",
                "filename": filename,
                "hash": hash_value,
            })
            for seq, filename, hash_value in marker_entries
        ]
    else:
        assets.pop("markers", None)
    manifest["hash"] = manifest_hash(manifest)
