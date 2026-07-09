"""External-memory exact parallel-passage discovery.

This scanner is deliberately separate from the trigram/seed finder in
``parallel.py``. It streams bucket text, writes longer winnowed fingerprints to
partition files, then processes one partition at a time so corpus-scale runs do
not require all anchors or candidate pairs in RAM.
"""

from __future__ import annotations

import json
import hashlib
import sqlite3
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from .build import compute_bkkx_hash
from .parallel import (
    ParallelCluster,
    _BucketCache,
    _make_location,
    _maximal_pair_span,
    _remove_contained_clusters,
    _sha256,
    _span_sort_key,
)


_WORK_DB_VERSION = 1
_WORK_DB_ALGORITHM = "parallel-scan-v2"
_HEARTBEAT_SECONDS = 60.0
_GROUP_HEARTBEAT_SECONDS = 60.0
_ANCHOR_HEARTBEAT_CHECK_INTERVAL = 10000
_PAIR_HEARTBEAT_CHECK_INTERVAL = 10000


@dataclass(frozen=True)
class ParallelScanStats:
    """Counters collected during an external-memory scan."""

    bucket_count: int
    anchors_written: int
    partitions: int
    skipped_anchor_groups: int
    candidate_spans: int
    clusters: int
    anchor_seconds: float = 0.0
    partition_seconds: float = 0.0
    cluster_seconds: float = 0.0
    total_seconds: float = 0.0


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
    work_db: Path | str | None = None,
    force_work_db: bool = False,
    jobs: int = 1,
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
        jobs=jobs,
    )
    index_path = Path(index_path)
    work_root = Path(work_dir) if work_dir is not None else _default_work_dir(index_path)
    work_root.mkdir(parents=True, exist_ok=True)
    work_db_path = Path(work_db) if work_db is not None else None

    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="bkk-parallel-", dir=work_root) as tmp:
        run_dir = Path(tmp)
        index_conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
        index_conn.row_factory = sqlite3.Row
        expected_meta = (
            _expected_work_meta(
                index_path,
                index_conn,
                bucket=bucket,
                min_length=min_length,
                anchor_length=anchor_length,
                max_anchor_occurrences=max_anchor_occurrences,
                partitions=partitions,
            )
            if work_db_path is not None else None
        )
        reject_reason = None
        if work_db_path is not None and work_db_path.exists() and not force_work_db:
            reused, reject_reason = _try_reuse_work_db(
                work_db_path,
                expected_meta or {},
                index_conn,
                min_occurrences=min_occurrences,
                include_contained=include_contained,
                context=context,
                progress=progress,
                started_at=t0,
            )
            if reused is not None:
                index_conn.close()
                return reused
        if work_db_path is not None and work_db_path.exists():
            if not force_work_db:
                reason = f": {reject_reason}" if reject_reason else ""
                index_conn.close()
                raise ValueError(f"work DB is not reusable{reason}: {work_db_path}")
            work_db_path.unlink()
        if work_db_path is not None:
            work_db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path = work_db_path or (run_dir / "work.sqlite3")
        work_conn = sqlite3.connect(str(db_path))
        work_conn.row_factory = sqlite3.Row
        try:
            index_conn.execute("PRAGMA temp_store = FILE")
            work_conn.execute("PRAGMA temp_store = FILE")
            work_conn.execute(f"PRAGMA temp_store_directory = '{run_dir}'")
            work_conn.execute("PRAGMA cache_size = -200000")
            _init_work_db(work_conn)
            if expected_meta is not None:
                _write_work_meta(work_conn, expected_meta, status="running")

            bucket_count = _count_buckets(index_conn, bucket, min_length)
            _emit(progress, f"selected buckets: {bucket_count}")
            t_anchor = time.monotonic()
            anchors_written = _write_anchor_partitions(
                index_conn,
                run_dir,
                bucket=bucket,
                min_length=min_length,
                anchor_length=anchor_length,
                partitions=partitions,
                progress=progress,
            )
            anchor_seconds = time.monotonic() - t_anchor
            _emit(progress, f"anchors written: {anchors_written} ({anchor_seconds:.1f}s)")

            cache = _BucketCache(index_conn)
            t_partitions = time.monotonic()
            skipped_anchor_groups, candidate_spans = _process_partitions(
                work_conn,
                cache,
                index_path,
                run_dir,
                min_length=min_length,
                anchor_length=anchor_length,
                max_anchor_occurrences=max_anchor_occurrences,
                partitions=partitions,
                jobs=jobs,
                progress=progress,
            )
            partition_seconds = time.monotonic() - t_partitions
            _emit(progress, f"partition processing: {partition_seconds:.1f}s")
            t_cluster = time.monotonic()
            clusters = _clusters_from_work_spans(
                work_conn,
                index_conn,
                cache,
                min_occurrences=min_occurrences,
                include_contained=include_contained,
                context=context,
            )
            cluster_seconds = time.monotonic() - t_cluster
            total_seconds = time.monotonic() - t0
            stats = ParallelScanStats(
                bucket_count=bucket_count,
                anchors_written=anchors_written,
                partitions=partitions,
                skipped_anchor_groups=skipped_anchor_groups,
                candidate_spans=candidate_spans,
                clusters=len(clusters),
                anchor_seconds=anchor_seconds,
                partition_seconds=partition_seconds,
                cluster_seconds=cluster_seconds,
                total_seconds=total_seconds,
            )
            if expected_meta is not None:
                _write_work_meta(
                    work_conn,
                    expected_meta,
                    status="complete",
                    stats=stats,
                )
            _emit(
                progress,
                "done: "
                f"{candidate_spans} spans, {len(clusters)} clusters "
                f"({total_seconds:.1f}s; cluster {cluster_seconds:.1f}s)",
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
    jobs: int,
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
    if jobs < 1:
        raise ValueError("jobs must be >= 1")


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
        CREATE TABLE scan_meta (
          key   TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        """
    )


def _prepare_anchor_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS temp.anchor_occurrence;
        CREATE TEMP TABLE anchor_occurrence (
          hash      TEXT NOT NULL,
          bucket_id INTEGER NOT NULL,
          position  INTEGER NOT NULL
        );
        """
    )


def _expected_work_meta(
    index_path: Path,
    conn: sqlite3.Connection,
    *,
    bucket: str,
    min_length: int,
    anchor_length: int,
    max_anchor_occurrences: int,
    partitions: int,
) -> dict[str, str]:
    return {
        "work_db_version": str(_WORK_DB_VERSION),
        "algorithm": _WORK_DB_ALGORITHM,
        "index_path": str(index_path.resolve()),
        "index_signature": json.dumps(list(_index_signature(index_path))),
        "index_hash": compute_bkkx_hash(index_path),
        "index_schema_version": str(_index_schema_version(conn)),
        "bucket": bucket,
        "min_length": str(min_length),
        "anchor_length": str(anchor_length),
        "max_anchor_occurrences": str(max_anchor_occurrences),
        "partitions": str(partitions),
    }


def _index_signature(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def _index_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        raise ValueError("index has no schema version")
    return int(row["value"])


def _write_work_meta(
    conn: sqlite3.Connection,
    expected_meta: dict[str, str],
    *,
    status: str,
    stats: ParallelScanStats | None = None,
) -> None:
    payload = dict(expected_meta)
    payload["status"] = status
    if stats is not None:
        payload.update({
            "bucket_count": str(stats.bucket_count),
            "anchors_written": str(stats.anchors_written),
            "skipped_anchor_groups": str(stats.skipped_anchor_groups),
            "candidate_spans": str(stats.candidate_spans),
            "clusters": str(stats.clusters),
            "anchor_seconds": f"{stats.anchor_seconds:.9f}",
            "partition_seconds": f"{stats.partition_seconds:.9f}",
            "cluster_seconds": f"{stats.cluster_seconds:.9f}",
            "total_seconds": f"{stats.total_seconds:.9f}",
        })
    conn.execute("DELETE FROM scan_meta")
    conn.executemany(
        "INSERT INTO scan_meta(key, value) VALUES (?, ?)",
        sorted(payload.items()),
    )
    conn.commit()


def _read_work_meta(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        rows = conn.execute("SELECT key, value FROM scan_meta").fetchall()
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"cannot read scan_meta: {exc}") from exc
    return {row["key"]: row["value"] for row in rows}


def _try_reuse_work_db(
    work_db: Path,
    expected_meta: dict[str, str],
    index_conn: sqlite3.Connection,
    *,
    min_occurrences: int,
    include_contained: bool,
    context: int,
    progress: TextIO | None,
    started_at: float,
) -> tuple[tuple[list[ParallelCluster], ParallelScanStats] | None, str | None]:
    try:
        conn = sqlite3.connect(str(work_db))
        conn.row_factory = sqlite3.Row
    except sqlite3.DatabaseError as exc:
        return None, f"cannot open DB: {exc}"
    try:
        try:
            meta = _read_work_meta(conn)
        except ValueError as exc:
            return None, str(exc)
        for key, value in expected_meta.items():
            if meta.get(key) != value:
                return None, f"metadata mismatch for {key}"
        if meta.get("status") != "complete":
            return None, "scan is not complete"
        try:
            candidate_spans = conn.execute(
                "SELECT COUNT(*) FROM candidate_span"
            ).fetchone()[0]
        except sqlite3.DatabaseError as exc:
            return None, f"cannot read candidate spans: {exc}"
        _emit(progress, f"reusing work DB: {work_db}")
        cache = _BucketCache(index_conn)
        t_cluster = time.monotonic()
        clusters = _clusters_from_work_spans(
            conn,
            index_conn,
            cache,
            min_occurrences=min_occurrences,
            include_contained=include_contained,
            context=context,
        )
        cluster_seconds = time.monotonic() - t_cluster
        total_seconds = time.monotonic() - started_at
        try:
            stats = ParallelScanStats(
                bucket_count=_meta_int(meta, "bucket_count"),
                anchors_written=_meta_int(meta, "anchors_written"),
                partitions=int(expected_meta["partitions"]),
                skipped_anchor_groups=_meta_int(meta, "skipped_anchor_groups"),
                candidate_spans=candidate_spans,
                clusters=len(clusters),
                anchor_seconds=0.0,
                partition_seconds=0.0,
                cluster_seconds=cluster_seconds,
                total_seconds=total_seconds,
            )
        except ValueError as exc:
            return None, str(exc)
        _emit(
            progress,
            "done: "
            f"{candidate_spans} spans, {len(clusters)} clusters "
            f"({total_seconds:.1f}s; reused work DB, cluster {cluster_seconds:.1f}s)",
        )
        return (clusters, stats), None
    finally:
        conn.close()


def _meta_int(meta: dict[str, str], key: str) -> int:
    try:
        return int(meta[key])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"work DB metadata has invalid {key}") from exc


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
    started_at = time.monotonic()
    next_heartbeat = started_at + _HEARTBEAT_SECONDS
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
                part = int.from_bytes(h[:4], "big") % partitions
                handles[part].write(f"{h.hex()}\t{row['bucket_id']}\t{pos}\n")
                anchors_written += 1
                if anchors_written % _ANCHOR_HEARTBEAT_CHECK_INTERVAL == 0:
                    now = time.monotonic()
                    if now >= next_heartbeat:
                        _emit(
                            progress,
                            "anchor heartbeat: "
                            f"{buckets_seen} buckets, "
                            f"{anchors_written} anchors, "
                            f"{now - started_at:.1f}s elapsed",
                        )
                        next_heartbeat = now + _HEARTBEAT_SECONDS
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
) -> Iterator[tuple[int, bytes]]:
    if len(text) < min_length:
        return
    window = min_length - anchor_length + 1
    mins: deque[tuple[int, bytes]] = deque()
    last_selected: int | None = None
    for pos in range(0, len(text) - anchor_length + 1):
        h = _anchor_hash_bytes(text[pos:pos + anchor_length])
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


def _winnow_key(pos: int, h: bytes) -> tuple[bytes, int]:
    # Pick the minimum hash; ties choose the rightmost occurrence.
    return h, -pos


def _anchor_hash_bytes(text: str) -> bytes:
    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()


def _anchor_hash(text: str) -> str:
    return _anchor_hash_bytes(text).hex()


def _process_partitions(
    work_conn: sqlite3.Connection,
    cache: _BucketCache,
    index_path: Path,
    run_dir: Path,
    *,
    min_length: int,
    anchor_length: int,
    max_anchor_occurrences: int,
    partitions: int,
    jobs: int,
    progress: TextIO | None,
) -> tuple[int, int]:
    if jobs > 1:
        return _process_partitions_parallel(
            work_conn,
            index_path,
            run_dir,
            min_length=min_length,
            anchor_length=anchor_length,
            max_anchor_occurrences=max_anchor_occurrences,
            partitions=partitions,
            jobs=jobs,
            progress=progress,
        )
    skipped_anchor_groups = 0
    candidate_spans = 0
    partition_dir = run_dir / "anchors"
    for part in range(partitions):
        path = partition_dir / f"part-{part:04d}.tsv"
        if not path.exists() or path.stat().st_size == 0:
            continue
        _emit(progress, f"partition {part + 1}/{partitions}: loading")
        _prepare_anchor_db(work_conn)
        _load_partition(work_conn, path)
        work_conn.commit()
        repeated_groups, skipped, spans = _record_partition_spans(
            work_conn,
            cache,
            max_anchor_occurrences=max_anchor_occurrences,
            min_length=min_length,
            anchor_length=anchor_length,
            progress=progress,
            label=f"partition {part + 1}/{partitions}",
        )
        skipped_anchor_groups += skipped
        candidate_spans += spans
        work_conn.commit()
        _emit(
            progress,
            f"partition {part + 1}/{partitions}: "
            f"{repeated_groups} repeated anchors, "
            f"{skipped_anchor_groups} skipped groups, "
            f"{candidate_spans} spans",
        )
    return skipped_anchor_groups, candidate_spans


def _process_partitions_parallel(
    work_conn: sqlite3.Connection,
    index_path: Path,
    run_dir: Path,
    *,
    min_length: int,
    anchor_length: int,
    max_anchor_occurrences: int,
    partitions: int,
    jobs: int,
    progress: TextIO | None,
) -> tuple[int, int]:
    partition_dir = run_dir / "anchors"
    tasks = []
    for part in range(partitions):
        path = partition_dir / f"part-{part:04d}.tsv"
        if path.exists() and path.stat().st_size > 0:
            span_db = run_dir / f"part-{part:04d}.spans.sqlite3"
            tasks.append((
                str(index_path),
                str(path),
                str(span_db),
                part,
                min_length,
                anchor_length,
                max_anchor_occurrences,
            ))
    skipped_anchor_groups = 0
    candidate_spans = 0
    completed = 0
    started_at = time.monotonic()
    _emit(
        progress,
        f"partition workers: {len(tasks)} nonempty partitions, jobs={jobs}",
    )
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = {
            pool.submit(_process_partition_worker, task): task[3]
            for task in tasks
        }
        while futures:
            done, _pending = wait(
                futures,
                timeout=_HEARTBEAT_SECONDS,
                return_when=FIRST_COMPLETED,
            )
            if not done:
                elapsed = time.monotonic() - started_at
                _emit(
                    progress,
                    "partition heartbeat: "
                    f"{completed}/{len(tasks)} complete, "
                    f"{len(futures)} running, "
                    f"{skipped_anchor_groups} skipped groups, "
                    f"{candidate_spans} spans, "
                    f"{elapsed:.1f}s elapsed",
                )
                continue
            for future in done:
                futures.pop(future)
                result = future.result()
                completed += 1
                part, repeated_groups, skipped, _worker_spans, span_db = result
                skipped_anchor_groups += skipped
                candidate_spans += _merge_candidate_spans(work_conn, Path(span_db))
                _emit(
                    progress,
                    f"partition {part + 1}/{partitions}: "
                    f"{repeated_groups} repeated anchors, "
                    f"{skipped_anchor_groups} skipped groups, "
                    f"{candidate_spans} spans "
                    f"({completed}/{len(tasks)} complete)",
                )
    work_conn.commit()
    return skipped_anchor_groups, candidate_spans


def _process_partition_worker(args) -> tuple[int, int, int, int, str]:
    (
        index_path_s,
        partition_path_s,
        span_db_s,
        part,
        min_length,
        anchor_length,
        max_anchor_occurrences,
    ) = args
    index_conn = sqlite3.connect(f"file:{index_path_s}?mode=ro", uri=True)
    index_conn.row_factory = sqlite3.Row
    span_db = Path(span_db_s)
    work_conn = sqlite3.connect(str(span_db))
    work_conn.row_factory = sqlite3.Row
    try:
        index_conn.execute("PRAGMA temp_store = FILE")
        work_conn.execute("PRAGMA temp_store = FILE")
        _init_work_db(work_conn)
        _prepare_anchor_db(work_conn)
        _load_partition(work_conn, Path(partition_path_s))
        cache = _BucketCache(index_conn)
        repeated_groups, skipped, spans = _record_partition_spans(
            work_conn,
            cache,
            max_anchor_occurrences=max_anchor_occurrences,
            min_length=min_length,
            anchor_length=anchor_length,
            progress=None,
            label=f"partition {part + 1}",
        )
        work_conn.commit()
        return part, repeated_groups, skipped, spans, span_db_s
    finally:
        work_conn.close()
        index_conn.close()


def _merge_candidate_spans(conn: sqlite3.Connection, span_db: Path) -> int:
    other = sqlite3.connect(str(span_db))
    try:
        rows = []
        before = conn.total_changes
        for row in other.execute(
            "SELECT bucket_a, start_a, end_a, bucket_b, start_b, end_b "
            "FROM candidate_span "
            "ORDER BY bucket_a, start_a, end_a, bucket_b, start_b, end_b"
        ):
            rows.append(tuple(row))
            if len(rows) >= 10000:
                _insert_candidate_span_rows(conn, rows)
                rows.clear()
        if rows:
            _insert_candidate_span_rows(conn, rows)
        return conn.total_changes - before
    finally:
        other.close()


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
    conn.execute(
        "CREATE INDEX idx_anchor_occurrence_hash "
        "ON anchor_occurrence(hash, bucket_id, position)"
    )


def _record_partition_spans(
    conn: sqlite3.Connection,
    cache: _BucketCache,
    *,
    max_anchor_occurrences: int,
    min_length: int,
    anchor_length: int,
    progress: TextIO | None,
    label: str,
) -> tuple[int, int, int]:
    repeated_groups = 0
    skipped_anchor_groups = 0
    candidate_spans = 0
    started_at = time.monotonic()
    next_heartbeat = started_at + _GROUP_HEARTBEAT_SECONDS
    current_hash: str | None = None
    postings: list[tuple[int, int]] = []
    for row in conn.execute(
        "SELECT hash, bucket_id, position FROM anchor_occurrence "
        "ORDER BY hash, bucket_id, position"
    ):
        h = row["hash"]
        if current_hash is None:
            current_hash = h
        if h != current_hash:
            repeated, skipped, spans = _record_hash_group(
                conn,
                cache,
                postings,
                max_anchor_occurrences=max_anchor_occurrences,
                min_length=min_length,
                anchor_length=anchor_length,
                progress=progress,
                label=f"{label} hash {current_hash}",
            )
            repeated_groups += repeated
            skipped_anchor_groups += skipped
            candidate_spans += spans
            now = time.monotonic()
            if progress is not None and now >= next_heartbeat:
                _emit(
                    progress,
                    f"{label} heartbeat: "
                    f"{repeated_groups} repeated anchors, "
                    f"{skipped_anchor_groups} skipped groups, "
                    f"{candidate_spans} spans, "
                    f"{now - started_at:.1f}s elapsed",
                )
                next_heartbeat = now + _GROUP_HEARTBEAT_SECONDS
            current_hash = h
            postings = []
        postings.append((row["bucket_id"], row["position"]))
    if current_hash is not None:
        repeated, skipped, spans = _record_hash_group(
            conn,
            cache,
            postings,
            max_anchor_occurrences=max_anchor_occurrences,
            min_length=min_length,
            anchor_length=anchor_length,
            progress=progress,
            label=f"{label} hash {current_hash}",
        )
        repeated_groups += repeated
        skipped_anchor_groups += skipped
        candidate_spans += spans
    return repeated_groups, skipped_anchor_groups, candidate_spans


def _record_hash_group(
    conn: sqlite3.Connection,
    cache: _BucketCache,
    postings: list[tuple[int, int]],
    *,
    max_anchor_occurrences: int,
    min_length: int,
    anchor_length: int,
    progress: TextIO | None,
    label: str,
) -> tuple[int, int, int]:
    if len(postings) < 2:
        return 0, 0, 0
    if len(postings) > max_anchor_occurrences:
        return 1, 1, 0
    spans = _record_group_spans(
        conn,
        cache,
        postings,
        min_length=min_length,
        anchor_length=anchor_length,
        progress=progress,
        label=label,
    )
    return 1, 0, spans


def _record_group_spans(
    conn: sqlite3.Connection,
    cache: _BucketCache,
    postings: list[tuple[int, int]],
    *,
    min_length: int,
    anchor_length: int,
    progress: TextIO | None,
    label: str,
) -> int:
    rows = []
    pairs_seen = 0
    started_at = time.monotonic()
    next_heartbeat = started_at + _GROUP_HEARTBEAT_SECONDS
    for i, left in enumerate(postings):
        for right in postings[i + 1:]:
            pairs_seen += 1
            if pairs_seen % _PAIR_HEARTBEAT_CHECK_INTERVAL == 0:
                now = time.monotonic()
                if progress is not None and now >= next_heartbeat:
                    _emit(
                        progress,
                        f"{label} heartbeat: "
                        f"{pairs_seen} pairs checked, "
                        f"{len(rows)} local spans, "
                        f"{now - started_at:.1f}s elapsed",
                    )
                    next_heartbeat = now + _GROUP_HEARTBEAT_SECONDS
            span_a, span_b = _maximal_pair_span(
                cache,
                left[0],
                left[1],
                right[0],
                right[1],
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
    _insert_candidate_span_rows(conn, rows)
    return conn.total_changes - before


def _insert_candidate_span_rows(
    conn: sqlite3.Connection,
    rows: list[tuple[int, int, int, int, int, int]],
) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO candidate_span"
        "(bucket_a, start_a, end_a, bucket_b, start_b, end_b) "
        "VALUES (?,?,?,?,?,?)",
        rows,
    )


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
