"""Read and write precomputed voice problem reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TextIO

import yaml

from bkk.index.merge import find_bundle
from bkk.marker_assets import VALID_BUCKETS, effective_markers_for_bucket, load_marker_asset

VOICE_PROBLEM_TYPE = "voice:problem"
REPORT_VERSION = 1


class VoiceProblemReportError(ValueError):
    pass


def find_voice_problems(
    corpus_root: Path | str,
    *,
    text_id: str | None = None,
) -> list[dict[str, Any]]:
    root = Path(corpus_root)
    rows: list[dict[str, Any]] = []
    bundle_dirs = [_bundle_dir_for_text_id(root, text_id)] if text_id else sorted(_bundle_dirs(root))
    for bundle_dir in bundle_dirs:
        textid = bundle_dir.name
        manifest_path = bundle_dir / f"{textid}.manifest.yaml"
        rows.extend(_scope_problems(bundle_dir, manifest_path, textid, edition=None))

        editions_root = bundle_dir / "editions"
        if editions_root.is_dir():
            for sub in sorted(editions_root.iterdir()):
                if not sub.is_dir():
                    continue
                ed_manifest = sub / f"{textid}-{sub.name}.manifest.yaml"
                if ed_manifest.exists():
                    rows.extend(
                        _scope_problems(sub, ed_manifest, textid, edition=sub.name)
                    )
    rows.sort(key=lambda r: (
        r["textid"], r["edition"] or "", r["seq"], r["bucket"], r["offset"],
        r["marker_id"],
    ))
    for idx, row in enumerate(rows, 1):
        row["id"] = idx
    return rows


def _bundle_dir_for_text_id(root: Path, text_id: str) -> Path:
    bundle_dir = find_bundle(root, text_id)
    if bundle_dir is None:
        raise FileNotFoundError(
            f"bundle directory not found for {text_id!r} under {root}"
        )
    return bundle_dir


def write_voice_problems_report(
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


def update_voice_problems_report(
    path: Path | str,
    *,
    text_id: str,
    rows: list[dict[str, Any]],
) -> None:
    report = Path(path)
    existing = read_voice_problems_report(report) if report.exists() else []
    merged = [
        row for row in existing
        if row.get("textid") != text_id
    ] + rows
    merged.sort(key=lambda r: (
        r.get("textid") or "",
        r.get("edition") or "",
        r.get("seq") or 0,
        r.get("bucket") or "",
        r.get("offset") or 0,
        r.get("marker_id") or "",
    ))
    for idx, row in enumerate(merged, 1):
        row["id"] = idx
    write_voice_problems_report(merged, report)


def read_voice_problems_report(path: Path | str) -> list[dict[str, Any]]:
    report = Path(path)
    with report.open("r", encoding="utf-8") as fh:
        first = fh.readline().strip()
        expected = f"# bkk-voice-problems version={REPORT_VERSION}"
        if first != expected:
            raise VoiceProblemReportError(
                f"{report}: invalid voice problem report header "
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
                raise VoiceProblemReportError(
                    f"{report}:{line_no}: invalid JSON: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise VoiceProblemReportError(
                    f"{report}:{line_no}: row is not an object"
                )
            rows.append(row)
    return rows


def _write(rows: list[dict[str, Any]], fh: TextIO) -> None:
    fh.write(f"# bkk-voice-problems version={REPORT_VERSION}\n")
    for row in rows:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
        fh.write("\n")


def _bundle_dirs(root: Path) -> list[Path]:
    out: list[Path] = []
    for path in root.rglob("*.manifest.yaml"):
        if not path.is_file():
            continue
        bundle_dir = path.parent
        textid = bundle_dir.name
        if path.name == f"{textid}.manifest.yaml":
            out.append(bundle_dir)
    return out


def _scope_problems(
    scope_dir: Path,
    manifest_path: Path,
    textid: str,
    *,
    edition: str | None,
) -> list[dict[str, Any]]:
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except OSError:
        return []
    if not isinstance(manifest, dict):
        return []
    title = ((manifest.get("metadata") or {}).get("title"))
    parts = (manifest.get("assets") or {}).get("parts") or []
    rows: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        seq = part.get("seq")
        filename = part.get("filename")
        if not isinstance(seq, int) or not isinstance(filename, str):
            continue
        juan_path = scope_dir / filename
        try:
            juan = yaml.safe_load(juan_path.read_text(encoding="utf-8")) or {}
        except OSError:
            continue
        if not isinstance(juan, dict):
            continue
        marker_asset = load_marker_asset(scope_dir, manifest, seq)
        for bucket in VALID_BUCKETS:
            for marker in effective_markers_for_bucket(juan, bucket, marker_asset):
                if not isinstance(marker, dict):
                    continue
                if marker.get("type") != VOICE_PROBLEM_TYPE:
                    continue
                rows.append(_row(
                    textid=textid,
                    title=title if isinstance(title, str) else None,
                    edition=edition,
                    seq=seq,
                    bucket=bucket,
                    marker=marker,
                ))
    return rows


def _row(
    *,
    textid: str,
    title: str | None,
    edition: str | None,
    seq: int,
    bucket: str,
    marker: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": 0,
        "textid": textid,
        "title": title,
        "edition": edition,
        "seq": seq,
        "bucket": bucket,
        "offset": marker.get("offset") if isinstance(marker.get("offset"), int) else 0,
        "length": marker.get("length") if isinstance(marker.get("length"), int) else 0,
        "marker_id": marker.get("id") if isinstance(marker.get("id"), str) else "",
        "source": marker.get("source") if isinstance(marker.get("source"), str) else None,
        "code": marker.get("code") if isinstance(marker.get("code"), str) else None,
        "message": marker.get("message") if isinstance(marker.get("message"), str) else "",
    }
