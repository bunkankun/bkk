"""Synthesize missing page-breaks before first-line line-break markers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from bkk.importer.hashing import manifest_hash
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.marker_assets import (
    VALID_BUCKETS,
    load_marker_asset,
    marker_asset_hash,
    marker_asset_entry_for_seq,
)


_FIRST_LINE_ID_RE = re.compile(r"^(?P<page_id>.+-\d+[A-Za-z])01$")
_IMAGE_LETTER_RE = re.compile(r"^(?P<head>.*)(?P<letter>[A-Za-z])(?P<suffix>\.[^./\\]+)$")
_IMAGE_NUMBER_RE = re.compile(r"^(?P<head>.*?)(?P<number>\d+)(?P<suffix>\.[^./\\]+)$")


def synthesize_missing_page_breaks(
    bundle_dir: Path | str,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Patch marker assets that start a page with ``line-break`` only.

    A KRP first-line marker such as ``KR3k0059_WYG_152-1a01`` implies a page
    marker ``KR3k0059_WYG_152-1a`` at the same offset. If that page-break is
    missing from the same bucket, insert it immediately before the line-break.
    When the next page-break has an image path, infer the preceding path by
    decrementing the final image letter, e.g. ``...0303b.png`` -> ``...0303a.png``.
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
                _synthesize_scope(
                    root,
                    short,
                    manifest_path,
                    dry_run=dry_run,
                )
            )
    return {"bundle_dir": str(bundle_dir), "dry_run": dry_run, "scopes": results}


def _synthesize_scope(
    root: Path,
    edition_short: str | None,
    manifest_path: Path,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(manifest, dict):
        raise RuntimeError(f"{manifest_path.name}: manifest top level is not a mapping")

    inserted_total = 0
    marker_assets_changed: dict[int, tuple[str, str]] = {}
    lines: list[str] = []

    for part in (manifest.get("assets") or {}).get("parts") or []:
        if not isinstance(part, dict):
            continue
        seq = part.get("seq")
        if not isinstance(seq, int):
            continue
        entry = marker_asset_entry_for_seq(manifest, seq)
        marker_filename = (
            entry.get("filename")
            if isinstance(entry, dict) and isinstance(entry.get("filename"), str)
            else None
        )
        if marker_filename is None:
            continue
        marker_asset = load_marker_asset(root, manifest, seq)
        if marker_asset is None:
            continue

        changed = False
        inserted_by_bucket: dict[str, int] = {}
        markers_obj = marker_asset.get("markers")
        if not isinstance(markers_obj, dict):
            continue
        for bucket in VALID_BUCKETS:
            markers = markers_obj.get(bucket)
            if not isinstance(markers, list):
                continue
            fixed, inserted = _synthesize_bucket(markers)
            if inserted:
                markers_obj[bucket] = [marker_to_flow(dict(marker)) for marker in fixed]
                inserted_by_bucket[bucket] = inserted
                inserted_total += inserted
                changed = True

        if not changed:
            continue
        lines.append(
            f"juan {seq:03d}: inserted "
            + ", ".join(
                f"{count} {bucket}" for bucket, count in inserted_by_bucket.items()
            )
        )
        if dry_run:
            continue
        marker_asset["hash"] = marker_asset_hash(marker_asset)
        (root / marker_filename).write_text(dump(marker_asset), encoding="utf-8")
        marker_assets_changed[seq] = (marker_filename, marker_asset["hash"])

    if not dry_run and marker_assets_changed:
        _patch_manifest(manifest, marker_assets_changed)
        manifest_path.write_text(dump(manifest), encoding="utf-8")

    return {
        "edition": edition_short or "bkk",
        "manifest": manifest_path.name,
        "inserted": inserted_total,
        "marker_assets_changed": sorted(marker_assets_changed),
        "lines": lines,
    }


def _synthesize_bucket(markers: list[Any]) -> tuple[list[dict[str, Any]], int]:
    typed_markers = [marker for marker in markers if isinstance(marker, dict)]
    page_break_ids = {
        marker.get("id")
        for marker in typed_markers
        if marker.get("type") == "page-break" and isinstance(marker.get("id"), str)
    }

    out: list[dict[str, Any]] = []
    inserted = 0
    for index, marker in enumerate(typed_markers):
        synth = _page_break_for_line_break(
            marker,
            page_break_ids=page_break_ids,
            following=_next_page_break(typed_markers, index + 1),
        )
        if synth is not None:
            out.append(synth)
            page_break_ids.add(synth["id"])
            inserted += 1
        out.append(dict(marker))
    return out, inserted


def _page_break_for_line_break(
    marker: dict[str, Any],
    *,
    page_break_ids: set[Any],
    following: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if marker.get("type") != "line-break":
        return None
    marker_id = marker.get("id")
    offset = marker.get("offset")
    if not isinstance(marker_id, str):
        return None
    if not isinstance(offset, int) or isinstance(offset, bool):
        return None
    match = _FIRST_LINE_ID_RE.match(marker_id)
    if match is None:
        return None
    page_id = match.group("page_id")
    if page_id in page_break_ids:
        return None

    synth: dict[str, Any] = {
        "type": "page-break",
        "offset": offset,
        "content": "",
        "id": page_id,
    }
    image = _preceding_image(following.get("image") if following else None)
    if image is not None:
        synth["image"] = image
    return synth


def _next_page_break(
    markers: list[dict[str, Any]],
    start: int,
) -> dict[str, Any] | None:
    for marker in markers[start:]:
        if marker.get("type") == "page-break":
            return marker
    return None


def _preceding_image(image: Any) -> str | None:
    if not isinstance(image, str) or not image:
        return None
    match = _IMAGE_LETTER_RE.match(image)
    if match is not None:
        letter = match.group("letter")
        if "b" <= letter <= "z" or "B" <= letter <= "Z":
            return (
                f"{match.group('head')}{chr(ord(letter) - 1)}"
                f"{match.group('suffix')}"
            )
        return None
    match = _IMAGE_NUMBER_RE.match(image)
    if match is not None:
        number = match.group("number")
        value = int(number)
        if value > 0:
            return (
                f"{match.group('head')}{value - 1:0{len(number)}d}"
                f"{match.group('suffix')}"
            )
    return None


def _patch_manifest(
    manifest: dict[str, Any],
    marker_assets: dict[int, tuple[str, str]],
) -> None:
    assets = manifest.setdefault("assets", {})
    marker_entries = []
    for entry in assets.get("markers") or []:
        if not isinstance(entry, dict):
            marker_entries.append(entry)
            continue
        seq = entry.get("seq")
        if isinstance(seq, int) and seq in marker_assets:
            filename, hash_value = marker_assets[seq]
            entry = dict(entry)
            entry["filename"] = filename
            entry["hash"] = hash_value
        marker_entries.append(marker_to_flow(entry))
    assets["markers"] = marker_entries
    manifest["hash"] = manifest_hash(manifest)
