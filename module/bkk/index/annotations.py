"""Build a SQLite index over the local ``bkk-annotations`` archive.

The archive itself remains JSONL, one file per text/juan.  This derived
``.bkka`` index gives the UI a fast "where used" lookup for L2 word senses
without scanning every annotation file on each button click.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from bkk.serialize.uuid import strip_uuid_prefix

log = logging.getLogger("bkk.index.annotations")

ANNOTATION_SCHEMA_VERSION = 2

DDL = """
CREATE TABLE meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE annotation_location (
  sense_uuid TEXT NOT NULL,
  text_id TEXT NOT NULL,
  juan_seq INTEGER NOT NULL,
  bucket TEXT,
  bucket_offset INTEGER NOT NULL,
  length INTEGER,
  marker_id TEXT,
  annotation_id TEXT,
  concept TEXT,
  concept_id TEXT,
  orth TEXT,
  pron TEXT,
  sense_def TEXT,
  note TEXT,
  translation_title TEXT,
  translation_text TEXT,
  resp TEXT,
  curation_state TEXT,
  rating INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_annotation_location_sense
  ON annotation_location(sense_uuid, text_id, juan_seq, bucket_offset, annotation_id);
CREATE INDEX idx_annotation_location_text
  ON annotation_location(text_id, juan_seq, bucket_offset);
"""


def build_annotation_index(
    annotations_root: Path | str,
    out_path: Path | str | None = None,
) -> Path:
    """Build ``_annotations.bkka`` from ``<annotations_root>/**/*.ann.jsonl``."""
    root = Path(annotations_root)
    if not root.is_dir():
        raise FileNotFoundError(f"annotations root not found: {root}")
    out = Path(out_path) if out_path is not None else root / "_annotations.bkka"
    if out.exists():
        out.unlink()
    out.parent.mkdir(parents=True, exist_ok=True)

    rows = list(iter_annotation_location_rows(root))
    conn = sqlite3.connect(str(out))
    try:
        conn.executescript(DDL)
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [
                ("schema_version", str(ANNOTATION_SCHEMA_VERSION)),
                ("kind", "annotation_locations"),
                ("annotations_root", str(root)),
            ],
        )
        conn.executemany(
            "INSERT INTO annotation_location"
            "(sense_uuid, text_id, juan_seq, bucket, bucket_offset, length, "
            "marker_id, annotation_id, concept, concept_id, orth, pron, sense_def, note, "
            "translation_title, translation_text, resp, curation_state, rating) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return out


def iter_annotation_location_rows(root: Path | str) -> Iterable[tuple[Any, ...]]:
    root = Path(root)
    for jsonl_path in sorted(root.glob("*/*.ann.jsonl")):
        text_id = jsonl_path.parent.name
        seq = _seq_from_path(jsonl_path)
        if seq is None:
            continue
        for raw in _read_raw_records(jsonl_path):
            row = _location_row(raw, text_id, seq)
            if row is not None:
                yield row


def annotation_index_schema_version(path: Path | str) -> int | None:
    try:
        conn = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return None
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def _seq_from_path(path: Path) -> int | None:
    stem = path.name.removesuffix(".ann.jsonl")
    try:
        return int(stem.rsplit("_", 1)[-1])
    except ValueError:
        log.warning("annotation archive filename lacks juan seq: %s", path)
        return None


def _read_raw_records(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                log.warning("skipping malformed annotation JSON at %s:%s", path, line_no)
                continue
            if isinstance(raw, dict):
                yield raw


def _location_row(raw: dict[str, Any], text_id: str, seq: int) -> tuple[Any, ...] | None:
    state = raw.get("curation_state")
    if state in {"rejected", "superseded"}:
        return None
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        return None
    sense = payload.get("sense")
    if not isinstance(sense, dict):
        return None
    sense_uuid = sense.get("id")
    if not isinstance(sense_uuid, str) or not sense_uuid:
        return None
    sense_uuid = strip_uuid_prefix(sense_uuid)
    bucket_offset = raw.get("bucket_offset")
    if not isinstance(bucket_offset, int):
        return None
    anchor = raw.get("anchor")
    if not isinstance(anchor, dict):
        anchor = {}
    form = payload.get("form")
    if not isinstance(form, dict):
        form = {}
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    translation = payload.get("translation")
    if not isinstance(translation, dict):
        translation = {}
    note = metadata.get("note") if isinstance(metadata.get("note"), str) else None
    if note is None and isinstance(payload.get("note"), str):
        note = payload.get("note")
    length = anchor.get("length")
    rating_raw = raw.get("rating")
    rating = rating_raw if isinstance(rating_raw, int) and rating_raw in (0, 1, 2) else 0
    return (
        sense_uuid,
        text_id,
        seq,
        raw.get("bucket") if isinstance(raw.get("bucket"), str) else None,
        bucket_offset,
        length if isinstance(length, int) else None,
        anchor.get("marker_id") if isinstance(anchor.get("marker_id"), str) else None,
        raw.get("id") if isinstance(raw.get("id"), str) else None,
        payload.get("concept") if isinstance(payload.get("concept"), str) else None,
        payload.get("concept_id") if isinstance(payload.get("concept_id"), str) else None,
        form.get("orth") if isinstance(form.get("orth"), str) else None,
        form.get("pron") if isinstance(form.get("pron"), str) else None,
        sense.get("def") if isinstance(sense.get("def"), str) else None,
        note,
        translation.get("title") if isinstance(translation.get("title"), str) else None,
        translation.get("text") if isinstance(translation.get("text"), str) else None,
        metadata.get("resp") if isinstance(metadata.get("resp"), str) else None,
        state if isinstance(state, str) else None,
        rating,
    )


