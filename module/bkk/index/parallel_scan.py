"""External-memory exact parallel-passage discovery.

This scanner is deliberately separate from the trigram/seed finder in
``parallel.py``. It streams bucket text, writes longer winnowed fingerprints to
partition files, then processes one partition at a time so corpus-scale runs do
not require all anchors or candidate pairs in RAM.
"""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import time
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from .parallel import (
    ParallelCluster,
    _BucketCache,
    _make_location,
    _maximal_pair_span,
    _remove_contained_clusters,
    _sha256,
    _span_sort_key,
)


@dataclass(frozen=True)
class ParallelScanStats:
    """Counters collected during an external-memory scan."""

    bucket_count: int
    anchors_written: int
    partitions: int
    skipped_anchor_groups: int
    candidate_spans: int
    clusters: int


def discover_parallel_passages_scan(
    index_path: Path | str,
    *,
    bucket: str = "body",
    min_length: int = 24,
    anchor_length: int = 12,
    min_occurrences: int = 2,
    max_anchor_occurrences: int = 200,
    partitions: int = 256,
    work_dir: Path | str | None = None,
    include_contained: bool = False,
    context: int = 20,
    progress: TextIO | None = None,
) -> tuple[list[ParallelCluster], ParallelScanStats]:
    """Discover exact repeated passages using external-memory fingerprints."""
    _validate_scan_args(
        bucket=bucket,
        min_length=min_length,
        anchor_length=anchor_length,
        min_occurrences=min_occurrences,
        max_anchor_occurrences=max_anchor_occurrences,
        partitions=partitions,
    )
    work_root = Path(work_dir) if work_dir is not None else _default_work_dir(index_path)
    work_root.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="bkk-parallel-", dir=work_root) as tmp:
        run_dir = Path(tmp)
        index_conn = sqlite3.connect(f"file:{Path(index_path)}?mode=ro", uri=True)
        index_conn.row_factory = sqlite3.Row
        work_conn = sqlite3.connect(str(run_dir / "work.sqlite3"))
        work_conn.row_factory = sqlite3.Row
        try:
            index_conn.execute("PRAGMA temp_store = FILE")
            work_conn.execute("PRAGMA temp_store = FILE")
            work_conn.execute(f"PRAGMA temp_store_directory = '{run_dir}'")
            work_conn.execute("PRAGMA cache_size = -200000")
            _init_work_db(work_conn)

            bucket_count = _count_buckets(index_conn, bucket, min_length)
            _emit(progress, f"selected buckets: {bucket_count}")
            anchors_written = _write_anchor_partitions(
                index_conn,
                run_dir,
                bucket=bucket,
                min_length=min_length,
                anchor_length=anchor_length,
                partitions=partitions,
                progress=progress,
            )
            _emit(progress, f"anchors written: {anchors_written}")

            cache = _BucketCache(index_conn)
            skipped_anchor_groups, candidate_spans = _process_partitions(
                work_conn,
                cache,
                run_dir,
                min_length=min_length,
                anchor_length=anchor_length,
                max_anchor_occurrences=max_anchor_occurrences,
                partitions=partitions,
                progress=progress,
            )
            clusters = _clusters_from_work_spans(
                work_conn,
                index_conn,
                cache,
                min_occurrences=min_occurrences,
                include_contained=include_contained,
                context=context,
            )
            stats = ParallelScanStats(
                bucket_count=bucket_count,
                anchors_written=anchors_written,
                partitions=partitions,
                skipped_anchor_groups=skipped_anchor_groups,
                candidate_spans=candidate_spans,
                clusters=len(clusters),
            )
            _emit(
                progress,
                "done: "
                f"{candidate_spans} spans, {len(clusters)} clusters "
                f"({time.monotonic() - t0:.1f}s)",
            )
            return clusters, stats
        finally:
            work_conn.close()
            index_conn.close()


def _validate_scan_args(
    *,
    bucket: str,
    min_length: int,
    anchor_length: int,
    min_occurrences: int,
    max_anchor_occurrences: int,
    partitions: int,
) -> None:
    if bucket not in {"front", "body", "back", "all"}:
        raise ValueError("bucket must be one of: front, body, back, all")
    if min_length < 1:
        raise ValueError("min_length must be positive")
    if anchor_length < 1:
        raise ValueError("anchor_length must be positive")
    if anchor_length > min_length:
        raise ValueError("anchor_length must be less than or equal to min_length")
    if min_occurrences < 2:
        raise ValueError("min_occurrences must be at least 2")
    if max_anchor_occurrences < 2:
        raise ValueError("max_anchor_occurrences must be at least 2")
    if partitions < 1:
        raise ValueError("partitions must be positive")


def _default_work_dir(index_path: Path | str) -> Path:
    index_parent = Path(index_path).resolve().parent
    return index_parent


def _init_work_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE candidate_span (
          bucket_a INTEGER NOT NULL,
          start_a  INTEGER NOT NULL,
          end_a    INTEGER NOT NULL,
          bucket_b INTEGER NOT NULL,
          start_b  INTEGER NOT NULL,
          end_b    INTEGER NOT NULL,
          PRIMARY KEY (bucket_a, start_a, end_a, bucket_b, start_b, end_b)
        );
        CREATE TABLE anchor_occurrence (
          hash      TEXT NOT NULL,
          bucket_id INTEGER NOT NULL,
          position  INTEGER NOT NULL
        );
        CREATE INDEX idx_anchor_occurrence_hash ON anchor_occurrence(hash);
        """
    )


def _bucket_where(bucket: str, min_text_length: int) -> tuple[str, tuple]:
    if bucket == "all":
        return "length(text) >= ?", (min_text_length,)
    return "kind = ? AND length(text) >= ?", (bucket, min_text_length)


def _count_buckets(conn: sqlite3.Connection, bucket: str, min_text_length: int) -> int:
    where, params = _bucket_where(bucket, min_text_length)
    return conn.execute(f"SELECT COUNT(*) FROM bucket WHERE {where}", params).fetchone()[0]


def _write_anchor_partitions(
    conn: sqlite3.Connection,
    run_dir: Path,
    *,
    bucket: str,
    min_length: int,
    anchor_length: int,
    partitions: int,
    progress: TextIO | None,
) -> int:
    partition_dir = run_dir / "anchors"
    partition_dir.mkdir()
    handles = [
        (partition_dir / f"part-{i:04d}.tsv").open("w", encoding="utf-8")
        for i in range(partitions)
    ]
    anchors_written = 0
    buckets_seen = 0
    where, params = _bucket_where(bucket, min_length)
    try:
        rows = conn.execute(
            f"SELECT bucket_id, text FROM bucket WHERE {where} ORDER BY bucket_id",
            params,
        )
        for row in rows:
            buckets_seen += 1
            for pos, h in _winnowed_anchors(
                row["text"],
                anchor_length=anchor_length,
                min_length=min_length,
            ):
                part = int(h[:8], 16) % partitions
                handles[part].write(f"{h}\t{row['bucket_id']}\t{pos}\n")
                anchors_written += 1
            if buckets_seen % 1000 == 0:
                _emit(
                    progress,
                    f"anchor pass: {buckets_seen} buckets, "
                    f"{anchors_written} anchors",
                )
    finally:
        for handle in handles:
            handle.close()
    return anchors_written


def _winnowed_anchors(
    text: str,
    *,
    anchor_length: int,
    min_length: int,
) -> Iterator[tuple[int, str]]:
    if len(text) < min_length:
        return
    window = min_length - anchor_length + 1
    mins: deque[tuple[int, str]] = deque()
    last_selected: int | None = None
    for pos in range(0, len(text) - anchor_length + 1):
        h = _anchor_hash(text[pos:pos + anchor_length])
        while mins and _winnow_key(pos, h) <= _winnow_key(mins[-1][0], mins[-1][1]):
            mins.pop()
        mins.append((pos, h))
        window_start = pos - window + 1
        while mins and mins[0][0] < window_start:
            mins.popleft()
        if pos >= window - 1 and mins:
            selected_pos, selected_hash = mins[0]
            if selected_pos != last_selected:
                yield selected_pos, selected_hash
                last_selected = selected_pos


def _winnow_key(pos: int, h: str) -> tuple[str, int]:
    # Pick the minimum hash; ties choose the rightmost occurrence.
    return h, -pos


def _anchor_hash(text: str) -> str:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()


def _process_partitions(
    work_conn: sqlite3.Connection,
    cache: _BucketCache,
    run_dir: Path,
    *,
    min_length: int,
    anchor_length: int,
    max_anchor_occurrences: int,
    partitions: int,
    progress: TextIO | None,
) -> tuple[int, int]:
    skipped_anchor_groups = 0
    candidate_spans = 0
    partition_dir = run_dir / "anchors"
    for part in range(partitions):
        path = partition_dir / f"part-{part:04d}.tsv"
        if not path.exists() or path.stat().st_size == 0:
            continue
        work_conn.execute("DELETE FROM anchor_occurrence")
        _load_partition(work_conn, path)
        work_conn.commit()
        groups = work_conn.execute(
            "SELECT hash, COUNT(*) AS n FROM anchor_occurrence "
            "GROUP BY hash HAVING n >= 2 ORDER BY hash"
        ).fetchall()
        for group in groups:
            n = group["n"]
            if n > max_anchor_occurrences:
                skipped_anchor_groups += 1
                continue
            postings = work_conn.execute(
                "SELECT bucket_id, position FROM anchor_occurrence "
                "WHERE hash = ? ORDER BY bucket_id, position",
                (group["hash"],),
            ).fetchall()
            candidate_spans += _record_group_spans(
                work_conn,
                cache,
                postings,
                min_length=min_length,
                anchor_length=anchor_length,
            )
        work_conn.commit()
        _emit(
            progress,
            f"partition {part + 1}/{partitions}: "
            f"{len(groups)} repeated anchors, "
            f"{skipped_anchor_groups} skipped groups, "
            f"{candidate_spans} spans",
        )
    return skipped_anchor_groups, candidate_spans


def _load_partition(conn: sqlite3.Connection, path: Path) -> None:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            h, bucket_id, pos = line.rstrip("\n").split("\t")
            rows.append((h, int(bucket_id), int(pos)))
            if len(rows) >= 10000:
                conn.executemany(
                    "INSERT INTO anchor_occurrence(hash, bucket_id, position) "
                    "VALUES (?,?,?)",
                    rows,
                )
                rows.clear()
    if rows:
        conn.executemany(
            "INSERT INTO anchor_occurrence(hash, bucket_id, position) VALUES (?,?,?)",
            rows,
        )


def _record_group_spans(
    conn: sqlite3.Connection,
    cache: _BucketCache,
    postings: list[sqlite3.Row],
    *,
    min_length: int,
    anchor_length: int,
) -> int:
    rows = []
    for i, left in enumerate(postings):
        for right in postings[i + 1:]:
            span_a, span_b = _maximal_pair_span(
                cache,
                left["bucket_id"],
                left["position"],
                right["bucket_id"],
                right["position"],
                anchor_length,
            )
            if span_a is None or span_b is None:
                continue
            if span_a.end - span_a.start < min_length:
                continue
            rows.append((
                span_a.bucket_id,
                span_a.start,
                span_a.end,
                span_b.bucket_id,
                span_b.start,
                span_b.end,
            ))
    if not rows:
        return 0
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO candidate_span"
        "(bucket_a, start_a, end_a, bucket_b, start_b, end_b) "
        "VALUES (?,?,?,?,?,?)",
        rows,
    )
    return conn.total_changes - before


def _clusters_from_work_spans(
    work_conn: sqlite3.Connection,
    index_conn: sqlite3.Connection,
    cache: _BucketCache,
    *,
    min_occurrences: int,
    include_contained: bool,
    context: int,
) -> list[ParallelCluster]:
    grouped: dict[tuple[str, int], dict[str, object]] = {}
    for row in work_conn.execute(
        "SELECT bucket_a, start_a, end_a, bucket_b, start_b, end_b "
        "FROM candidate_span "
        "ORDER BY (end_a - start_a) DESC, bucket_a, start_a, bucket_b, start_b"
    ):
        info_a = cache.get(row["bucket_a"])
        text = info_a.text[row["start_a"]:row["end_a"]]
        key = (_sha256(text), len(text))
        entry = grouped.setdefault(key, {"text": text, "spans": set()})
        spans = entry["spans"]
        assert isinstance(spans, set)
        spans.add((row["bucket_a"], row["start_a"], row["end_a"]))
        spans.add((row["bucket_b"], row["start_b"], row["end_b"]))

    raw_clusters: list[tuple[str, int, set[tuple[int, int, int]]]] = []
    for entry in grouped.values():
        spans = entry["spans"]
        text = entry["text"]
        assert isinstance(spans, set)
        assert isinstance(text, str)
        if len(spans) >= min_occurrences:
            raw_clusters.append((text, len(text), spans))
    raw_clusters.sort(key=lambda c: (-c[1], c[0], _span_sort_key(c[2])))

    if not include_contained:
        raw_clusters = _remove_contained_clusters(raw_clusters, min_occurrences)

    clusters = []
    for idx, (text, length, spans) in enumerate(raw_clusters, 1):
        locations = tuple(
            _make_location(index_conn, cache, bucket_id, start, end, context)
            for bucket_id, start, end in sorted(spans)
        )
        clusters.append(
            ParallelCluster(
                cluster_id=f"parallel-{idx:06d}",
                length=length,
                occurrence_count=len(locations),
                text=text,
                locations=locations,
            )
        )
    return clusters


def _emit(progress: TextIO | None, message: str) -> None:
    if progress is None:
        return
    progress.write(message + "\n")
    progress.flush()
