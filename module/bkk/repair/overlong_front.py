"""Report non-initial juans with unusually long front buckets."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TextIO

import yaml

from bkk.index.merge import discover_bundles, find_bundle

REPORT_VERSION = 1
DEFAULT_MIN_CHARS = 200


def find_overlong_front_buckets(
    corpus_root: Path | str,
    *,
    text_id: str | None = None,
    text_prefixes: Sequence[str] | None = None,
    min_chars: int = DEFAULT_MIN_CHARS,
    include_first: bool = False,
) -> dict[str, Any]:
    """Scan a corpus root for juans whose ``front.text`` is too long."""
    if text_id and text_prefixes:
        raise ValueError("provide at most one of text_id or text_prefixes")
    if min_chars < 0:
        raise ValueError("min_chars must be >= 0")

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
        result = find_overlong_front_buckets_in_bundle(
            bundle_dir,
            min_chars=min_chars,
            include_first=include_first,
        )
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


def find_overlong_front_buckets_in_bundle(
    bundle_dir: Path | str,
    *,
    min_chars: int = DEFAULT_MIN_CHARS,
    include_first: bool = False,
) -> dict[str, Any]:
    """Scan one bundle, including edition scopes, for long front buckets."""
    if min_chars < 0:
        raise ValueError("min_chars must be >= 0")

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
        result = _scan_scope(
            scope_dir,
            manifest_path,
            text_id,
            edition=edition,
            min_chars=min_chars,
            include_first=include_first,
        )
        rows.extend(result["rows"])
        errors.extend(result["errors"])

    rows.sort(key=_row_sort_key)
    for idx, row in enumerate(rows, 1):
        row["id"] = idx
    return {"rows": rows, "errors": errors, "scopes_scanned": scopes_scanned}


def write_overlong_front_report(
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


def _scan_scope(
    scope_dir: Path,
    manifest_path: Path,
    text_id: str,
    *,
    edition: str | None,
    min_chars: int,
    include_first: bool,
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

    title = (manifest.get("metadata") or {}).get("title")
    title_value = title if isinstance(title, str) else None
    for part in (manifest.get("assets") or {}).get("parts") or []:
        if not isinstance(part, dict):
            continue
        seq = part.get("seq")
        filename = part.get("filename")
        if not isinstance(seq, int) or not isinstance(filename, str):
            continue
        if seq == 1 and not include_first:
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

        front = juan.get("front")
        if not isinstance(front, dict):
            continue
        front_text = front.get("text")
        if not isinstance(front_text, str):
            continue
        front_chars = len(front_text)
        if front_chars <= min_chars:
            continue

        body = juan.get("body")
        body_text = body.get("text") if isinstance(body, dict) else None
        body_chars = len(body_text) if isinstance(body_text, str) else None
        rows.append(_row(
            scope_dir=scope_dir,
            path=juan_path,
            text_id=text_id,
            title=title_value,
            edition=edition,
            seq=seq,
            chars=front_chars,
            body_chars=body_chars,
            preview=_preview(front_text),
        ))
    return {"rows": rows, "errors": errors}


def _row(
    *,
    scope_dir: Path,
    path: Path,
    text_id: str,
    title: str | None,
    edition: str | None,
    seq: int,
    chars: int,
    body_chars: int | None,
    preview: str,
) -> dict[str, Any]:
    return {
        "id": 0,
        "problem": "overlong-front-bucket",
        "textid": text_id,
        "title": title,
        "edition": edition,
        "seq": seq,
        "bucket": "front",
        "chars": chars,
        "body_chars": body_chars,
        "path": _rel_path(scope_dir, path),
        "preview": preview,
    }


def _write(rows: list[dict[str, Any]], fh: TextIO) -> None:
    fh.write(f"# bkk-overlong-front version={REPORT_VERSION}\n")
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


def _preview(text: str, limit: int = 40) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."


def _row_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("textid") or "",
        row.get("edition") or "",
        row.get("seq") or 0,
        row.get("chars") or 0,
        row.get("path") or "",
    )
