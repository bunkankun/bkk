"""Marker-ID baseline snapshot + drift check.

See ``docs/bkk-marker-ids.md`` for the contract these helpers enforce.

Public surface:

- :func:`gather_marker_ids` walks a bundle directory and returns the
  ``{seq: [{"id", "type"}, ...]}`` shape for the master and every edition.
- :func:`freeze_marker_ids` writes that shape to the canonical snapshot file
  ``<bundle-dir>/<text-id>.marker-ids.yaml``.
- :func:`validate_marker_ids` compares the current bundle against the
  snapshot and returns a list of :class:`MarkerIdIssue` rows (``missing``,
  ``repurposed``, ``extra``). Empty list = baseline holds.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from bkk.marker_assets import (
    effective_markers_for_bucket,
    load_marker_asset,
    VALID_BUCKETS,
)


SNAPSHOT_SUFFIX = ".marker-ids.yaml"


@dataclass
class MarkerIdIssue:
    kind: str          # "missing" | "repurposed" | "extra"
    scope: str         # "master" or edition short
    seq: int
    id: str
    detail: str = ""


def _snapshot_path(bundle_dir: Path, text_id: str) -> Path:
    return bundle_dir / f"{text_id}{SNAPSHOT_SUFFIX}"


def _read_yaml(path: Path) -> dict | None:
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def _load_manifest(path: Path) -> dict | None:
    return _read_yaml(path)


def _ids_for_juan(juan: dict, marker_asset: dict | None) -> list[dict[str, str]]:
    """Walk every bucket and return ``[{"id", "type"}, ...]`` for markers that
    carry a non-empty id, in stable (offset-sorted) order."""
    out: list[dict[str, str]] = []
    for bucket_name in VALID_BUCKETS:
        bucket = juan.get(bucket_name)
        if not isinstance(bucket, dict):
            continue
        for m in effective_markers_for_bucket(juan, bucket_name, marker_asset):
            if not isinstance(m, dict):
                continue
            mid = m.get("id")
            mtype = m.get("type")
            if isinstance(mid, str) and mid and isinstance(mtype, str):
                out.append({"id": mid, "type": mtype})
    return out


def _gather_scope(
    scope_dir: Path,
    text_id: str,
    manifest: dict | None,
    *,
    edition_short: str | None,
) -> list[dict[str, Any]]:
    """Walk one scope (master or one edition) and collect per-juan ID lists."""
    if not isinstance(manifest, dict):
        return []
    assets = manifest.get("assets") or {}
    parts = assets.get("parts") or []
    juans: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        seq = part.get("seq")
        fname = part.get("filename")
        if not (isinstance(seq, int) and isinstance(fname, str)):
            continue
        juan_path = scope_dir / fname
        juan = _read_yaml(juan_path)
        if not isinstance(juan, dict):
            continue
        marker_asset = load_marker_asset(scope_dir, manifest, seq)
        entry: dict[str, Any] = {"seq": seq, "ids": _ids_for_juan(juan, marker_asset)}
        if edition_short is not None:
            entry["edition"] = edition_short
        juans.append(entry)
    juans.sort(key=lambda e: e["seq"])
    return juans


def gather_marker_ids(bundle_dir: Path) -> dict[str, Any]:
    """Collect marker IDs from every juan in master + each edition."""
    bundle_dir = Path(bundle_dir).resolve()
    text_id = bundle_dir.name
    master_manifest = _load_manifest(bundle_dir / f"{text_id}.manifest.yaml")
    snapshot: dict[str, Any] = {
        "text_id": text_id,
        "master": _gather_scope(
            bundle_dir, text_id, master_manifest, edition_short=None,
        ),
        "editions": {},
    }
    editions_dir = bundle_dir / "editions"
    if editions_dir.is_dir():
        for sub in sorted(editions_dir.iterdir()):
            if not sub.is_dir():
                continue
            short = sub.name
            ed_manifest = _load_manifest(
                sub / f"{text_id}-{short}.manifest.yaml",
            )
            snapshot["editions"][short] = _gather_scope(
                sub, text_id, ed_manifest, edition_short=short,
            )
    return snapshot


def freeze_marker_ids(
    bundle_dir: Path, *, force: bool = False,
) -> Path:
    """Write the snapshot file. Refuses to overwrite unless ``force`` is set."""
    bundle_dir = Path(bundle_dir).resolve()
    text_id = bundle_dir.name
    path = _snapshot_path(bundle_dir, text_id)
    if path.exists() and not force:
        raise FileExistsError(
            f"{path.name} already exists; pass force=True to overwrite",
        )
    snapshot = gather_marker_ids(bundle_dir)
    path.write_text(
        yaml.safe_dump(snapshot, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def _diff_scope(
    scope: str,
    baseline_juans: list[dict[str, Any]],
    current_juans: list[dict[str, Any]],
) -> list[MarkerIdIssue]:
    issues: list[MarkerIdIssue] = []
    cur_by_seq: dict[int, dict[str, str]] = {}
    for entry in current_juans:
        seq = entry.get("seq")
        if not isinstance(seq, int):
            continue
        cur_by_seq[seq] = {
            row["id"]: row["type"]
            for row in entry.get("ids") or []
            if isinstance(row, dict)
            and isinstance(row.get("id"), str)
            and isinstance(row.get("type"), str)
        }

    seen_seqs: set[int] = set()
    for entry in baseline_juans:
        seq = entry.get("seq")
        if not isinstance(seq, int):
            continue
        seen_seqs.add(seq)
        baseline_ids: dict[str, str] = {
            row["id"]: row["type"]
            for row in entry.get("ids") or []
            if isinstance(row, dict)
            and isinstance(row.get("id"), str)
            and isinstance(row.get("type"), str)
        }
        current_ids = cur_by_seq.get(seq, {})
        for mid, mtype in baseline_ids.items():
            if mid not in current_ids:
                issues.append(MarkerIdIssue(
                    kind="missing", scope=scope, seq=seq, id=mid,
                    detail=f"baseline type='{mtype}'",
                ))
            elif current_ids[mid] != mtype:
                issues.append(MarkerIdIssue(
                    kind="repurposed", scope=scope, seq=seq, id=mid,
                    detail=f"baseline='{mtype}' current='{current_ids[mid]}'",
                ))
        for mid in current_ids.keys() - baseline_ids.keys():
            issues.append(MarkerIdIssue(
                kind="extra", scope=scope, seq=seq, id=mid,
                detail=f"type='{current_ids[mid]}'",
            ))

    for seq, current_ids in cur_by_seq.items():
        if seq in seen_seqs:
            continue
        for mid, mtype in current_ids.items():
            issues.append(MarkerIdIssue(
                kind="extra", scope=scope, seq=seq, id=mid,
                detail=f"type='{mtype}' (juan not in baseline)",
            ))
    return issues


def validate_marker_ids(bundle_dir: Path) -> list[MarkerIdIssue]:
    """Return drift issues against the snapshot, or raise if the snapshot is
    missing or malformed."""
    bundle_dir = Path(bundle_dir).resolve()
    text_id = bundle_dir.name
    snap_path = _snapshot_path(bundle_dir, text_id)
    if not snap_path.exists():
        raise FileNotFoundError(
            f"no marker-ids snapshot at {snap_path.name}; "
            f"run --freeze-marker-ids first",
        )
    baseline = _read_yaml(snap_path)
    if not isinstance(baseline, dict):
        raise ValueError(f"{snap_path.name} is not a YAML mapping")

    current = gather_marker_ids(bundle_dir)
    issues: list[MarkerIdIssue] = []
    issues.extend(_diff_scope(
        "master", baseline.get("master") or [], current.get("master") or [],
    ))
    base_editions = baseline.get("editions") or {}
    cur_editions = current.get("editions") or {}
    for short in sorted(set(base_editions) | set(cur_editions)):
        issues.extend(_diff_scope(
            short,
            base_editions.get(short) or [],
            cur_editions.get(short) or [],
        ))
    return issues
