"""Remove a documentary edition from a bundle."""

from __future__ import annotations

import copy
import shutil
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


_VARIANT_BASE_KEYS = {"type", "offset", "length", "content", "id"}


def remove_edition(
    bundle_dir: Path,
    edition_short: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Remove ``edition_short`` from ``bundle_dir``.

    The operation deletes ``editions/<short>/``, removes the short from the
    master manifest's top-level ``editions`` list, and prunes that short from
    every ``type: variant`` marker in the remaining bundle scopes.
    """
    bundle_dir = Path(bundle_dir).resolve()
    if not bundle_dir.is_dir():
        raise FileNotFoundError(f"not a directory: {bundle_dir}")
    text_id = bundle_dir.name
    if edition_short in {"bkk", "krp"}:
        raise ValueError("remove-edition only removes directories under editions/")

    master_manifest_path = bundle_dir / f"{text_id}.manifest.yaml"
    master_manifest = _load_manifest(master_manifest_path)
    edition_dir = bundle_dir / "editions" / edition_short
    listed = _manifest_lists_edition(master_manifest, edition_short)
    if not edition_dir.is_dir() and not listed:
        raise FileNotFoundError(
            f"edition {edition_short!r} not found in editions/ or manifest"
        )

    scopes: list[tuple[Path, Path, bool]] = [
        (bundle_dir, master_manifest_path, True),
    ]
    editions_root = bundle_dir / "editions"
    if editions_root.is_dir():
        for sub in sorted(editions_root.iterdir()):
            if sub.is_dir() and sub.name != edition_short:
                scopes.append((
                    sub,
                    sub / f"{text_id}-{sub.name}.manifest.yaml",
                    False,
                ))

    scope_results = []
    for root, manifest_path, master in scopes:
        if manifest_path.exists():
            manifest = master_manifest if master else _load_manifest(manifest_path)
            scope_results.append(
                _remove_from_scope(
                    root=root,
                    manifest_path=manifest_path,
                    manifest=manifest,
                    edition_short=edition_short,
                    master=master,
                    dry_run=dry_run,
                )
            )

    if not dry_run and edition_dir.is_dir():
        shutil.rmtree(edition_dir)

    return {
        "bundle_dir": str(bundle_dir),
        "edition": edition_short,
        "dry_run": dry_run,
        "edition_dir": str(edition_dir.relative_to(bundle_dir)),
        "edition_dir_exists": edition_dir.is_dir(),
        "manifest_listed": listed,
        "scopes": scope_results,
    }


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(manifest, dict):
        raise RuntimeError(f"{path.name}: manifest top level is not a mapping")
    return manifest


def _manifest_lists_edition(manifest: dict[str, Any], edition_short: str) -> bool:
    editions = manifest.get("editions")
    if not isinstance(editions, list):
        return False
    return any(
        isinstance(entry, dict) and entry.get("short") == edition_short
        for entry in editions
    )


def _remove_from_scope(
    *,
    root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    edition_short: str,
    master: bool,
    dry_run: bool,
) -> dict[str, Any]:
    juan_hashes: dict[int, str] = {}
    changed_juans: set[int] = set()
    marker_assets: dict[int, tuple[str, str] | None] = {}
    removed_witnesses = 0
    dropped_variants = 0
    changed_assets = 0
    deleted_assets = 0

    for part in (manifest.get("assets") or {}).get("parts") or []:
        if not isinstance(part, dict):
            continue
        seq = part.get("seq")
        filename = part.get("filename")
        if not isinstance(seq, int) or not isinstance(filename, str):
            continue
        juan_path = root / filename
        if not juan_path.exists():
            continue
        juan = yaml.safe_load(juan_path.read_text(encoding="utf-8")) or {}
        if not isinstance(juan, dict):
            continue

        inline_changed, inline_removed, inline_dropped = _prune_inline_variants(
            juan, edition_short,
        )
        if inline_changed:
            removed_witnesses += inline_removed
            dropped_variants += inline_dropped
            changed_juans.add(seq)
            if not dry_run:
                juan["hash"] = _self_hash(juan)
                juan_hashes[seq] = juan["hash"]
                juan_path.write_text(dump(juan), encoding="utf-8")

        marker_asset = load_marker_asset(root, manifest, seq)
        if marker_asset is None:
            continue
        asset_changed, asset_removed, asset_dropped = _prune_marker_asset_variants(
            marker_asset, edition_short,
        )
        if not asset_changed:
            continue
        removed_witnesses += asset_removed
        dropped_variants += asset_dropped
        entry = marker_asset_entry_for_seq(manifest, seq)
        filename = entry.get("filename") if isinstance(entry, dict) else None
        if not isinstance(filename, str):
            continue
        if _marker_asset_has_markers(marker_asset):
            marker_asset["hash"] = marker_asset_hash(marker_asset)
            marker_assets[seq] = (filename, marker_asset["hash"])
            changed_assets += 1
            if not dry_run:
                (root / filename).write_text(dump(marker_asset), encoding="utf-8")
        else:
            marker_assets[seq] = None
            deleted_assets += 1
            if not dry_run:
                asset_path = root / filename
                if asset_path.exists():
                    asset_path.unlink()

    manifest_changed = False
    if master:
        manifest_changed = _remove_manifest_edition(manifest, edition_short)

    if not dry_run and (juan_hashes or marker_assets or manifest_changed):
        _patch_manifest(manifest, juan_hashes, marker_assets)
        manifest_path.write_text(dump(manifest), encoding="utf-8")

    return {
        "edition": "bkk" if master else root.name,
        "manifest": manifest_path.name,
        "manifest_changed": manifest_changed,
        "variant_witnesses_removed": removed_witnesses,
        "variant_markers_dropped": dropped_variants,
        "juans_changed": sorted(changed_juans),
        "marker_assets_changed": changed_assets,
        "marker_assets_deleted": deleted_assets,
    }


def _remove_manifest_edition(
    manifest: dict[str, Any],
    edition_short: str,
) -> bool:
    editions = manifest.get("editions")
    if not isinstance(editions, list):
        return False
    kept = [
        marker_to_flow(dict(entry)) if isinstance(entry, dict) else entry
        for entry in editions
        if not (isinstance(entry, dict) and entry.get("short") == edition_short)
    ]
    if len(kept) == len(editions):
        return False
    if kept:
        manifest["editions"] = kept
    else:
        manifest.pop("editions", None)
    return True


def _prune_inline_variants(
    juan: dict[str, Any],
    edition_short: str,
) -> tuple[bool, int, int]:
    changed = False
    removed = 0
    dropped = 0
    for bucket_name in VALID_BUCKETS:
        bucket = juan.get(bucket_name)
        if not isinstance(bucket, dict):
            continue
        markers = bucket.get("markers")
        if not isinstance(markers, list):
            continue
        next_markers, bucket_removed, bucket_dropped = _prune_variant_list(
            markers, edition_short,
        )
        if bucket_removed or bucket_dropped:
            changed = True
            removed += bucket_removed
            dropped += bucket_dropped
            if next_markers:
                bucket["markers"] = next_markers
            else:
                bucket.pop("markers", None)
    return changed, removed, dropped


def _prune_marker_asset_variants(
    marker_asset: dict[str, Any],
    edition_short: str,
) -> tuple[bool, int, int]:
    markers_obj = marker_asset.get("markers")
    if not isinstance(markers_obj, dict):
        return False, 0, 0
    changed = False
    removed = 0
    dropped = 0
    for bucket_name in VALID_BUCKETS:
        markers = markers_obj.get(bucket_name)
        if not isinstance(markers, list):
            continue
        next_markers, bucket_removed, bucket_dropped = _prune_variant_list(
            markers, edition_short,
        )
        if bucket_removed or bucket_dropped:
            changed = True
            removed += bucket_removed
            dropped += bucket_dropped
            markers_obj[bucket_name] = next_markers
    return changed, removed, dropped


def _prune_variant_list(
    markers: list[Any],
    edition_short: str,
) -> tuple[list[Any], int, int]:
    out: list[Any] = []
    removed = 0
    dropped = 0
    for marker in markers:
        if not isinstance(marker, dict) or marker.get("type") != "variant":
            out.append(marker)
            continue
        if edition_short not in marker:
            out.append(marker)
            continue
        next_marker = dict(marker)
        next_marker.pop(edition_short, None)
        removed += 1
        witness_keys = [
            key for key in next_marker
            if key not in _VARIANT_BASE_KEYS
        ]
        if witness_keys:
            out.append(marker_to_flow(next_marker))
        else:
            dropped += 1
    return out, removed, dropped


def _marker_asset_has_markers(marker_asset: dict[str, Any]) -> bool:
    markers_obj = marker_asset.get("markers")
    if not isinstance(markers_obj, dict):
        return False
    return any(
        bool(markers_obj.get(bucket_name))
        for bucket_name in VALID_BUCKETS
    )


def _patch_manifest(
    manifest: dict[str, Any],
    juan_hashes: dict[int, str],
    marker_assets: dict[int, tuple[str, str] | None],
) -> None:
    assets = manifest.setdefault("assets", {})
    parts_out = []
    for entry in assets.get("parts") or []:
        if not isinstance(entry, dict):
            parts_out.append(entry)
            continue
        seq = entry.get("seq")
        if isinstance(seq, int) and seq in juan_hashes:
            entry = dict(entry)
            entry["hash"] = juan_hashes[seq]
        parts_out.append(marker_to_flow(entry))
    assets["parts"] = parts_out

    marker_entries = []
    seen: set[int] = set()
    for entry in assets.get("markers") or []:
        if not isinstance(entry, dict):
            continue
        seq = entry.get("seq")
        if not isinstance(seq, int):
            marker_entries.append(marker_to_flow(entry))
            continue
        seen.add(seq)
        if seq not in marker_assets:
            marker_entries.append(marker_to_flow(entry))
            continue
        asset_info = marker_assets[seq]
        if asset_info is None:
            continue
        filename, hash_value = asset_info
        next_entry = dict(entry)
        next_entry["filename"] = filename
        next_entry["hash"] = hash_value
        marker_entries.append(marker_to_flow(next_entry))
    for seq, asset_info in marker_assets.items():
        if seq in seen or asset_info is None:
            continue
        filename, hash_value = asset_info
        marker_entries.append(marker_to_flow({
            "seq": seq,
            "role": "markers",
            "filename": filename,
            "hash": hash_value,
        }))
    marker_entries.sort(
        key=lambda entry: entry.get("seq", 0) if isinstance(entry, dict) else 0,
    )
    if marker_entries:
        assets["markers"] = marker_entries
    else:
        assets.pop("markers", None)

    manifest["hash"] = manifest_hash(manifest)


def _self_hash(data: dict[str, Any]) -> str:
    zeroed = copy.deepcopy(data)
    zeroed["hash"] = ZERO_HASH
    return sha256_jcs(zeroed)
