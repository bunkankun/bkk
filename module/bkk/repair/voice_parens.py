"""Repair paren punctuation split across front/body bucket boundaries."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.index.merge import find_bundle
from bkk.marker_assets import (
    effective_markers_for_bucket,
    load_marker_asset,
    marker_asset_entry_for_seq,
    marker_asset_hash,
)
from bkk.voice.problems import read_voice_problems_report


_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


class VoiceParenBoundaryRepairError(RuntimeError):
    """A targeted boundary-paren repair could not be applied."""


def move_body_initial_close_parens_from_report(
    corpus_root: Path | str,
    report_path: Path | str,
    *,
    dry_run: bool = True,
    text_id: str | None = None,
    text_prefixes: list[str] | None = None,
) -> dict[str, Any]:
    """Move misplaced ``)`` punctuation markers from body start to front end.

    The voice-problem report is used only as the targeting source. A juan is
    eligible when the report contains both:

    - ``front`` / ``parens`` / ``unmatched-open`` for the same text, edition,
      and seq; and
    - ``body`` / ``parens`` / ``stray-close`` at offset 0.

    The actual marker moved is the source ``punctuation`` marker with
    ``content == ")"`` and ``offset == 0`` in the body bucket. Existing
    ``voice:problem`` markers are left for ``bkk voice add`` to refresh.
    """
    root = Path(corpus_root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"corpus root is not a directory: {root}")
    report = Path(report_path).expanduser().resolve()
    rows = read_voice_problems_report(report)
    targets = _targets_from_report(
        rows,
        text_id=text_id,
        text_prefixes=text_prefixes or [],
    )

    by_text: dict[str, dict[tuple[str | None, int], set[int]]] = {}
    for key, front_offsets in targets.items():
        row_text_id, edition, seq = key
        by_text.setdefault(row_text_id, {})[(edition, seq)] = front_offsets

    bundles = []
    errors: list[dict[str, Any]] = []
    total_moved = 0
    total_skipped = 0
    for target_text_id in sorted(by_text):
        bundle_dir = find_bundle(root, target_text_id)
        if bundle_dir is None:
            errors.append({
                "textid": target_text_id,
                "message": f"bundle not found under {root}",
            })
            continue
        result = move_body_initial_close_parens_in_bundle(
            bundle_dir,
            by_text[target_text_id],
            dry_run=dry_run,
        )
        total_moved += result["moved"]
        total_skipped += result["skipped"]
        bundles.append(result)

    return {
        "corpus_root": str(root),
        "report_path": str(report),
        "dry_run": dry_run,
        "targets": len(targets),
        "moved": total_moved,
        "skipped": total_skipped,
        "bundles": bundles,
        "errors": errors,
    }


def move_body_initial_close_parens_in_bundle(
    bundle_dir: Path | str,
    targets: dict[tuple[str | None, int], set[int]],
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Apply targeted boundary-paren repairs in one bundle."""
    bundle_dir = Path(bundle_dir).expanduser().resolve()
    if not bundle_dir.is_dir():
        raise FileNotFoundError(f"not a directory: {bundle_dir}")
    text_id = bundle_dir.name

    moved = 0
    skipped = 0
    scopes: list[dict[str, Any]] = []
    for scope_dir, short, manifest_path in _scope_paths(bundle_dir, text_id):
        scope_targets = {
            seq: offsets
            for (edition, seq), offsets in targets.items()
            if edition == short
        }
        if not scope_targets:
            continue
        scope = _move_scope(
            text_id,
            scope_dir,
            short,
            manifest_path,
            scope_targets,
            dry_run=dry_run,
        )
        moved += scope["moved"]
        skipped += scope["skipped"]
        scopes.append(scope)

    return {
        "bundle_dir": str(bundle_dir),
        "textid": text_id,
        "dry_run": dry_run,
        "moved": moved,
        "skipped": skipped,
        "scopes": scopes,
    }


def _targets_from_report(
    rows: list[dict[str, Any]],
    *,
    text_id: str | None,
    text_prefixes: list[str],
) -> dict[tuple[str, str | None, int], set[int]]:
    front_offsets: dict[tuple[str, str | None, int], set[int]] = {}
    body_keys: set[tuple[str, str | None, int]] = set()
    prefixes = tuple(text_prefixes)

    for row in rows:
        row_text_id = row.get("textid")
        if not isinstance(row_text_id, str):
            continue
        if text_id is not None and row_text_id != text_id:
            continue
        if prefixes and not row_text_id.startswith(prefixes):
            continue
        seq = row.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            continue
        edition = row.get("edition")
        if edition is not None and not isinstance(edition, str):
            continue
        if row.get("source") != "parens":
            continue
        key = (row_text_id, edition, seq)
        if (
            row.get("bucket") == "front"
            and row.get("code") == "unmatched-open"
            and isinstance(row.get("offset"), int)
        ):
            front_offsets.setdefault(key, set()).add(row["offset"])
        elif (
            row.get("bucket") == "body"
            and row.get("code") == "stray-close"
            and row.get("offset") == 0
        ):
            body_keys.add(key)

    return {
        key: offsets
        for key, offsets in front_offsets.items()
        if key in body_keys
    }


def _scope_paths(bundle_dir: Path, text_id: str) -> list[tuple[Path, str | None, Path]]:
    out: list[tuple[Path, str | None, Path]] = [
        (bundle_dir, None, bundle_dir / f"{text_id}.manifest.yaml"),
    ]
    editions = bundle_dir / "editions"
    if editions.is_dir():
        for sub in sorted(editions.iterdir()):
            if sub.is_dir():
                out.append((sub, sub.name, sub / f"{text_id}-{sub.name}.manifest.yaml"))
    return [(root, short, manifest) for root, short, manifest in out if manifest.exists()]


def _move_scope(
    text_id: str,
    scope_dir: Path,
    edition_short: str | None,
    manifest_path: Path,
    scope_targets: dict[int, set[int]],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    manifest = yaml.load(manifest_path.read_text(encoding="utf-8"), Loader=_YAML_LOADER) or {}
    if not isinstance(manifest, dict):
        raise VoiceParenBoundaryRepairError(
            f"{manifest_path.name}: manifest top level is not a mapping"
        )

    moved_rows: list[dict[str, Any]] = []
    skipped_rows: list[dict[str, Any]] = []
    juan_hashes: dict[int, str] = {}
    marker_hashes: dict[int, tuple[str, str]] = {}

    for part in (manifest.get("assets") or {}).get("parts") or []:
        if not isinstance(part, dict):
            continue
        seq = part.get("seq")
        filename = part.get("filename")
        if not isinstance(seq, int) or not isinstance(filename, str):
            continue
        if seq not in scope_targets:
            continue

        juan_path = scope_dir / filename
        juan = yaml.load(juan_path.read_text(encoding="utf-8"), Loader=_YAML_LOADER) or {}
        if not isinstance(juan, dict):
            skipped_rows.append(_skip(seq, "juan top level is not a mapping"))
            continue
        marker_asset = load_marker_asset(scope_dir, manifest, seq)
        result = _move_one_juan(
            juan,
            marker_asset,
            front_open_offsets=scope_targets[seq],
        )
        if not result["moved"]:
            skipped_rows.append(_skip(seq, result["reason"]))
            continue

        moved_rows.append({
            "seq": seq,
            "filename": filename,
            "marker_id": result["marker_id"],
            "from": ["body", 0],
            "to": ["front", result["front_offset"]],
        })

        if dry_run:
            continue
        if result["juan_changed"]:
            juan["hash"] = _self_hash(juan)
            juan_path.write_text(dump(juan), encoding="utf-8")
            juan_hashes[seq] = juan["hash"]
        if result["asset_changed"]:
            if marker_asset is None:
                raise VoiceParenBoundaryRepairError(
                    f"{manifest_path.name} seq {seq}: marker asset changed but is missing"
                )
            entry = marker_asset_entry_for_seq(manifest, seq)
            marker_filename = (
                entry.get("filename")
                if isinstance(entry, dict) and isinstance(entry.get("filename"), str)
                else None
            )
            if marker_filename is None:
                raise VoiceParenBoundaryRepairError(
                    f"{manifest_path.name} seq {seq}: marker asset manifest entry missing"
                )
            marker_asset["hash"] = marker_asset_hash(marker_asset)
            (scope_dir / marker_filename).write_text(dump(marker_asset), encoding="utf-8")
            marker_hashes[seq] = (marker_filename, marker_asset["hash"])

    if not dry_run and (juan_hashes or marker_hashes):
        _patch_manifest(manifest, juan_hashes, marker_hashes)
        manifest_path.write_text(dump(manifest), encoding="utf-8")

    return {
        "edition": edition_short or "bkk",
        "manifest": manifest_path.name,
        "moved": len(moved_rows),
        "skipped": len(skipped_rows),
        "moves": moved_rows,
        "skips": skipped_rows,
    }


def _move_one_juan(
    juan: dict[str, Any],
    marker_asset: dict[str, Any] | None,
    *,
    front_open_offsets: set[int],
) -> dict[str, Any]:
    front = juan.get("front")
    body = juan.get("body")
    if not isinstance(front, dict):
        return _not_moved("front bucket is missing")
    if not isinstance(body, dict):
        return _not_moved("body bucket is missing")
    front_text = front.get("text") or ""
    if not isinstance(front_text, str):
        return _not_moved("front.text is not a string")
    if not _has_front_open_marker(juan, marker_asset, front_open_offsets):
        return _not_moved("matching front '(' punctuation marker not found")

    inline_body = _marker_list(body)
    asset_body = _asset_marker_list(marker_asset, "body")
    candidates = _close_candidates(inline_body, asset_body)
    if len(candidates) != 1:
        return _not_moved(
            f"expected exactly one body ')' punctuation at offset 0, found {len(candidates)}"
        )

    source, index = candidates[0]
    front_offset = len(front_text)
    if source == "inline":
        marker = dict(inline_body.pop(index))
        marker["offset"] = front_offset
        inline_front = _ensure_inline_marker_list(front)
        inline_front.append(marker)
        _write_marker_list(body, inline_body)
        _write_marker_list(front, _sort_markers(inline_front))
        return _moved(marker, front_offset, juan_changed=True, asset_changed=False)

    if marker_asset is None:
        return _not_moved("external body marker found but marker asset is missing")
    marker = dict(asset_body.pop(index))
    marker["offset"] = front_offset
    asset_front = _ensure_asset_marker_list(marker_asset, "front")
    asset_front.append(marker)
    _write_asset_marker_list(marker_asset, "body", asset_body)
    _write_asset_marker_list(marker_asset, "front", _sort_markers(asset_front))
    return _moved(marker, front_offset, juan_changed=False, asset_changed=True)


def _has_front_open_marker(
    juan: dict[str, Any],
    marker_asset: dict[str, Any] | None,
    front_open_offsets: set[int],
) -> bool:
    for marker in effective_markers_for_bucket(juan, "front", marker_asset):
        if (
            marker.get("type") == "punctuation"
            and marker.get("content") == "("
            and marker.get("offset") in front_open_offsets
        ):
            return True
    return False


def _marker_list(bucket: dict[str, Any]) -> list[Any]:
    markers = bucket.get("markers")
    return list(markers) if isinstance(markers, list) else []


def _ensure_inline_marker_list(bucket: dict[str, Any]) -> list[Any]:
    markers = bucket.get("markers")
    if not isinstance(markers, list):
        markers = []
        bucket["markers"] = markers
    return markers


def _asset_marker_list(
    marker_asset: dict[str, Any] | None,
    bucket: str,
) -> list[Any]:
    if marker_asset is None:
        return []
    markers_obj = marker_asset.get("markers")
    if not isinstance(markers_obj, dict):
        return []
    markers = markers_obj.get(bucket)
    return list(markers) if isinstance(markers, list) else []


def _ensure_asset_marker_list(marker_asset: dict[str, Any], bucket: str) -> list[Any]:
    markers_obj = marker_asset.setdefault("markers", {})
    if not isinstance(markers_obj, dict):
        markers_obj = {}
        marker_asset["markers"] = markers_obj
    markers = markers_obj.get(bucket)
    if not isinstance(markers, list):
        markers = []
        markers_obj[bucket] = markers
    return markers


def _write_marker_list(bucket: dict[str, Any], markers: list[Any]) -> None:
    if markers:
        bucket["markers"] = [_flow_marker(marker) for marker in markers]
    else:
        bucket.pop("markers", None)


def _write_asset_marker_list(
    marker_asset: dict[str, Any],
    bucket: str,
    markers: list[Any],
) -> None:
    markers_obj = marker_asset.setdefault("markers", {})
    if not isinstance(markers_obj, dict):
        markers_obj = {}
        marker_asset["markers"] = markers_obj
    if markers:
        markers_obj[bucket] = [_flow_marker(marker) for marker in markers]
    else:
        markers_obj.pop(bucket, None)


def _close_candidates(
    inline_body: list[Any],
    asset_body: list[Any],
) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for index, marker in enumerate(inline_body):
        if _is_body_initial_close(marker):
            out.append(("inline", index))
    for index, marker in enumerate(asset_body):
        if _is_body_initial_close(marker):
            out.append(("asset", index))
    return out


def _is_body_initial_close(marker: Any) -> bool:
    return (
        isinstance(marker, dict)
        and marker.get("type") == "punctuation"
        and marker.get("content") == ")"
        and marker.get("offset") == 0
    )


def _sort_markers(markers: list[Any]) -> list[Any]:
    indexed = list(enumerate(markers))
    indexed.sort(
        key=lambda item: (
            item[1].get("offset", 0)
            if isinstance(item[1], dict) and isinstance(item[1].get("offset"), int)
            else 0,
            item[0],
        )
    )
    return [marker for _, marker in indexed]


def _flow_marker(marker: Any) -> Any:
    if isinstance(marker, dict):
        return marker_to_flow(dict(marker))
    return marker


def _not_moved(reason: str) -> dict[str, Any]:
    return {
        "moved": False,
        "reason": reason,
        "juan_changed": False,
        "asset_changed": False,
    }


def _moved(
    marker: dict[str, Any],
    front_offset: int,
    *,
    juan_changed: bool,
    asset_changed: bool,
) -> dict[str, Any]:
    return {
        "moved": True,
        "marker_id": marker.get("id") if isinstance(marker.get("id"), str) else "",
        "front_offset": front_offset,
        "juan_changed": juan_changed,
        "asset_changed": asset_changed,
    }


def _skip(seq: int, reason: str) -> dict[str, Any]:
    return {"seq": seq, "reason": reason}


def _patch_manifest(
    manifest: dict[str, Any],
    juan_hashes: dict[int, str],
    marker_hashes: dict[int, tuple[str, str]],
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

    markers_out = []
    for entry in assets.get("markers") or []:
        if not isinstance(entry, dict):
            markers_out.append(entry)
            continue
        seq = entry.get("seq")
        if isinstance(seq, int) and seq in marker_hashes:
            filename, hash_value = marker_hashes[seq]
            entry = dict(entry)
            entry["filename"] = filename
            entry["hash"] = hash_value
        markers_out.append(marker_to_flow(entry))
    if markers_out:
        assets["markers"] = markers_out
    manifest["hash"] = manifest_hash(manifest)


def _self_hash(data: dict[str, Any]) -> str:
    zeroed = copy.deepcopy(data)
    zeroed["hash"] = ZERO_HASH
    return sha256_jcs(zeroed)
