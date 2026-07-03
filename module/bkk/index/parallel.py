"""Discover exact repeated passages from a ``.bkkx`` trigram index."""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import unicodedata
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from bkk.chars.canonicalize import canonicalize_query
from bkk.chars.refs import CanonicalizationContext


# A single diff op vs. the cluster representative. Shapes:
#   ("=", n)              — n consecutive matching characters
#   ("s", rep_ch, occ_ch) — substitution
#   ("i", occ_ch)         — character present in the occurrence only
#   ("d", rep_ch)         — character present in the representative only
DiffOp = tuple


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
    edit_distance: int = 0
    # Occurrence's actual substring; empty when it matches the cluster
    # representative exactly (``edit_distance == 0``).
    text: str = ""
    # Run-length-encoded alignment ops vs. the cluster representative; empty
    # for exact occurrences.
    diff: tuple[DiffOp, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ParallelCluster:
    """A repeated passage and every location where it occurs."""

    cluster_id: str
    length: int
    occurrence_count: int
    text: str
    locations: tuple[ParallelLocation, ...]
    representative_edits: int = 0


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
    target_textid: str | None = None,
    target_juan_seq: int | None = None,
    bucket: str = "body",
    min_length: int = 12,
    min_occurrences: int = 2,
    max_postings: int = 500,
    include_contained: bool = False,
    context: int = 20,
    max_edits: int = 0,
    canon_ctx: CanonicalizationContext | None = None,
) -> list[ParallelCluster]:
    """Return repeated master-text passages from ``index_path``.

    ``seed`` narrows discovery to occurrences of a 1-6 character term, then
    extends around those occurrences. When ``seed`` is omitted, the function
    falls back to a full trigram-anchor scan, which is intended only for small
    indices. When ``target_textid`` is set, anchors come only from that text
    (and only ``target_juan_seq``, when supplied), but are compared with
    postings from the complete index; every retained pair therefore contains
    at least one target location. ``bucket`` is one of ``"front"``,
    ``"body"``, ``"back"``, or
    ``"all"``. ``max_edits`` (0-4) allows that many character edits
    (insertion/deletion/substitution) between occurrences when extending past
    the exact anchor; the anchor itself still has to match exactly. The
    function opens the index read-only and writes only TEMP tables.
    """
    if seed is not None and target_textid is not None:
        raise ValueError("seed and target_textid are mutually exclusive")
    if target_juan_seq is not None and target_textid is None:
        raise ValueError("target_juan_seq requires target_textid")
    if seed is not None:
        seed = unicodedata.normalize("NFC", seed)
        if canon_ctx is not None:
            seed = canonicalize_query(seed, canon_ctx)
        if not 1 <= len(seed) <= 6:
            raise ValueError("seed must be 1 to 6 characters")
    min_allowed = 1 if seed is not None else 3
    if min_length < min_allowed:
        raise ValueError(f"min_length must be at least {min_allowed}")
    if min_occurrences < 2:
        raise ValueError("min_occurrences must be at least 2")
    if max_postings < 2:
        raise ValueError("max_postings must be at least 2")
    if not 0 <= max_edits <= 4:
        raise ValueError("max_edits must be between 0 and 4")
    if bucket not in {"front", "body", "back", "all"}:
        raise ValueError("bucket must be one of: front, body, back, all")

    conn = sqlite3.connect(f"file:{Path(index_path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA temp_store = FILE")
        _prepare_temp_tables(
            conn,
            bucket,
            target_textid=target_textid,
            target_juan_seq=target_juan_seq,
        )
        cache = _BucketCache(conn)
        target_bucket_ids: set[int] | None = None
        if target_textid is not None:
            target_bucket_ids = {
                row[0]
                for row in conn.execute(
                    "SELECT bucket_id FROM temp.parallel_target"
                )
            }
            if target_juan_seq is None:
                exists = conn.execute(
                    "SELECT 1 FROM juan WHERE textid = ? LIMIT 1",
                    (target_textid,),
                ).fetchone()
            else:
                exists = conn.execute(
                    "SELECT 1 FROM juan WHERE textid = ? AND seq = ? LIMIT 1",
                    (target_textid, target_juan_seq),
                ).fetchone()
            if exists is None:
                target = (
                    target_textid
                    if target_juan_seq is None
                    else f"{target_textid}/{target_juan_seq}"
                )
                raise ValueError(f"target {target!r} is not present in the index")
        if seed is not None:
            postings = _seed_postings(conn, seed, max_postings)
            _record_spans_for_postings(
                conn, cache, postings, min_length, len(seed),
                max_edits=max_edits, target_bucket_ids=target_bucket_ids,
            )
        else:
            for gram in _usable_grams(
                conn, max_postings, targeted=target_textid is not None,
            ):
                postings = conn.execute(
                    "SELECT t.source_id AS bucket_id, t.position "
                    "FROM trigram t JOIN temp.parallel_source s "
                    "ON s.bucket_id = t.source_id "
                    "WHERE t.source_kind = 'bucket' AND t.gram = ? "
                    "ORDER BY t.source_id, t.position",
                    (gram,),
                ).fetchall()
                _record_spans_for_postings(
                    conn, cache, postings, min_length, 3,
                    max_edits=max_edits,
                    target_bucket_ids=target_bucket_ids,
                )
        if max_edits == 0:
            clusters = _clusters_from_spans(
                conn,
                cache,
                min_occurrences=min_occurrences,
                include_contained=include_contained,
                context=context,
            )
        else:
            clusters = _clusters_from_spans_fuzzy(
                conn,
                cache,
                max_edits=max_edits,
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


def _prepare_temp_tables(
    conn: sqlite3.Connection,
    bucket: str,
    *,
    target_textid: str | None = None,
    target_juan_seq: int | None = None,
) -> None:
    conn.executescript(
        """
        CREATE TEMP TABLE parallel_source (
          bucket_id INTEGER PRIMARY KEY
        );
        CREATE TEMP TABLE parallel_target (
          bucket_id INTEGER PRIMARY KEY
        );
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
    if target_textid is not None:
        sql = (
            "INSERT INTO temp.parallel_target(bucket_id) "
            "SELECT b.bucket_id FROM bucket b "
            "JOIN juan j ON j.juan_id = b.juan_id "
            "JOIN temp.parallel_source s ON s.bucket_id = b.bucket_id "
            "WHERE j.textid = ?"
        )
        params: tuple = (target_textid,)
        if target_juan_seq is not None:
            sql += " AND j.seq = ?"
            params += (target_juan_seq,)
        conn.execute(sql, params)


def _usable_grams(
    conn: sqlite3.Connection,
    max_postings: int,
    *,
    targeted: bool = False,
) -> Iterator[str]:
    if targeted:
        rows = conn.execute(
            "SELECT candidate.gram, COUNT(*) AS n "
            "FROM ("
            "  SELECT DISTINCT t.gram FROM trigram t "
            "  JOIN temp.parallel_target target "
            "    ON target.bucket_id = t.source_id "
            "  WHERE t.source_kind = 'bucket'"
            ") candidate "
            "JOIN trigram all_t ON all_t.gram = candidate.gram "
            "JOIN temp.parallel_source source "
            "  ON source.bucket_id = all_t.source_id "
            "WHERE all_t.source_kind = 'bucket' "
            "GROUP BY candidate.gram HAVING n BETWEEN 2 AND ? "
            "ORDER BY n ASC, candidate.gram",
            (max_postings,),
        )
    else:
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
    *,
    max_edits: int = 0,
    target_bucket_ids: set[int] | None = None,
) -> None:
    rows = []
    for i, left in enumerate(postings):
        for right in postings[i + 1:]:
            if (
                target_bucket_ids is not None
                and left["bucket_id"] not in target_bucket_ids
                and right["bucket_id"] not in target_bucket_ids
            ):
                continue
            if max_edits == 0:
                span_a, span_b = _maximal_pair_span(
                    cache,
                    left["bucket_id"],
                    left["position"],
                    right["bucket_id"],
                    right["position"],
                    seed_length,
                )
                edits = 0
            else:
                span_a, span_b, edits = _maximal_pair_span_fuzzy(
                    cache,
                    left["bucket_id"],
                    left["position"],
                    right["bucket_id"],
                    right["position"],
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
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO temp.parallel_pair_span"
            "(bucket_a, start_a, end_a, bucket_b, start_b, end_b, edits) "
            "VALUES (?,?,?,?,?,?,?)",
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


def _maximal_pair_span_fuzzy(
    cache: _BucketCache,
    bucket_a: int,
    pos_a: int,
    bucket_b: int,
    pos_b: int,
    seed_length: int,
    max_edits: int,
) -> tuple[_Span | None, _Span | None, int]:
    """Like ``_maximal_pair_span`` but allows up to ``max_edits`` total
    insertions/deletions/substitutions split between the left and right
    extensions of the exact-matching seed anchor."""
    if bucket_a == bucket_b and pos_a == pos_b:
        return None, None, 0
    info_a = cache.get(bucket_a)
    info_b = cache.get(bucket_b)
    text_a = info_a.text
    text_b = info_b.text
    if text_a[pos_a:pos_a + seed_length] != text_b[pos_b:pos_b + seed_length]:
        return None, None, 0

    right_best = _extend_within_edits(
        text_a[pos_a + seed_length:],
        text_b[pos_b + seed_length:],
        max_edits,
    )
    left_best = _extend_within_edits(
        text_a[:pos_a][::-1],
        text_b[:pos_b][::-1],
        max_edits,
    )

    best_total = -1
    best_la = best_lb = best_ra = best_rb = 0
    best_le = best_re = 0
    for el in range(max_edits + 1):
        er = max_edits - el
        la, lb, le = left_best[el]
        ra, rb, re = right_best[er]
        total = la + lb + ra + rb
        if total > best_total or (
            total == best_total and le + re < best_le + best_re
        ):
            best_total = total
            best_la, best_lb = la, lb
            best_ra, best_rb = ra, rb
            best_le, best_re = le, re

    start_a = pos_a - best_la
    end_a = pos_a + seed_length + best_ra
    start_b = pos_b - best_lb
    end_b = pos_b + seed_length + best_rb
    if bucket_a == bucket_b and start_a < end_b and start_b < end_a:
        return None, None, 0

    span_a = _Span(bucket_a, start_a, end_a)
    span_b = _Span(bucket_b, start_b, end_b)
    edits = best_le + best_re
    if (span_b.bucket_id, span_b.start, span_b.end) < (
        span_a.bucket_id,
        span_a.start,
        span_a.end,
    ):
        return span_b, span_a, edits
    return span_a, span_b, edits


def _extend_within_edits(
    a: str, b: str, k: int,
) -> list[tuple[int, int, int]]:
    """Return ``best[0..k]`` where ``best[e] = (delta_a, delta_b, edits_used)``
    is the longest forward extension of ``a`` vs ``b`` that ends on a
    character match (or empty) and uses at most ``e`` edits.

    Uses a Landau-Vishkin-style band: ``L[e][d_idx]`` holds the furthest
    position in ``a`` reachable on diagonal ``d = i - j`` with exactly ``e``
    edits, after extending greedily through matching characters.
    """
    max_a, max_b = len(a), len(b)
    NEG = -1

    if k <= 0 or max_a == 0 or max_b == 0:
        i = 0
        while i < max_a and i < max_b and a[i] == b[i]:
            i += 1
        return [(i, i, 0)] * (k + 1)

    width = 2 * k + 1

    def lcp_from(i: int, j: int) -> int:
        while i < max_a and j < max_b and a[i] == b[j]:
            i += 1
            j += 1
        return i

    L = [[NEG] * width for _ in range(k + 1)]
    best: list[tuple[int, int, int]] = []

    start_i = lcp_from(0, 0)
    L[0][0 + k] = start_i
    best.append((start_i, start_i, 0))

    for e in range(1, k + 1):
        cur = best[e - 1]
        for d in range(-e, e + 1):
            d_idx = d + k
            cand = NEG
            # Substitution: from (i-1, j-1) on the same diagonal.
            prev = L[e - 1][d_idx]
            if prev != NEG and prev < max_a and (prev - d) < max_b:
                if prev + 1 > cand:
                    cand = prev + 1
            # Advance in a only (gap in b): from diagonal d-1.
            if d - 1 >= -k:
                prev = L[e - 1][d_idx - 1]
                if prev != NEG and prev < max_a:
                    if prev + 1 > cand:
                        cand = prev + 1
            # Advance in b only (gap in a): from diagonal d+1, i unchanged.
            if d + 1 <= k:
                prev = L[e - 1][d_idx + 1]
                if prev != NEG and (prev - d) < max_b:
                    if prev > cand:
                        cand = prev
            if cand == NEG:
                continue
            if cand > max_a:
                cand = max_a
            j = cand - d
            if j < 0 or j > max_b:
                continue
            new_i = lcp_from(cand, j)
            L[e][d_idx] = new_i
            # Only treat as a candidate best when the LCP step extended past
            # the edit (so the span ends on a matching character), or we
            # ran out of input on at least one side cleanly.
            ended_in_match = new_i > cand
            ran_out = new_i == max_a or (new_i - d) == max_b
            if not (ended_in_match or ran_out):
                continue
            new_j = new_i - d
            if new_i + new_j > cur[0] + cur[1]:
                cur = (new_i, new_j, e)
        best.append(cur)

    return best


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


def _clusters_from_spans_fuzzy(
    conn: sqlite3.Connection,
    cache: _BucketCache,
    *,
    max_edits: int,
    min_occurrences: int,
    include_contained: bool,
    context: int,
) -> list[ParallelCluster]:
    """Cluster fuzzy pair spans by union-find, then re-score each occurrence
    against the cluster's representative (longest) span."""
    nodes: set[tuple[int, int, int]] = set()
    edges: list[tuple[tuple[int, int, int], tuple[int, int, int]]] = []
    for row in conn.execute(
        "SELECT bucket_a, start_a, end_a, bucket_b, start_b, end_b "
        "FROM temp.parallel_pair_span"
    ):
        a = (row["bucket_a"], row["start_a"], row["end_a"])
        b = (row["bucket_b"], row["start_b"], row["end_b"])
        nodes.add(a)
        nodes.add(b)
        edges.append((a, b))

    parent: dict[tuple[int, int, int], tuple[int, int, int]] = {n: n for n in nodes}

    def find(x: tuple[int, int, int]) -> tuple[int, int, int]:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    groups: dict[tuple[int, int, int], list[tuple[int, int, int]]] = {}
    for n in nodes:
        groups.setdefault(find(n), []).append(n)

    drift_cap = 2 * max_edits
    KeptEntry = tuple[tuple[int, int, int], int, str, tuple[DiffOp, ...]]
    raw_clusters: list[tuple[str, int, list[KeptEntry], int]] = []
    for members in groups.values():
        if len(members) < min_occurrences:
            continue
        rep = max(members, key=lambda m: (m[2] - m[1], -m[0], -m[1]))
        rep_text = cache.get(rep[0]).text[rep[1]:rep[2]]
        kept: list[KeptEntry] = []
        max_d = 0
        for m in members:
            if m == rep:
                kept.append((m, 0, "", ()))
                continue
            info = cache.get(m[0])
            mt = info.text[m[1]:m[2]]
            d = _bounded_edit_distance(rep_text, mt, drift_cap)
            if d <= drift_cap:
                ops = _align_ops(rep_text, mt) if d > 0 else ()
                kept.append((m, d, mt, ops))
                if d > max_d:
                    max_d = d
        if len(kept) < min_occurrences:
            continue
        raw_clusters.append((rep_text, len(rep_text), kept, max_d))

    raw_clusters.sort(
        key=lambda c: (-c[1], c[3], c[0], min(entry[0] for entry in c[2])),
    )

    if not include_contained:
        raw_clusters = _remove_contained_clusters_fuzzy(raw_clusters, min_occurrences)

    clusters: list[ParallelCluster] = []
    for idx, (text, length, kept, max_d) in enumerate(raw_clusters, 1):
        kept_sorted = sorted(kept, key=lambda entry: entry[0])
        locations = tuple(
            _make_location(
                conn, cache, m[0], m[1], m[2], context,
                edit_distance=d, text=mt, diff=ops,
            )
            for m, d, mt, ops in kept_sorted
        )
        clusters.append(
            ParallelCluster(
                cluster_id=f"parallel-{idx:06d}",
                length=length,
                occurrence_count=len(locations),
                text=text,
                locations=locations,
                representative_edits=max_d,
            )
        )
    return clusters


def _remove_contained_clusters_fuzzy(
    clusters: list,
    min_occurrences: int,
) -> list:
    kept: list = []
    for text, length, members, max_d in clusters:
        contained = False
        spans = {entry[0] for entry in members}
        for _k_text, k_length, k_members, _k_d in kept:
            if k_length < length:
                continue
            k_spans = {entry[0] for entry in k_members}
            contained_count = sum(
                1 for span in spans if _span_contained_in_any(span, k_spans)
            )
            if contained_count >= min_occurrences:
                contained = True
                break
        if not contained:
            kept.append((text, length, members, max_d))
    return kept


def _align_ops(rep: str, occ: str) -> tuple[DiffOp, ...]:
    """Levenshtein alignment of ``rep`` against ``occ``, run-length encoded.

    Strings here are short (at most a few dozen characters from a parallel
    span), so the full ``O(n*m)`` DP is fine — this is called once per
    occurrence after fuzzy clustering, not in the seed-extension hot loop.
    """
    n, m = len(rep), len(occ)
    if n == 0 and m == 0:
        return ()
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        dp[i][0] = i
    for j in range(1, m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        row = dp[i]
        prev = dp[i - 1]
        ri = rep[i - 1]
        for j in range(1, m + 1):
            cost = 0 if ri == occ[j - 1] else 1
            v = prev[j - 1] + cost
            d = prev[j] + 1
            if d < v:
                v = d
            d = row[j - 1] + 1
            if d < v:
                v = d
            row[j] = v

    raw: list[DiffOp] = []
    i, j = n, m
    while i > 0 or j > 0:
        if (
            i > 0
            and j > 0
            and rep[i - 1] == occ[j - 1]
            and dp[i][j] == dp[i - 1][j - 1]
        ):
            raw.append(("=", rep[i - 1]))
            i -= 1
            j -= 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            raw.append(("s", rep[i - 1], occ[j - 1]))
            i -= 1
            j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1:
            raw.append(("d", rep[i - 1]))
            i -= 1
        else:
            raw.append(("i", occ[j - 1]))
            j -= 1

    ops: list[DiffOp] = []
    run = 0
    for op in reversed(raw):
        if op[0] == "=":
            run += 1
        else:
            if run:
                ops.append(("=", run))
                run = 0
            ops.append(op)
    if run:
        ops.append(("=", run))
    return tuple(ops)


def _bounded_edit_distance(s1: str, s2: str, cap: int) -> int:
    """Levenshtein distance between ``s1`` and ``s2``, capped at ``cap + 1``.

    Returns ``cap + 1`` whenever the true distance exceeds ``cap``; safe to
    use as an "exceeds budget" sentinel without paying for the full DP."""
    if cap < 0:
        return 0 if s1 == s2 else 1
    n, m = len(s1), len(s2)
    if abs(n - m) > cap:
        return cap + 1
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [cap + 1] * m
        row_min = cur[0]
        lo = max(1, i - cap)
        hi = min(m, i + cap)
        for j in range(lo, hi + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            v = prev[j - 1] + cost
            if prev[j] + 1 < v:
                v = prev[j] + 1
            if cur[j - 1] + 1 < v:
                v = cur[j - 1] + 1
            cur[j] = v
            if v < row_min:
                row_min = v
        if row_min > cap:
            return cap + 1
        prev = cur
    return prev[m] if prev[m] <= cap else cap + 1


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
    edit_distance: int = 0,
    text: str = "",
    diff: tuple[DiffOp, ...] = (),
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
        edit_distance=edit_distance,
        text=text,
        diff=diff,
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
        "representative_edits",
        "text",
        "textid",
        "juan_seq",
        "bucket",
        "start",
        "end",
        "toc_label",
        "left",
        "right",
        "edit_distance",
    ])
    for cluster in clusters:
        for loc in cluster.locations:
            writer.writerow([
                cluster.cluster_id,
                cluster.length,
                cluster.occurrence_count,
                cluster.representative_edits,
                cluster.text,
                loc.textid,
                loc.juan_seq,
                loc.bucket,
                loc.start,
                loc.end,
                loc.toc_label or "",
                loc.left,
                loc.right,
                loc.edit_distance,
            ])


def _cluster_to_dict(cluster: ParallelCluster) -> dict:
    return {
        "cluster_id": cluster.cluster_id,
        "length": cluster.length,
        "occurrence_count": cluster.occurrence_count,
        "representative_edits": cluster.representative_edits,
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
                "edit_distance": loc.edit_distance,
            }
            for loc in cluster.locations
        ],
    }


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _span_sort_key(spans: set[tuple[int, int, int]]) -> tuple[int, int, int]:
    return min(spans)
