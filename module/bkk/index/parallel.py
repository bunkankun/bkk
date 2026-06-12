"""Discover exact repeated passages from a ``.bkkx`` trigram index."""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import unicodedata
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


@dataclass(frozen=True)
class ParallelLocation:
    """One occurrence of a repeated passage."""

    textid: str
    juan_seq: int
    bucket: str
    bucket_id: int
    start: int
    end: int
    toc_label: str | None
    left: str
    right: str


@dataclass(frozen=True)
class ParallelCluster:
    """A repeated passage and every location where it occurs."""

    cluster_id: str
    length: int
    occurrence_count: int
    text: str
    locations: tuple[ParallelLocation, ...]


@dataclass(frozen=True)
class _BucketInfo:
    bucket_id: int
    textid: str
    juan_seq: int
    kind: str
    text: str


@dataclass(frozen=True)
class _Span:
    bucket_id: int
    start: int
    end: int


class _BucketCache:
    def __init__(self, conn: sqlite3.Connection, capacity: int = 128):
        self._conn = conn
        self._capacity = max(1, capacity)
        self._cache: OrderedDict[int, _BucketInfo] = OrderedDict()

    def get(self, bucket_id: int) -> _BucketInfo:
        cached = self._cache.get(bucket_id)
        if cached is not None:
            self._cache.move_to_end(bucket_id)
            return cached
        row = self._conn.execute(
            "SELECT b.bucket_id, b.text, b.kind, j.textid, j.seq "
            "FROM bucket b JOIN juan j ON b.juan_id = j.juan_id "
            "WHERE b.bucket_id = ?",
            (bucket_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"bucket_id {bucket_id} not found")
        info = _BucketInfo(
            bucket_id=row["bucket_id"],
            textid=row["textid"],
            juan_seq=row["seq"],
            kind=row["kind"],
            text=row["text"],
        )
        self._cache[bucket_id] = info
        if len(self._cache) > self._capacity:
            self._cache.popitem(last=False)
        return info


def discover_parallel_passages(
    index_path: Path | str,
    *,
    seed: str | None = None,
    bucket: str = "body",
    min_length: int = 12,
    min_occurrences: int = 2,
    max_postings: int = 500,
    include_contained: bool = False,
    context: int = 20,
) -> list[ParallelCluster]:
    """Return exact repeated master-text passages from ``index_path``.

    ``seed`` narrows discovery to occurrences of a 1-6 character term, then
    extends around those occurrences. When ``seed`` is omitted, the function
    falls back to a full trigram-anchor scan, which is intended only for small
    indices. ``bucket`` is one of ``"front"``, ``"body"``, ``"back"``, or
    ``"all"``. The function opens the index read-only and writes only TEMP
    tables.
    """
    if seed is not None:
        seed = unicodedata.normalize("NFC", seed)
        if not 1 <= len(seed) <= 6:
            raise ValueError("seed must be 1 to 6 characters")
    min_allowed = 1 if seed is not None else 3
    if min_length < min_allowed:
        raise ValueError(f"min_length must be at least {min_allowed}")
    if min_occurrences < 2:
        raise ValueError("min_occurrences must be at least 2")
    if max_postings < 2:
        raise ValueError("max_postings must be at least 2")
    if bucket not in {"front", "body", "back", "all"}:
        raise ValueError("bucket must be one of: front, body, back, all")

    conn = sqlite3.connect(f"file:{Path(index_path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA temp_store = FILE")
        _prepare_temp_tables(conn, bucket)
        cache = _BucketCache(conn)
        if seed is not None:
            postings = _seed_postings(conn, seed, max_postings)
            _record_spans_for_postings(
                conn, cache, postings, min_length, len(seed),
            )
        else:
            for gram in _usable_grams(conn, max_postings):
                postings = conn.execute(
                    "SELECT t.source_id AS bucket_id, t.position "
                    "FROM trigram t JOIN temp.parallel_source s "
                    "ON s.bucket_id = t.source_id "
                    "WHERE t.source_kind = 'bucket' AND t.gram = ? "
                    "ORDER BY t.source_id, t.position",
                    (gram,),
                ).fetchall()
                _record_spans_for_postings(conn, cache, postings, min_length, 3)
        clusters = _clusters_from_spans(
            conn,
            cache,
            min_occurrences=min_occurrences,
            include_contained=include_contained,
            context=context,
        )
        return clusters
    finally:
        conn.close()


def write_parallel_report(
    clusters: list[ParallelCluster],
    out: Path | str | TextIO,
    *,
    format: str = "jsonl",
) -> None:
    """Write ``clusters`` as JSONL or TSV."""
    if format not in {"jsonl", "tsv"}:
        raise ValueError("format must be 'jsonl' or 'tsv'")
    if hasattr(out, "write"):
        _write_parallel_report(clusters, out, format=format)
        return
    path = Path(out)
    with path.open("w", encoding="utf-8", newline="") as f:
        _write_parallel_report(clusters, f, format=format)


def _prepare_temp_tables(conn: sqlite3.Connection, bucket: str) -> None:
    conn.executescript(
        """
        CREATE TEMP TABLE parallel_source (
          bucket_id INTEGER PRIMARY KEY
        );
        CREATE TEMP TABLE parallel_pair_span (
          bucket_a INTEGER NOT NULL,
          start_a  INTEGER NOT NULL,
          end_a    INTEGER NOT NULL,
          bucket_b INTEGER NOT NULL,
          start_b  INTEGER NOT NULL,
          end_b    INTEGER NOT NULL,
          PRIMARY KEY (bucket_a, start_a, end_a, bucket_b, start_b, end_b)
        );
        """
    )
    if bucket == "all":
        conn.execute(
            "INSERT INTO temp.parallel_source(bucket_id) "
            "SELECT bucket_id FROM bucket WHERE length(text) >= 3"
        )
    else:
        conn.execute(
            "INSERT INTO temp.parallel_source(bucket_id) "
            "SELECT bucket_id FROM bucket WHERE kind = ? AND length(text) >= 3",
            (bucket,),
        )


def _usable_grams(conn: sqlite3.Connection, max_postings: int) -> Iterator[str]:
    rows = conn.execute(
        "SELECT t.gram, COUNT(*) AS n "
        "FROM trigram t JOIN temp.parallel_source s ON s.bucket_id = t.source_id "
        "WHERE t.source_kind = 'bucket' "
        "GROUP BY t.gram HAVING n BETWEEN 2 AND ? "
        "ORDER BY n ASC, t.gram",
        (max_postings,),
    )
    for row in rows:
        yield row["gram"]


def _seed_postings(
    conn: sqlite3.Connection,
    seed: str,
    max_postings: int,
) -> list[sqlite3.Row | dict[str, int]]:
    if len(seed) == 3:
        n = conn.execute(
            "SELECT COUNT(*) FROM trigram t JOIN temp.parallel_source s "
            "ON s.bucket_id = t.source_id "
            "WHERE t.source_kind = 'bucket' AND t.gram = ?",
            (seed,),
        ).fetchone()[0]
        if n > max_postings:
            raise ValueError(
                f"seed {seed!r} occurs {n} times; choose a more specific seed "
                f"or raise --max-postings"
            )
        return conn.execute(
            "SELECT t.source_id AS bucket_id, t.position "
            "FROM trigram t JOIN temp.parallel_source s "
            "ON s.bucket_id = t.source_id "
            "WHERE t.source_kind = 'bucket' AND t.gram = ? "
            "ORDER BY t.source_id, t.position",
            (seed,),
        ).fetchall()

    if len(seed) > 3:
        anchor = seed[:3]
        postings: list[dict[str, int]] = []
        cache: dict[int, str] = {}
        for row in conn.execute(
            "SELECT t.source_id AS bucket_id, t.position "
            "FROM trigram t JOIN temp.parallel_source s "
            "ON s.bucket_id = t.source_id "
            "WHERE t.source_kind = 'bucket' AND t.gram = ? "
            "ORDER BY t.source_id, t.position",
            (anchor,),
        ):
            bucket_id = row["bucket_id"]
            pos = row["position"]
            text = cache.get(bucket_id)
            if text is None:
                text_row = conn.execute(
                    "SELECT text FROM bucket WHERE bucket_id = ?",
                    (bucket_id,),
                ).fetchone()
                text = text_row["text"]
                cache[bucket_id] = text
            if text[pos:pos + len(seed)] != seed:
                continue
            postings.append({"bucket_id": bucket_id, "position": pos})
            if len(postings) > max_postings:
                raise ValueError(
                    f"seed {seed!r} occurs more than {max_postings} times; "
                    f"choose a more specific seed or raise --max-postings"
                )
        return postings

    postings: list[dict[str, int]] = []
    for row in conn.execute(
        "SELECT b.bucket_id, b.text FROM bucket b "
        "JOIN temp.parallel_source s ON s.bucket_id = b.bucket_id "
        "ORDER BY b.bucket_id"
    ):
        start = 0
        text = row["text"]
        while True:
            pos = text.find(seed, start)
            if pos < 0:
                break
            postings.append({"bucket_id": row["bucket_id"], "position": pos})
            if len(postings) > max_postings:
                raise ValueError(
                    f"seed {seed!r} occurs more than {max_postings} times; "
                    f"choose a more specific seed or raise --max-postings"
                )
            start = pos + 1
    return postings


def _record_spans_for_postings(
    conn: sqlite3.Connection,
    cache: _BucketCache,
    postings: list[sqlite3.Row | dict[str, int]],
    min_length: int,
    seed_length: int,
) -> None:
    rows = []
    for i, left in enumerate(postings):
        for right in postings[i + 1:]:
            span_a, span_b = _maximal_pair_span(
                cache,
                left["bucket_id"],
                left["position"],
                right["bucket_id"],
                right["position"],
                seed_length,
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
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO temp.parallel_pair_span"
            "(bucket_a, start_a, end_a, bucket_b, start_b, end_b) "
            "VALUES (?,?,?,?,?,?)",
            rows,
        )


def _maximal_pair_span(
    cache: _BucketCache,
    bucket_a: int,
    pos_a: int,
    bucket_b: int,
    pos_b: int,
    seed_length: int,
) -> tuple[_Span | None, _Span | None]:
    if bucket_a == bucket_b and pos_a == pos_b:
        return None, None
    info_a = cache.get(bucket_a)
    info_b = cache.get(bucket_b)
    text_a = info_a.text
    text_b = info_b.text
    if text_a[pos_a:pos_a + seed_length] != text_b[pos_b:pos_b + seed_length]:
        return None, None

    left = 0
    while (
        pos_a - left > 0
        and pos_b - left > 0
        and text_a[pos_a - left - 1] == text_b[pos_b - left - 1]
    ):
        left += 1

    right = seed_length
    while (
        pos_a + right < len(text_a)
        and pos_b + right < len(text_b)
        and text_a[pos_a + right] == text_b[pos_b + right]
    ):
        right += 1

    start_a = pos_a - left
    end_a = pos_a + right
    start_b = pos_b - left
    end_b = pos_b + right
    if bucket_a == bucket_b and start_a < end_b and start_b < end_a:
        return None, None

    span_a = _Span(bucket_a, start_a, end_a)
    span_b = _Span(bucket_b, start_b, end_b)
    if (span_b.bucket_id, span_b.start, span_b.end) < (
        span_a.bucket_id,
        span_a.start,
        span_a.end,
    ):
        return span_b, span_a
    return span_a, span_b


def _clusters_from_spans(
    conn: sqlite3.Connection,
    cache: _BucketCache,
    *,
    min_occurrences: int,
    include_contained: bool,
    context: int,
) -> list[ParallelCluster]:
    grouped: dict[tuple[str, int], dict[str, object]] = {}
    for row in conn.execute(
        "SELECT bucket_a, start_a, end_a, bucket_b, start_b, end_b "
        "FROM temp.parallel_pair_span "
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
            _make_location(conn, cache, bucket_id, start, end, context)
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


def _remove_contained_clusters(
    clusters: list[tuple[str, int, set[tuple[int, int, int]]]],
    min_occurrences: int,
) -> list[tuple[str, int, set[tuple[int, int, int]]]]:
    kept: list[tuple[str, int, set[tuple[int, int, int]]]] = []
    for text, length, spans in clusters:
        contained = False
        for _k_text, k_length, k_spans in kept:
            if k_length < length:
                continue
            contained_count = sum(
                1 for span in spans if _span_contained_in_any(span, k_spans)
            )
            if contained_count >= min_occurrences:
                contained = True
                break
        if not contained:
            kept.append((text, length, spans))
    return kept


def _span_contained_in_any(
    span: tuple[int, int, int],
    candidates: set[tuple[int, int, int]],
) -> bool:
    bucket_id, start, end = span
    return any(
        bucket_id == c_bucket and c_start <= start and c_end >= end
        for c_bucket, c_start, c_end in candidates
    )


def _make_location(
    conn: sqlite3.Connection,
    cache: _BucketCache,
    bucket_id: int,
    start: int,
    end: int,
    context: int,
) -> ParallelLocation:
    info = cache.get(bucket_id)
    return ParallelLocation(
        textid=info.textid,
        juan_seq=info.juan_seq,
        bucket=info.kind,
        bucket_id=bucket_id,
        start=start,
        end=end,
        toc_label=_toc_label(conn, info.textid, info.juan_seq, info.kind, start),
        left=info.text[max(0, start - context):start],
        right=info.text[end:end + context],
    )


def _toc_label(
    conn: sqlite3.Connection,
    textid: str,
    juan_seq: int,
    bucket: str,
    offset: int,
) -> str | None:
    row = conn.execute(
        "SELECT label FROM toc "
        "WHERE textid = ? AND juan_seq = ? AND bucket = ? "
        "AND span_start <= ? AND span_end > ? "
        "ORDER BY (span_end - span_start) ASC LIMIT 1",
        (textid, juan_seq, bucket, offset, offset),
    ).fetchone()
    return row["label"] if row else None


def _write_parallel_report(
    clusters: list[ParallelCluster],
    out: TextIO,
    *,
    format: str,
) -> None:
    if format == "jsonl":
        for cluster in clusters:
            out.write(json.dumps(_cluster_to_dict(cluster), ensure_ascii=False) + "\n")
        return

    writer = csv.writer(out, delimiter="\t", lineterminator="\n")
    writer.writerow([
        "cluster_id",
        "length",
        "occurrence_count",
        "text",
        "textid",
        "juan_seq",
        "bucket",
        "start",
        "end",
        "toc_label",
        "left",
        "right",
    ])
    for cluster in clusters:
        for loc in cluster.locations:
            writer.writerow([
                cluster.cluster_id,
                cluster.length,
                cluster.occurrence_count,
                cluster.text,
                loc.textid,
                loc.juan_seq,
                loc.bucket,
                loc.start,
                loc.end,
                loc.toc_label or "",
                loc.left,
                loc.right,
            ])


def _cluster_to_dict(cluster: ParallelCluster) -> dict:
    return {
        "cluster_id": cluster.cluster_id,
        "length": cluster.length,
        "occurrence_count": cluster.occurrence_count,
        "text": cluster.text,
        "locations": [
            {
                "textid": loc.textid,
                "juan_seq": loc.juan_seq,
                "bucket": loc.bucket,
                "start": loc.start,
                "end": loc.end,
                "toc_label": loc.toc_label,
                "left": loc.left,
                "right": loc.right,
            }
            for loc in cluster.locations
        ],
    }


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _span_sort_key(spans: set[tuple[int, int, int]]) -> tuple[int, int, int]:
    return min(spans)
