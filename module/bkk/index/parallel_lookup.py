"""Sidecar point lookup for precomputed parallel-passage clusters."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from .build import compute_bkkx_hash
from .parallel import (
    ParallelCluster,
    _BucketCache,
    _align_ops,
    _clusters_from_spans,
    _clusters_from_spans_fuzzy,
    _make_location,
    _maximal_pair_span_fuzzy,
)
from .parallel_scan import discover_parallel_passages_scan


LOOKUP_SCHEMA_VERSION = 1
DEFAULT_LOOKUP_MIN_LENGTH = 12
DEFAULT_LOOKUP_MAX_EDITS = 4
DEFAULT_LOOKUP_MIN_OCCURRENCES = 2
DEFAULT_LOOKUP_ANCHOR_LENGTH = 12
DEFAULT_SKETCH_K_GRAM = 5
DEFAULT_SKETCH_SIZE = 128
DEFAULT_LSH_BANDS = 16


class ParallelLookupStaleError(ValueError):
    """Raised when a lookup sidecar no longer matches its source index."""


@dataclass(frozen=True)
class ParallelLookupBuildStats:
    """Counters collected while building a parallel lookup sidecar."""

    lookup_path: Path
    clusters: int
    occurrences: int
    candidate_spans: int
    fuzzy_spans: int
    total_seconds: float
    sketch_bucket_count: int = 0
    sketch_candidate_pairs: int = 0


@dataclass(frozen=True)
class _SketchPrefilter:
    sketches: dict[int, bytes]
    band_postings: list[tuple[str, int]]
    candidate_pairs: set[tuple[int, int]]


def default_parallel_lookup_path(index_path: Path | str) -> Path:
    """Return the default ``.bkkp`` sidecar path for ``index_path``."""
    return Path(index_path).with_suffix(".bkkp")


def build_parallel_lookup(
    index_path: Path | str,
    lookup_path: Path | str | None = None,
    *,
    bucket: str = "body",
    min_length: int = DEFAULT_LOOKUP_MIN_LENGTH,
    anchor_length: int = DEFAULT_LOOKUP_ANCHOR_LENGTH,
    max_edits: int = DEFAULT_LOOKUP_MAX_EDITS,
    max_anchor_occurrences: int = 200,
    min_occurrences: int = DEFAULT_LOOKUP_MIN_OCCURRENCES,
    partitions: int = 256,
    work_dir: Path | str | None = None,
    work_db: Path | str | None = None,
    force_work_db: bool = False,
    jobs: int = 1,
    include_contained: bool = False,
    enable_sketch_prefilter: bool = False,
    sketch_k_gram: int = DEFAULT_SKETCH_K_GRAM,
    sketch_size: int = DEFAULT_SKETCH_SIZE,
    lsh_bands: int = DEFAULT_LSH_BANDS,
    progress: TextIO | None = None,
) -> ParallelLookupBuildStats:
    """Build a ``.bkkp`` sidecar for point parallel-passage lookup.

    The sidecar stores only cluster structure and source offsets. Text,
    context, TOC labels, and diffs are hydrated from the paired ``.bkkx`` at
    query time.
    """
    _validate_build_args(
        bucket=bucket,
        min_length=min_length,
        anchor_length=anchor_length,
        max_edits=max_edits,
        max_anchor_occurrences=max_anchor_occurrences,
        min_occurrences=min_occurrences,
        partitions=partitions,
        jobs=jobs,
        enable_sketch_prefilter=enable_sketch_prefilter,
        sketch_k_gram=sketch_k_gram,
        sketch_size=sketch_size,
        lsh_bands=lsh_bands,
    )
    index_path = Path(index_path)
    if not index_path.is_file():
        raise FileNotFoundError(index_path)
    lookup_path = (
        default_parallel_lookup_path(index_path)
        if lookup_path is None else Path(lookup_path)
    )
    lookup_path.parent.mkdir(parents=True, exist_ok=True)

    started_at = time.monotonic()
    index_conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    index_conn.row_factory = sqlite3.Row
    try:
        index_meta = _index_meta(index_path, index_conn)
        sketch_prefilter = (
            _build_sketch_prefilter(
                index_conn,
                bucket=bucket,
                k_gram=sketch_k_gram,
                sketch_size=sketch_size,
                lsh_bands=lsh_bands,
            )
            if enable_sketch_prefilter else None
        )
        if sketch_prefilter is not None:
            _emit(
                progress,
                "sketch prefilter: "
                f"{len(sketch_prefilter.sketches)} buckets, "
                f"{len(sketch_prefilter.candidate_pairs)} candidate pairs",
            )
        with tempfile.TemporaryDirectory(
            prefix="bkk-parallel-lookup-", dir=str(lookup_path.parent)
        ) as tmp:
            tmp_dir = Path(tmp)
            scan_work_db = (
                Path(work_db) if work_db is not None else tmp_dir / "scan.sqlite3"
            )
            discover_parallel_passages_scan(
                index_path,
                bucket=bucket,
                min_length=min_length,
                anchor_length=anchor_length,
                min_occurrences=min_occurrences,
                max_anchor_occurrences=max_anchor_occurrences,
                partitions=partitions,
                work_dir=work_dir,
                work_db=scan_work_db,
                force_work_db=force_work_db,
                jobs=jobs,
                include_contained=True,
                context=0,
                candidate_bucket_pairs=(
                    sketch_prefilter.candidate_pairs
                    if sketch_prefilter is not None else None
                ),
                progress=progress,
            )
            candidate_spans = _candidate_span_count(scan_work_db)
            fuzzy_spans = _prepare_lookup_spans(
                index_conn,
                scan_work_db,
                min_length=min_length,
                max_edits=max_edits,
            )
            cache = _BucketCache(index_conn)
            if max_edits == 0:
                clusters = _clusters_from_spans(
                    index_conn,
                    cache,
                    min_occurrences=min_occurrences,
                    include_contained=include_contained,
                    context=0,
                )
            else:
                clusters = _clusters_from_spans_fuzzy(
                    index_conn,
                    cache,
                    max_edits=max_edits,
                    min_occurrences=min_occurrences,
                    include_contained=include_contained,
                    context=0,
                )
            tmp_lookup = tmp_dir / (lookup_path.name + ".tmp")
            _write_lookup_db(
                tmp_lookup,
                index_conn,
                cache,
                clusters,
                meta={
                    **index_meta,
                    "schema_version": str(LOOKUP_SCHEMA_VERSION),
                    "status": "complete",
                    "bucket": bucket,
                    "min_length": str(min_length),
                    "anchor_length": str(anchor_length),
                    "extension_max_edits": str(max_edits),
                    "max_edits": str(_advertised_max_edits(clusters, max_edits)),
                    "max_anchor_occurrences": str(max_anchor_occurrences),
                    "min_occurrences": str(min_occurrences),
                    "partitions": str(partitions),
                    "include_contained": "1" if include_contained else "0",
                    "enable_sketch_prefilter": (
                        "1" if enable_sketch_prefilter else "0"
                    ),
                    "sketch_k_gram": str(sketch_k_gram),
                    "sketch_size": str(sketch_size),
                    "lsh_bands": str(lsh_bands),
                    "sketch_bucket_count": str(
                        len(sketch_prefilter.sketches)
                        if sketch_prefilter is not None else 0
                    ),
                    "sketch_candidate_pairs": str(
                        len(sketch_prefilter.candidate_pairs)
                        if sketch_prefilter is not None else 0
                    ),
                    "candidate_spans": str(candidate_spans),
                    "fuzzy_spans": str(fuzzy_spans),
                    "clusters": str(len(clusters)),
                },
                create_sketch_tables=enable_sketch_prefilter,
                sketch_prefilter=sketch_prefilter,
            )
            os.replace(tmp_lookup, lookup_path)
    finally:
        index_conn.close()
    occurrences = _lookup_occurrence_count(lookup_path)
    stats = ParallelLookupBuildStats(
        lookup_path=lookup_path,
        clusters=_lookup_cluster_count(lookup_path),
        occurrences=occurrences,
        candidate_spans=candidate_spans,
        fuzzy_spans=fuzzy_spans,
        total_seconds=time.monotonic() - started_at,
        sketch_bucket_count=(
            len(sketch_prefilter.sketches) if sketch_prefilter is not None else 0
        ),
        sketch_candidate_pairs=(
            len(sketch_prefilter.candidate_pairs)
            if sketch_prefilter is not None else 0
        ),
    )
    _emit(
        progress,
        "parallel lookup done: "
        f"{stats.clusters} clusters, {stats.occurrences} occurrences "
        f"({stats.total_seconds:.1f}s)",
    )
    return stats


class ParallelLookup:
    """Read-only point lookup over a ``.bkkx`` plus ``.bkkp`` sidecar."""

    def __init__(
        self,
        index_path: Path | str,
        lookup_path: Path | str | None = None,
    ):
        self.index_path = Path(index_path)
        self.lookup_path = (
            default_parallel_lookup_path(self.index_path)
            if lookup_path is None else Path(lookup_path)
        )
        self.index_conn = sqlite3.connect(
            f"file:{self.index_path}?mode=ro", uri=True,
        )
        self.index_conn.row_factory = sqlite3.Row
        try:
            self.lookup_conn = sqlite3.connect(
                f"file:{self.lookup_path}?mode=ro", uri=True,
            )
            self.lookup_conn.row_factory = sqlite3.Row
        except Exception:
            self.index_conn.close()
            raise
        self.meta = self._read_meta()
        try:
            self._validate_sidecar()
        except Exception:
            self.close()
            raise
        self._cache = _BucketCache(self.index_conn)

    def close(self) -> None:
        self.lookup_conn.close()
        self.index_conn.close()

    def __enter__(self) -> "ParallelLookup":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def find_at(
        self,
        textid: str,
        juan_seq: int,
        offset: int,
        bucket: str = "body",
        *,
        min_length: int,
        max_edits: int = 0,
        min_occurrences: int = DEFAULT_LOOKUP_MIN_OCCURRENCES,
        context: int = 20,
        mode: str = "overlap",
        include_self: bool = False,
    ) -> list[ParallelCluster]:
        """Return clusters with an occurrence at ``offset`` in the given bucket."""
        self._validate_query(
            min_length=min_length,
            max_edits=max_edits,
            min_occurrences=min_occurrences,
            context=context,
            mode=mode,
        )
        if offset < 0:
            raise ValueError("offset must be non-negative")
        bucket_id = self._bucket_id(textid, juan_seq, bucket)
        if mode == "overlap":
            rows = self.lookup_conn.execute(
                "SELECT cluster_id, start, end, edit_distance "
                "FROM poccurrence "
                "WHERE bucket_id = ? AND start <= ? AND end > ?",
                (bucket_id, offset, offset),
            ).fetchall()
        else:
            rows = self.lookup_conn.execute(
                "SELECT cluster_id, start, end, edit_distance "
                "FROM poccurrence "
                "WHERE bucket_id = ? AND start <= ? AND end >= ?",
                (bucket_id, offset, offset),
            ).fetchall()
        hit_edits: dict[int, int] = {}
        query_spans: set[tuple[int, int, int]] = set()
        for row in rows:
            if row["edit_distance"] > max_edits:
                continue
            cluster_id = int(row["cluster_id"])
            hit_edits[cluster_id] = min(
                row["edit_distance"],
                hit_edits.get(cluster_id, row["edit_distance"]),
            )
            query_spans.add((bucket_id, int(row["start"]), int(row["end"])))
        if not hit_edits:
            return []

        clusters: list[tuple[int, ParallelCluster]] = []
        for cluster_id, hit_edit_distance in sorted(hit_edits.items()):
            cluster = self._hydrate_cluster(
                cluster_id,
                query_spans=query_spans,
                min_length=min_length,
                max_edits=max_edits,
                min_occurrences=min_occurrences,
                context=context,
                include_self=include_self,
            )
            if cluster is not None:
                clusters.append((hit_edit_distance, cluster))
        clusters.sort(key=lambda item: (-item[1].length, item[0], item[1].cluster_id))
        return [cluster for _hit_edit_distance, cluster in clusters]

    def _read_meta(self) -> dict[str, str]:
        rows = self.lookup_conn.execute("SELECT key, value FROM meta").fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def _validate_sidecar(self) -> None:
        if self.meta.get("status") != "complete":
            raise ParallelLookupStaleError(
                f"parallel lookup is not complete; rebuild {self.lookup_path}"
            )
        if self.meta.get("schema_version") != str(LOOKUP_SCHEMA_VERSION):
            raise ParallelLookupStaleError(
                f"parallel lookup schema is stale; rebuild {self.lookup_path}"
            )
        expected = _index_meta(self.index_path, self.index_conn)
        for key in ("index_hash", "index_signature"):
            if self.meta.get(key) != expected[key]:
                raise ParallelLookupStaleError(
                    "parallel lookup does not match the index; "
                    f"rebuild {self.lookup_path}"
                )

    def _validate_query(
        self,
        *,
        min_length: int,
        max_edits: int,
        min_occurrences: int,
        context: int,
        mode: str,
    ) -> None:
        if mode not in {"overlap", "cover"}:
            raise ValueError("mode must be one of: overlap, cover")
        if context < 0:
            raise ValueError("context must be non-negative")
        floor_length = int(self.meta.get("min_length", "1"))
        floor_occurrences = int(self.meta.get("min_occurrences", "2"))
        build_max_edits = int(self.meta.get("max_edits", "0"))
        if min_length < floor_length:
            raise ValueError(
                f"min_length must be at least the lookup build floor ({floor_length})"
            )
        if min_occurrences < floor_occurrences:
            raise ValueError(
                "min_occurrences must be at least the lookup build floor "
                f"({floor_occurrences})"
            )
        if max_edits < 0:
            raise ValueError("max_edits must be non-negative")
        if max_edits > build_max_edits:
            raise ValueError(
                f"max_edits must be <= the lookup query budget ({build_max_edits})"
            )

    def _bucket_id(self, textid: str, juan_seq: int, bucket: str) -> int:
        row = self.index_conn.execute(
            "SELECT b.bucket_id FROM bucket b "
            "JOIN juan j ON j.juan_id = b.juan_id "
            "WHERE j.textid = ? AND j.seq = ? AND b.kind = ?",
            (textid, juan_seq, bucket),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"bucket not found: {textid} juan {juan_seq} bucket {bucket}"
            )
        return int(row["bucket_id"])

    def _hydrate_cluster(
        self,
        cluster_id: int,
        *,
        query_spans: set[tuple[int, int, int]],
        min_length: int,
        max_edits: int,
        min_occurrences: int,
        context: int,
        include_self: bool,
    ) -> ParallelCluster | None:
        row = self.lookup_conn.execute(
            "SELECT cluster_id, length, rep_bucket_id, rep_start, rep_end "
            "FROM pcluster WHERE cluster_id = ? AND length >= ?",
            (cluster_id, min_length),
        ).fetchone()
        if row is None:
            return None
        occ_rows = self.lookup_conn.execute(
            "SELECT bucket_id, start, end, edit_distance "
            "FROM poccurrence "
            "WHERE cluster_id = ? AND edit_distance <= ? "
            "ORDER BY bucket_id, start, end",
            (cluster_id, max_edits),
        ).fetchall()
        if len(occ_rows) < min_occurrences:
            return None
        rep_text = self._cache.get(row["rep_bucket_id"]).text[
            row["rep_start"]:row["rep_end"]
        ]
        locations = []
        max_d = 0
        for occ in occ_rows:
            span = (int(occ["bucket_id"]), int(occ["start"]), int(occ["end"]))
            if not include_self and span in query_spans:
                continue
            d = int(occ["edit_distance"])
            text = "" if d == 0 else self._cache.get(span[0]).text[span[1]:span[2]]
            diff = _align_ops(rep_text, text) if d > 0 else ()
            locations.append(
                _make_location(
                    self.index_conn,
                    self._cache,
                    span[0],
                    span[1],
                    span[2],
                    context,
                    edit_distance=d,
                    text=text,
                    diff=diff,
                )
            )
            if d > max_d:
                max_d = d
        if not locations:
            return None
        return ParallelCluster(
            cluster_id=f"parallel-{cluster_id:06d}",
            length=int(row["length"]),
            occurrence_count=len(locations),
            text=rep_text,
            locations=tuple(locations),
            representative_edits=max_d,
        )


def _validate_build_args(
    *,
    bucket: str,
    min_length: int,
    anchor_length: int,
    max_edits: int,
    max_anchor_occurrences: int,
    min_occurrences: int,
    partitions: int,
    jobs: int,
    enable_sketch_prefilter: bool,
    sketch_k_gram: int,
    sketch_size: int,
    lsh_bands: int,
) -> None:
    if bucket not in {"front", "body", "back", "all"}:
        raise ValueError("bucket must be one of: front, body, back, all")
    if min_length < 1:
        raise ValueError("min_length must be positive")
    if anchor_length < 1:
        raise ValueError("anchor_length must be positive")
    if min_length < anchor_length:
        raise ValueError("min_length must be greater than or equal to anchor_length")
    if max_edits < 0 or max_edits > 4:
        raise ValueError("max_edits must be between 0 and 4")
    if max_anchor_occurrences < 2:
        raise ValueError("max_anchor_occurrences must be at least 2")
    if min_occurrences < 2:
        raise ValueError("min_occurrences must be at least 2")
    if partitions < 1:
        raise ValueError("partitions must be positive")
    if jobs < 1:
        raise ValueError("jobs must be >= 1")
    if enable_sketch_prefilter:
        if sketch_k_gram < 1:
            raise ValueError("sketch_k_gram must be positive")
        if sketch_size < 1:
            raise ValueError("sketch_size must be positive")
        if lsh_bands < 1:
            raise ValueError("lsh_bands must be positive")


def _index_meta(index_path: Path, conn: sqlite3.Connection) -> dict[str, str]:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        raise ValueError("index has no schema version")
    return {
        "index_path": str(index_path.resolve()),
        "index_hash": compute_bkkx_hash(index_path),
        "index_signature": json.dumps(list(_index_signature(index_path))),
        "index_schema_version": str(row["value"]),
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


def _candidate_span_count(work_db: Path) -> int:
    conn = sqlite3.connect(str(work_db))
    try:
        return int(conn.execute("SELECT COUNT(*) FROM candidate_span").fetchone()[0])
    finally:
        conn.close()


def _prepare_lookup_spans(
    index_conn: sqlite3.Connection,
    work_db: Path,
    *,
    min_length: int,
    max_edits: int,
) -> int:
    index_conn.executescript(
        """
        DROP TABLE IF EXISTS temp.parallel_pair_span;
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
    cache = _BucketCache(index_conn)
    work_conn = sqlite3.connect(str(work_db))
    work_conn.row_factory = sqlite3.Row
    try:
        rows = []
        before = index_conn.total_changes
        for row in work_conn.execute(
            "SELECT bucket_a, start_a, end_a, bucket_b, start_b, end_b "
            "FROM candidate_span "
            "ORDER BY bucket_a, start_a, end_a, bucket_b, start_b, end_b"
        ):
            if max_edits == 0:
                rows.append((
                    row["bucket_a"],
                    row["start_a"],
                    row["end_a"],
                    row["bucket_b"],
                    row["start_b"],
                    row["end_b"],
                    0,
                ))
            else:
                seed_length = min(
                    row["end_a"] - row["start_a"],
                    row["end_b"] - row["start_b"],
                )
                span_a, span_b, edits = _maximal_pair_span_fuzzy(
                    cache,
                    row["bucket_a"],
                    row["start_a"],
                    row["bucket_b"],
                    row["start_b"],
                    seed_length,
                    max_edits,
                )
                if span_a is None or span_b is None:
                    continue
                if span_a.end - span_a.start < min_length:
                    continue
                if span_b.end - span_b.start < min_length:
                    continue
                rows.append((
                    span_a.bucket_id,
                    span_a.start,
                    span_a.end,
                    span_b.bucket_id,
                    span_b.start,
                    span_b.end,
                    edits,
                ))
            if len(rows) >= 10000:
                _insert_lookup_span_rows(index_conn, rows)
                rows.clear()
        if rows:
            _insert_lookup_span_rows(index_conn, rows)
        return index_conn.total_changes - before
    finally:
        work_conn.close()


def _insert_lookup_span_rows(
    conn: sqlite3.Connection,
    rows: list[tuple[int, int, int, int, int, int, int]],
) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO temp.parallel_pair_span"
        "(bucket_a, start_a, end_a, bucket_b, start_b, end_b, edits) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )


def _write_lookup_db(
    path: Path,
    index_conn: sqlite3.Connection,
    cache: _BucketCache,
    clusters: list[ParallelCluster],
    *,
    meta: dict[str, str],
    create_sketch_tables: bool,
    sketch_prefilter: _SketchPrefilter | None,
) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE meta (
              key   TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE pcluster (
              cluster_id INTEGER PRIMARY KEY,
              length INTEGER NOT NULL,
              rep_bucket_id INTEGER NOT NULL,
              rep_start INTEGER NOT NULL,
              rep_end INTEGER NOT NULL,
              occurrence_count INTEGER NOT NULL,
              max_edits INTEGER NOT NULL
            );
            CREATE TABLE poccurrence (
              cluster_id INTEGER NOT NULL,
              bucket_id INTEGER NOT NULL,
              start INTEGER NOT NULL,
              end INTEGER NOT NULL,
              edit_distance INTEGER NOT NULL,
              PRIMARY KEY (cluster_id, bucket_id, start, end)
            );
            CREATE INDEX idx_poccurrence_loc
              ON poccurrence(bucket_id, start, end);
            CREATE INDEX idx_poccurrence_cluster
              ON poccurrence(cluster_id);
            """
        )
        if create_sketch_tables:
            conn.executescript(
                """
                CREATE TABLE psketch (
                  bucket_id INTEGER PRIMARY KEY,
                  sketch BLOB NOT NULL
                );
                CREATE TABLE plsh_band (
                  band_hash TEXT NOT NULL,
                  bucket_id INTEGER NOT NULL
                );
                CREATE INDEX idx_plsh_band
                  ON plsh_band(band_hash, bucket_id);
                """
            )
            if sketch_prefilter is not None:
                _write_sketch_tables(conn, sketch_prefilter)
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            sorted(meta.items()),
        )
        for idx, cluster in enumerate(clusters, 1):
            rep = _representative_span(cache, cluster)
            conn.execute(
                "INSERT INTO pcluster"
                "(cluster_id, length, rep_bucket_id, rep_start, rep_end, "
                "occurrence_count, max_edits) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    idx,
                    cluster.length,
                    rep[0],
                    rep[1],
                    rep[2],
                    len(cluster.locations),
                    cluster.representative_edits,
                ),
            )
            conn.executemany(
                "INSERT INTO poccurrence"
                "(cluster_id, bucket_id, start, end, edit_distance) "
                "VALUES (?,?,?,?,?)",
                (
                    (idx, loc.bucket_id, loc.start, loc.end, loc.edit_distance)
                    for loc in cluster.locations
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _representative_span(
    cache: _BucketCache,
    cluster: ParallelCluster,
) -> tuple[int, int, int]:
    candidates = []
    for loc in cluster.locations:
        text = cache.get(loc.bucket_id).text[loc.start:loc.end]
        if text == cluster.text:
            candidates.append((loc.bucket_id, loc.start, loc.end))
    if candidates:
        return min(candidates)
    loc = max(
        cluster.locations,
        key=lambda item: (item.end - item.start, -item.bucket_id, -item.start),
    )
    return loc.bucket_id, loc.start, loc.end


def _advertised_max_edits(
    clusters: list[ParallelCluster],
    extension_max_edits: int,
) -> int:
    return max(
        [extension_max_edits]
        + [cluster.representative_edits for cluster in clusters]
    )


def _build_sketch_prefilter(
    index_conn: sqlite3.Connection,
    *,
    bucket: str,
    k_gram: int,
    sketch_size: int,
    lsh_bands: int,
) -> _SketchPrefilter:
    sketches: dict[int, bytes] = {}
    band_postings: list[tuple[str, int]] = []
    by_band: dict[str, list[int]] = {}
    if bucket == "all":
        rows = index_conn.execute(
            "SELECT bucket_id, text FROM bucket WHERE length(text) >= ? "
            "ORDER BY bucket_id",
            (k_gram,),
        )
    else:
        rows = index_conn.execute(
            "SELECT bucket_id, text FROM bucket "
            "WHERE kind = ? AND length(text) >= ? ORDER BY bucket_id",
            (bucket, k_gram),
        )
    for row in rows:
        sketch = _minhash_sketch(row["text"], k_gram=k_gram, size=sketch_size)
        blob = b"".join(v.to_bytes(8, "big") for v in sketch)
        bucket_id = int(row["bucket_id"])
        sketches[bucket_id] = blob
        for band_hash in _lsh_band_hashes(blob, bands=lsh_bands):
            band_postings.append((band_hash, bucket_id))
            by_band.setdefault(band_hash, []).append(bucket_id)

    candidate_pairs = {(bucket_id, bucket_id) for bucket_id in sketches}
    for bucket_ids in by_band.values():
        unique = sorted(set(bucket_ids))
        for i, left in enumerate(unique):
            for right in unique[i + 1:]:
                candidate_pairs.add((left, right))
    return _SketchPrefilter(
        sketches=sketches,
        band_postings=band_postings,
        candidate_pairs=candidate_pairs,
    )


def _write_sketch_tables(
    lookup_conn: sqlite3.Connection,
    sketch_prefilter: _SketchPrefilter,
) -> None:
    lookup_conn.executemany(
        "INSERT INTO psketch(bucket_id, sketch) VALUES (?, ?)",
        sorted(sketch_prefilter.sketches.items()),
    )
    lookup_conn.executemany(
        "INSERT INTO plsh_band(band_hash, bucket_id) VALUES (?, ?)",
        sorted(sketch_prefilter.band_postings),
    )


def _minhash_sketch(text: str, *, k_gram: int, size: int) -> list[int]:
    max_u64 = (1 << 64) - 1
    sketch = [max_u64] * size
    if len(text) < k_gram:
        return sketch
    for pos in range(0, len(text) - k_gram + 1):
        gram = text[pos:pos + k_gram]
        digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=16).digest()
        a = int.from_bytes(digest[:8], "big")
        b = int.from_bytes(digest[8:], "big") | 1
        for i in range(size):
            value = (a + i * b) & max_u64
            if value < sketch[i]:
                sketch[i] = value
    return sketch


def _lsh_band_hashes(blob: bytes, *, bands: int) -> list[str]:
    if bands <= 1:
        return ["0:" + hashlib.blake2b(blob, digest_size=8).hexdigest()]
    width = max(8, (len(blob) + bands - 1) // bands)
    hashes = []
    for idx, start in enumerate(range(0, len(blob), width)):
        part = blob[start:start + width]
        if not part:
            continue
        hashes.append(
            f"{idx}:"
            + hashlib.blake2b(part, digest_size=8).hexdigest()
        )
    return hashes


def _lookup_cluster_count(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return int(conn.execute("SELECT COUNT(*) FROM pcluster").fetchone()[0])
    finally:
        conn.close()


def _lookup_occurrence_count(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        return int(conn.execute("SELECT COUNT(*) FROM poccurrence").fetchone()[0])
    finally:
        conn.close()


def _emit(progress: TextIO | None, message: str) -> None:
    if progress is None:
        return
    progress.write(message + "\n")
    progress.flush()
