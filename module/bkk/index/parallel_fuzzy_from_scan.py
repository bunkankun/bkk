"""Fuzzy refinement of exact ``parallel-scan`` JSONL candidates."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import TextIO

from .parallel import (
    ParallelCluster,
    _BucketCache,
    _clusters_from_spans_fuzzy,
    _maximal_pair_span_fuzzy,
)


def discover_fuzzy_from_scan(
    index_path: Path | str,
    scan_path: Path | str,
    *,
    max_edits: int = 1,
    min_length: int = 24,
    min_occurrences: int = 2,
    include_contained: bool = False,
    context: int = 20,
    progress: TextIO | None = None,
) -> list[ParallelCluster]:
    """Extend exact scan clusters into fuzzy clusters using the index text.

    The JSONL scan report supplies candidate anchors. The ``.bkkx`` index is
    still required because the JSONL locations are intentionally portable and
    do not carry bucket ids or full surrounding text.
    """
    _validate_args(max_edits=max_edits, min_length=min_length, min_occurrences=min_occurrences)
    conn = sqlite3.connect(f"file:{Path(index_path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(
            """
            CREATE TEMP TABLE parallel_pair_span (
              bucket_a INTEGER NOT NULL,
              start_a  INTEGER NOT NULL,
              end_a    INTEGER NOT NULL,
              bucket_b INTEGER NOT NULL,
              start_b  INTEGER NOT NULL,
              end_b    INTEGER NOT NULL,
              edits    INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (bucket_a, start_a, end_a, bucket_b, start_b, end_b)
            );
            """
        )
        cache = _BucketCache(conn)
        resolver = _BucketResolver(conn)
        clusters_seen = 0
        pairs_seen = 0
        spans_written = 0
        for record in _read_scan_jsonl(scan_path):
            clusters_seen += 1
            spans, pairs = _record_scan_cluster_spans(
                conn,
                cache,
                resolver,
                record,
                max_edits=max_edits,
                min_length=min_length,
            )
            pairs_seen += pairs
            spans_written += spans
            if progress is not None and clusters_seen % 1000 == 0:
                _emit(
                    progress,
                    f"fuzzy-from-scan: {clusters_seen} clusters, "
                    f"{pairs_seen} pairs, {spans_written} spans",
                )
        _emit(
            progress,
            f"fuzzy-from-scan candidates: {clusters_seen} clusters, "
            f"{pairs_seen} pairs, {spans_written} spans",
        )
        clusters = _clusters_from_spans_fuzzy(
            conn,
            cache,
            max_edits=max_edits,
            min_occurrences=min_occurrences,
            include_contained=include_contained,
            context=context,
        )
        _emit(progress, f"fuzzy-from-scan done: {len(clusters)} clusters")
        return clusters
    finally:
        conn.close()


def _validate_args(*, max_edits: int, min_length: int, min_occurrences: int) -> None:
    if max_edits < 0 or max_edits > 4:
        raise ValueError("max_edits must be between 0 and 4")
    if min_length < 1:
        raise ValueError("min_length must be positive")
    if min_occurrences < 2:
        raise ValueError("min_occurrences must be at least 2")


def _read_scan_jsonl(path: Path | str) -> Iterable[dict]:
    with Path(path).open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{lineno}: expected JSON object")
            yield record


class _BucketResolver:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._cache: dict[tuple[str, int, str], int] = {}

    def bucket_id(self, loc: dict) -> int:
        try:
            key = (
                str(loc["textid"]),
                int(loc["juan_seq"]),
                str(loc["bucket"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed scan location: {loc!r}") from exc
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        row = self._conn.execute(
            "SELECT b.bucket_id "
            "FROM bucket b JOIN juan j ON b.juan_id = j.juan_id "
            "WHERE j.textid = ? AND j.seq = ? AND b.kind = ?",
            key,
        ).fetchone()
        if row is None:
            raise ValueError(
                "scan location not found in index: "
                f"{key[0]} juan {key[1]} bucket {key[2]}"
            )
        bucket_id = int(row["bucket_id"])
        self._cache[key] = bucket_id
        return bucket_id


def _record_scan_cluster_spans(
    conn: sqlite3.Connection,
    cache: _BucketCache,
    resolver: _BucketResolver,
    record: dict,
    *,
    max_edits: int,
    min_length: int,
) -> tuple[int, int]:
    locs = record.get("locations")
    if not isinstance(locs, list):
        raise ValueError("scan record missing locations list")
    pairs_seen = 0
    spans_written = 0
    resolved = [_resolve_location(resolver, loc) for loc in locs]
    for i, left in enumerate(resolved):
        for right in resolved[i + 1:]:
            pairs_seen += 1
            seed_length = min(left[2] - left[1], right[2] - right[1])
            if seed_length < 1:
                continue
            span_a, span_b, _edits = _maximal_pair_span_fuzzy(
                cache,
                left[0],
                left[1],
                right[0],
                right[1],
                seed_length,
                max_edits,
            )
            if span_a is None or span_b is None:
                continue
            if span_a.end - span_a.start < min_length:
                continue
            before = conn.total_changes
            conn.execute(
                "INSERT OR IGNORE INTO temp.parallel_pair_span"
                "(bucket_a, start_a, end_a, bucket_b, start_b, end_b, edits) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    span_a.bucket_id,
                    span_a.start,
                    span_a.end,
                    span_b.bucket_id,
                    span_b.start,
                    span_b.end,
                    _edits,
                ),
            )
            spans_written += conn.total_changes - before
    return spans_written, pairs_seen


def _resolve_location(
    resolver: _BucketResolver,
    loc: object,
) -> tuple[int, int, int]:
    if not isinstance(loc, dict):
        raise ValueError(f"malformed scan location: {loc!r}")
    try:
        start = int(loc["start"])
        end = int(loc["end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"malformed scan location offsets: {loc!r}") from exc
    if start < 0 or end <= start:
        raise ValueError(f"invalid scan location offsets: {loc!r}")
    return resolver.bucket_id(loc), start, end


def _emit(progress: TextIO | None, message: str) -> None:
    if progress is None:
        return
    progress.write(message + "\n")
    progress.flush()
