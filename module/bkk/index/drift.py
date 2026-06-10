"""Drift detection between source files and the .bkki/.bkka indices.

The core index denormalises syntactic-function and semantic-feature codes onto
the ``senses`` table, and the annotation index further copies those labels plus
the sense definition onto every ``annotation_location`` row. When upstream
records change without a rebuild, those denormalised copies go stale.

This check compares ``source_hash`` columns recorded by the indexers against
hashes computed from the current source files. Output is a per-type drift
summary; the exit code is non-zero iff any drift was found.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from .core import _iter_collection, COLLECTIONS


def check_drift(
    *,
    core_root: Path | None,
    core_index: Path | None,
    annotations_root: Path | None,
    annotations_index: Path | None,
) -> int:
    drift_total = 0
    if core_root is not None and core_index is not None and core_index.exists():
        drift_total += _check_core(core_root, core_index)
    elif core_root is None or core_index is None:
        print("skipping core check (core_root or core_index unset)")
    else:
        print(f"skipping core check ({core_index} not found)")

    if (
        annotations_root is not None
        and annotations_index is not None
        and annotations_index.exists()
    ):
        drift_total += _check_annotations(annotations_root, annotations_index)
    elif annotations_root is None or annotations_index is None:
        print("skipping annotations check (root or index unset)")
    else:
        print(f"skipping annotations check ({annotations_index} not found)")

    if drift_total == 0:
        print("OK: no drift detected")
        return 0
    print(f"DRIFT: {drift_total} record(s) need reindex")
    return 1


def _check_core(core_root: Path, core_index: Path) -> int:
    conn = sqlite3.connect(f"file:{core_index}?mode=ro", uri=True)
    try:
        indexed = {
            uuid: (rel_path, source_hash)
            for uuid, rel_path, source_hash in conn.execute(
                "SELECT uuid, path, source_hash FROM notes"
            )
        }
    finally:
        conn.close()

    by_type_drift: dict[str, int] = {}
    seen_uuids: set[str] = set()
    missing_on_disk = 0

    for coll_dir, type_name in COLLECTIONS:
        coll_root = core_root / coll_dir
        if not coll_root.is_dir():
            continue
        for yml_path in _iter_collection(coll_root):
            rel = yml_path.relative_to(core_root).as_posix()
            uuid = yml_path.stem
            seen_uuids.add(uuid)
            indexed_row = indexed.get(uuid)
            if indexed_row is None:
                by_type_drift[type_name] = by_type_drift.get(type_name, 0) + 1
                continue
            indexed_rel, indexed_hash = indexed_row
            current_hash = hashlib.sha1(yml_path.read_bytes()).hexdigest()
            if current_hash != indexed_hash or indexed_rel != rel:
                by_type_drift[type_name] = by_type_drift.get(type_name, 0) + 1

    for uuid in indexed:
        if uuid not in seen_uuids:
            missing_on_disk += 1

    print(f"core: {core_index}")
    if not by_type_drift and missing_on_disk == 0:
        print("  no drift")
    else:
        for type_name, count in sorted(by_type_drift.items()):
            print(f"  {type_name}: {count} drifted")
        if missing_on_disk:
            print(f"  removed from disk: {missing_on_disk}")
    return sum(by_type_drift.values()) + missing_on_disk


def _check_annotations(annotations_root: Path, annotations_index: Path) -> int:
    conn = sqlite3.connect(f"file:{annotations_index}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT text_id, juan_seq, annotation_id, source_hash "
            "FROM annotation_location"
        ).fetchall()
    finally:
        conn.close()
    indexed = {
        (text_id, juan_seq, annotation_id): source_hash
        for text_id, juan_seq, annotation_id, source_hash in rows
    }

    seen: set[tuple[str, int, str | None]] = set()
    drift = 0
    for jsonl_path in sorted(annotations_root.glob("*/*.ann.jsonl")):
        text_id = jsonl_path.parent.name
        stem = jsonl_path.name.removesuffix(".ann.jsonl")
        try:
            seq = int(stem.rsplit("_", 1)[-1])
        except ValueError:
            continue
        for raw_line, ann_id in _iter_annotation_lines(jsonl_path):
            key = (text_id, seq, ann_id)
            seen.add(key)
            indexed_hash = indexed.get(key)
            if indexed_hash is None:
                drift += 1
                continue
            current_hash = hashlib.sha1(raw_line.encode("utf-8")).hexdigest()
            if current_hash != indexed_hash:
                drift += 1

    removed = sum(1 for key in indexed if key not in seen)
    print(f"annotations: {annotations_index}")
    if drift == 0 and removed == 0:
        print("  no drift")
    else:
        if drift:
            print(f"  annotation: {drift} drifted")
        if removed:
            print(f"  removed from disk: {removed}")
    return drift + removed


def _iter_annotation_lines(path: Path):
    import json
    with path.open(encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                raw = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            if raw.get("curation_state") in {"rejected", "superseded"}:
                continue
            ann_id = raw.get("id") if isinstance(raw.get("id"), str) else None
            yield stripped, ann_id
