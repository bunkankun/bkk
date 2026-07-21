"""Report markers whose offsets are negative."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

import yaml

from bkk.index.merge import discover_bundles, find_bundle
from bkk.marker_assets import (
    VALID_BUCKETS,
    external_markers_for_bucket,
    inline_markers_for_bucket,
    load_marker_asset,
    marker_asset_entry_for_seq,
)

REPORT_VERSION = 1


class NegativeOffsetReportError(ValueError):
    pass


def find_negative_offset_markers(
    corpus_root: Path | str,
    *,
    text_id: str | None = None,
    text_prefixes: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Scan a corpus root for markers with negative integer offsets."""
    if text_id and text_prefixes:
        raise ValueError("provide at most one of text_id or text_prefixes")
    root = Path(corpus_root)
    if text_id:
        bundle_dir = find_bundle(root, text_id)
        if bundle_dir is None:
            raise FileNotFoundError(
                f"bundle directory not found for {text_id!r} under {root}"
            )
        bundle_dirs = [bundle_dir]
    else:
        prefixes = tuple(text_prefixes or ())
        if prefixes:
            seen: set[Path] = set()
            bundle_dirs = []
            for prefix in prefixes:
                for bundle_dir in discover_bundles(root, prefix=prefix):
                    if bundle_dir not in seen:
                        seen.add(bundle_dir)
                        bundle_dirs.append(bundle_dir)
            bundle_dirs.sort(key=lambda p: p.name)
            if not bundle_dirs:
                raise FileNotFoundError(
                    f"no bundles found under {root} with prefixes {list(prefixes)!r}"
                )
        else:
            bundle_dirs = discover_bundles(root)

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    scopes_scanned = 0
    for bundle_dir in bundle_dirs:
        result = find_negative_offset_markers_in_bundle(bundle_dir)
        rows.extend(result["rows"])
        errors.extend(result["errors"])
        scopes_scanned += result["scopes_scanned"]

    rows.sort(key=_row_sort_key)
    for idx, row in enumerate(rows, 1):
        row["id"] = idx
    return {
        "rows": rows,
        "errors": errors,
        "bundles_scanned": len(bundle_dirs),
        "scopes_scanned": scopes_scanned,
    }


def find_negative_offset_markers_in_bundle(bundle_dir: Path | str) -> dict[str, Any]:
    """Scan one bundle, including edition scopes, for negative marker offsets."""
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

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    scopes_scanned = 0
    for scope_dir, edition, manifest_path in scopes:
        if not manifest_path.exists():
            continue
        scopes_scanned += 1
        result = _scan_scope(scope_dir, manifest_path, text_id, edition=edition)
        rows.extend(result["rows"])
        errors.extend(result["errors"])
    rows.sort(key=_row_sort_key)
    for idx, row in enumerate(rows, 1):
        row["id"] = idx
    return {"rows": rows, "errors": errors, "scopes_scanned": scopes_scanned}


def write_negative_offset_report(
    rows: list[dict[str, Any]],
    out: Path | str | TextIO,
) -> None:
    if hasattr(out, "write"):
        _write(rows, out)
        return
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        _write(rows, fh)


def read_negative_offset_report(path: Path | str) -> list[dict[str, Any]]:
    report = Path(path)
    with report.open("r", encoding="utf-8") as fh:
        first = fh.readline().strip()
        expected = f"# bkk-negative-offsets version={REPORT_VERSION}"
        if first != expected:
            raise NegativeOffsetReportError(
                f"{report}: invalid negative offset report header "
                f"(expected {expected!r})"
            )
        rows: list[dict[str, Any]] = []
        for line_no, line in enumerate(fh, 2):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise NegativeOffsetReportError(
                    f"{report}:{line_no}: invalid JSON: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise NegativeOffsetReportError(
                    f"{report}:{line_no}: row is not an object"
                )
            rows.append(row)
    return rows


def _scan_scope(
    scope_dir: Path,
    manifest_path: Path,
    text_id: str,
    *,
    edition: str | None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        errors.append(_error(scope_dir, manifest_path, f"could not read manifest: {exc}"))
        return {"rows": rows, "errors": errors}
    if not isinstance(manifest, dict):
        errors.append(_error(scope_dir, manifest_path, "manifest top level is not a mapping"))
        return {"rows": rows, "errors": errors}

    title = ((manifest.get("metadata") or {}).get("title"))
    title_value = title if isinstance(title, str) else None
    for part in (manifest.get("assets") or {}).get("parts") or []:
        if not isinstance(part, dict):
            continue
        seq = part.get("seq")
        filename = part.get("filename")
        if not isinstance(seq, int) or not isinstance(filename, str):
            continue
        juan_path = scope_dir / filename
        try:
            juan = yaml.safe_load(juan_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            errors.append(_error(scope_dir, juan_path, f"could not read juan: {exc}"))
            continue
        if not isinstance(juan, dict):
            errors.append(_error(scope_dir, juan_path, "juan top level is not a mapping"))
            continue

        marker_asset = None
        marker_asset_path: Path | None = None
        entry = marker_asset_entry_for_seq(manifest, seq)
        marker_filename = (
            entry.get("filename")
            if isinstance(entry, dict) and isinstance(entry.get("filename"), str)
            else None
        )
        if marker_filename is not None:
            marker_asset_path = scope_dir / marker_filename
            try:
                marker_asset = load_marker_asset(scope_dir, manifest, seq)
            except (OSError, yaml.YAMLError) as exc:
                errors.append(
                    _error(scope_dir, marker_asset_path, f"could not read marker asset: {exc}")
                )

        for bucket in VALID_BUCKETS:
            for marker_index, marker in enumerate(inline_markers_for_bucket(juan, bucket)):
                rows.extend(_maybe_row(
                    scope_dir=scope_dir,
                    path=juan_path,
                    text_id=text_id,
                    title=title_value,
                    edition=edition,
                    seq=seq,
                    bucket=bucket,
                    marker=marker,
                    source="inline",
                    marker_index=marker_index,
                ))
            for marker_index, marker in enumerate(
                external_markers_for_bucket(marker_asset, bucket)
            ):
                rows.extend(_maybe_row(
                    scope_dir=scope_dir,
                    path=marker_asset_path or scope_dir,
                    text_id=text_id,
                    title=title_value,
                    edition=edition,
                    seq=seq,
                    bucket=bucket,
                    marker=marker,
                    source="asset",
                    marker_index=marker_index,
                ))
    return {"rows": rows, "errors": errors}


def _maybe_row(
    *,
    scope_dir: Path,
    path: Path,
    text_id: str,
    title: str | None,
    edition: str | None,
    seq: int,
    bucket: str,
    marker: dict[str, Any],
    source: str,
    marker_index: int,
) -> list[dict[str, Any]]:
    offset = marker.get("offset")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset >= 0:
        return []
    return [_row(
        scope_dir=scope_dir,
        path=path,
        text_id=text_id,
        title=title,
        edition=edition,
        seq=seq,
        bucket=bucket,
        marker=marker,
        source=source,
        marker_index=marker_index,
        offset=offset,
    )]


def _row(
    *,
    scope_dir: Path,
    path: Path,
    text_id: str,
    title: str | None,
    edition: str | None,
    seq: int,
    bucket: str,
    marker: dict[str, Any],
    source: str,
    marker_index: int,
    offset: int,
) -> dict[str, Any]:
    length = marker.get("length")
    return {
        "id": 0,
        "problem": "negative-offset",
        "textid": text_id,
        "title": title,
        "edition": edition,
        "seq": seq,
        "bucket": bucket,
        "offset": offset,
        "length": length if isinstance(length, int) and not isinstance(length, bool) else None,
        "marker_type": marker.get("type") if isinstance(marker.get("type"), str) else "",
        "marker_id": marker.get("id") if isinstance(marker.get("id"), str) else "",
        "source": source,
        "marker_index": marker_index,
        "path": _rel_path(scope_dir, path),
    }


def _write(rows: list[dict[str, Any]], fh: TextIO) -> None:
    fh.write(f"# bkk-negative-offsets version={REPORT_VERSION}\n")
    for row in rows:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
        fh.write("\n")


def _error(scope_dir: Path, path: Path, message: str) -> dict[str, Any]:
    return {"path": _rel_path(scope_dir, path), "message": message}


def _rel_path(scope_dir: Path, path: Path) -> str:
    try:
        return str(path.relative_to(scope_dir))
    except ValueError:
        return str(path)


def _row_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("textid") or "",
        row.get("edition") or "",
        row.get("seq") or 0,
        row.get("bucket") or "",
        row.get("offset") or 0,
        row.get("source") or "",
        row.get("path") or "",
        row.get("marker_index") or 0,
    )
