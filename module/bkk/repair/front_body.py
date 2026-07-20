"""Move misplaced juan front-bucket content into empty body buckets."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.marker_assets import (
    VALID_BUCKETS,
    build_marker_asset,
    effective_markers_for_bucket,
    load_marker_asset,
    marker_asset_entry_for_seq,
    marker_asset_filename,
    marker_asset_hash,
    split_inline_external_markers,
    toc_marker_ids,
)


def move_front_to_empty_body(bundle_dir: Path, *, dry_run: bool = True) -> dict[str, Any]:
    """Find juans whose ``body.text`` is empty and ``front.text`` is not.

    In write mode, the full front bucket is prepended to body, markers are
    moved with rebased offsets, TOC spans are moved from front to body, and
    juan/asset/manifest hashes are patched.
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
                _move_scope(
                    text_id, root, short, manifest_path, dry_run=dry_run,
                )
            )
    return {"bundle_dir": str(bundle_dir), "dry_run": dry_run, "scopes": results}


def _move_scope(
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

    moved: list[dict[str, Any]] = []
    written_juans: dict[int, str] = {}
    written_marker_assets: dict[int, tuple[str, str] | None] = {}
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
        front = juan.get("front")
        body = juan.get("body")
        if not isinstance(front, dict):
            continue
        if not isinstance(body, dict):
            body = {"text": "", "hash": ZERO_HASH}
            juan["body"] = body

        front_text = front.get("text") or ""
        body_text = body.get("text") or ""
        if not isinstance(front_text, str) or not isinstance(body_text, str):
            continue
        if not front_text or body_text:
            continue

        marker_asset = load_marker_asset(root, manifest, seq)
        changed = _move_one_juan(
            text_id=text_id,
            seq=seq,
            edition_short=edition_short,
            manifest=manifest,
            juan=juan,
            marker_asset=marker_asset,
        )
        moved.append({
            "seq": seq,
            "filename": filename,
            "chars": len(front_text),
            "front_markers": changed["front_markers"],
            "body_markers": changed["body_markers"],
        })
        lines.append(
            f"juan {seq:03d}: move {len(front_text)} chars and "
            f"{changed['front_markers']} front markers into body"
        )

        if dry_run:
            continue

        juan["hash"] = _self_hash(juan)
        written_juans[seq] = juan["hash"]
        juan_path.write_text(dump(juan), encoding="utf-8")

        marker_path = changed["marker_path"]
        marker_asset = changed["marker_asset"]
        if marker_path is not None and marker_asset is not None:
            marker_asset["hash"] = marker_asset_hash(marker_asset)
            (root / marker_path).parent.mkdir(parents=True, exist_ok=True)
            (root / marker_path).write_text(dump(marker_asset), encoding="utf-8")
            written_marker_assets[seq] = (marker_path, marker_asset["hash"])
        elif marker_path is not None:
            asset_path = root / marker_path
            if asset_path.exists():
                asset_path.unlink()
            written_marker_assets[seq] = None

    if not dry_run and (written_juans or written_marker_assets):
        _patch_manifest(manifest, written_juans, written_marker_assets)
        manifest_path.write_text(dump(manifest), encoding="utf-8")

    return {
        "edition": edition_short or "bkk",
        "manifest": manifest_path.name,
        "moved": len(moved),
        "chars": sum(item["chars"] for item in moved),
        "juans_changed": [item["seq"] for item in moved],
        "lines": lines,
    }


def _move_one_juan(
    *,
    text_id: str,
    seq: int,
    edition_short: str | None,
    manifest: dict[str, Any],
    juan: dict[str, Any],
    marker_asset: dict[str, Any] | None,
) -> dict[str, Any]:
    front = juan["front"]
    body = juan["body"]
    front_text = front.get("text") or ""
    body_text = body.get("text") or ""
    front_len = len(front_text)

    front_markers = effective_markers_for_bucket(juan, "front", marker_asset)
    body_markers = effective_markers_for_bucket(juan, "body", marker_asset)
    next_front_markers, next_body_markers = _move_bucket_markers(
        front_markers,
        body_markers,
        moved_len=front_len,
        body_len=len(body_text),
    )

    body["text"] = front_text + body_text
    body["hash"] = sha256_text(body["text"]) if body["text"] else ZERO_HASH
    juan.pop("front", None)

    _move_toc_spans(manifest, seq=seq, moved_len=front_len)

    keep_ids = toc_marker_ids(manifest, seq)
    marker_asset, marker_path = _put_markers(
        text_id=text_id,
        seq=seq,
        edition_short=edition_short,
        manifest=manifest,
        juan=juan,
        marker_asset=marker_asset,
        bucket="body",
        markers=next_body_markers,
        keep_ids=keep_ids,
    )

    if marker_asset is not None:
        markers_obj = marker_asset.setdefault("markers", {})
        markers_obj.pop("front", None)
        if not any(markers_obj.get(name) for name in VALID_BUCKETS):
            marker_asset = None
    if next_front_markers:
        raise RuntimeError("front markers unexpectedly remained after full front move")

    return {
        "front_markers": len(front_markers),
        "body_markers": len(body_markers),
        "marker_asset": marker_asset,
        "marker_path": marker_path,
    }


def _move_bucket_markers(
    front_markers: list[dict[str, Any]],
    body_markers: list[dict[str, Any]],
    *,
    moved_len: int,
    body_len: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    moved = [dict(marker) for marker in front_markers]
    for marker in moved:
        if _marker_extent(marker, moved_len) is None:
            raise RuntimeError("front marker has invalid offset or length")
    shifted_body: list[dict[str, Any]] = []
    for marker in body_markers:
        next_marker = dict(marker)
        extent = _marker_extent(next_marker, body_len)
        if extent is None:
            raise RuntimeError("body marker has invalid offset or length")
        offset = next_marker["offset"]
        next_marker["offset"] = offset + moved_len
        shifted_body.append(next_marker)
    combined = moved + shifted_body
    combined.sort(key=lambda marker: marker.get("offset", 0))
    return [], combined


def _marker_extent(marker: dict[str, Any], text_len: int) -> tuple[int, int] | None:
    offset = marker.get("offset")
    if not isinstance(offset, int) or isinstance(offset, bool):
        return None
    if offset < 0 or offset > text_len:
        return None
    length = marker.get("length")
    if length is None:
        return offset, offset
    if not isinstance(length, int) or isinstance(length, bool) or length < 0:
        return None
    end = offset + length
    if end > text_len:
        return None
    return offset, end


def _move_toc_spans(manifest: dict[str, Any], *, seq: int, moved_len: int) -> None:
    for entry in manifest.get("table_of_contents") or []:
        if not isinstance(entry, dict):
            continue
        ref = entry.get("ref")
        if not isinstance(ref, dict) or ref.get("seq") != seq:
            continue
        span = ref.get("span")
        if not (
            isinstance(span, list)
            and len(span) == 3
            and isinstance(span[0], str)
            and isinstance(span[1], int)
            and isinstance(span[2], int)
        ):
            continue
        bucket, start, end = span
        if bucket == "front":
            ref["span"] = ["body", start, end]
        elif bucket == "body":
            ref["span"] = ["body", start + moved_len, end + moved_len]


def _put_markers(
    *,
    text_id: str,
    seq: int,
    edition_short: str | None,
    manifest: dict[str, Any],
    juan: dict[str, Any],
    marker_asset: dict[str, Any] | None,
    bucket: str,
    markers: list[dict[str, Any]],
    keep_ids: set[str],
) -> tuple[dict[str, Any] | None, str | None]:
    inline, external = split_inline_external_markers(markers, keep_ids=keep_ids)
    bucket_obj = juan.get(bucket)
    if not isinstance(bucket_obj, dict):
        raise RuntimeError(f"bucket {bucket} is missing")
    if inline:
        bucket_obj["markers"] = [marker_to_flow(dict(marker)) for marker in inline]
    else:
        bucket_obj.pop("markers", None)

    entry = marker_asset_entry_for_seq(manifest, seq)
    marker_path = (
        entry.get("filename")
        if isinstance(entry, dict) and isinstance(entry.get("filename"), str)
        else None
    )
    if external and marker_asset is None:
        marker_asset = build_marker_asset(text_id, seq, edition_short, {})
        marker_path = marker_asset_filename(text_id, seq, edition_short)
    if marker_asset is not None:
        markers_obj = marker_asset.setdefault("markers", {})
        if external:
            markers_obj[bucket] = [marker_to_flow(dict(marker)) for marker in external]
        else:
            markers_obj.pop(bucket, None)
        if any(markers_obj.get(name) for name in VALID_BUCKETS):
            return marker_asset, marker_path
        return None, marker_path
    return None, marker_path


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
    marker_entries.sort(key=lambda entry: entry.get("seq", 0) if isinstance(entry, dict) else 0)
    if marker_entries:
        assets["markers"] = marker_entries
    else:
        assets.pop("markers", None)

    manifest["hash"] = manifest_hash(manifest)


def _self_hash(data: dict[str, Any]) -> str:
    zeroed = copy.deepcopy(data)
    zeroed["hash"] = ZERO_HASH
    return sha256_jcs(zeroed)
