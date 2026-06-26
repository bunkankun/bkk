"""Aggregate parallel-scan clusters into juan-pair duplication rows.

Reuses ``parallel_scan.discover_parallel_passages_scan`` as the engine; this
module only post-processes its clusters into one row per (juan_a, juan_b),
collapsing overlapping spans into unique covered positions per side.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from .parallel import ParallelCluster, ParallelLocation
from .parallel_scan import discover_parallel_passages_scan


@dataclass(frozen=True)
class JuanRef:
    textid: str
    juan_seq: int
    bucket: str
    bucket_id: int


@dataclass(frozen=True)
class JuanPairDuplication:
    """One (juan_a, juan_b) row. For intra-juan rows, ``a == b``."""

    a: JuanRef
    b: JuanRef
    chars_a: int
    chars_b: int
    juan_length_a: int
    juan_length_b: int
    longest_span: int
    cluster_count: int

    @property
    def coverage_a(self) -> float:
        return self.chars_a / self.juan_length_a if self.juan_length_a else 0.0

    @property
    def coverage_b(self) -> float:
        return self.chars_b / self.juan_length_b if self.juan_length_b else 0.0


def find_duplicated_juan(
    index_path: Path | str,
    *,
    bucket: str = "body",
    min_length: int = 200,
    anchor_length: int = 12,
    min_occurrences: int = 2,
    max_anchor_occurrences: int = 200,
    partitions: int = 256,
    work_dir: Path | str | None = None,
    min_pair_chars: int = 100,
    progress: TextIO | None = None,
) -> list[JuanPairDuplication]:
    """Run a parallel-scan and aggregate clusters into juan-pair rows."""
    clusters, _stats = discover_parallel_passages_scan(
        index_path,
        bucket=bucket,
        min_length=min_length,
        anchor_length=anchor_length,
        min_occurrences=min_occurrences,
        max_anchor_occurrences=max_anchor_occurrences,
        partitions=partitions,
        work_dir=work_dir,
        include_contained=False,
        context=0,
        progress=progress,
    )
    rows = _aggregate_pairs(clusters)
    if rows:
        _attach_juan_lengths(rows, index_path)
    rows = [r for r in rows if min(r.chars_a, r.chars_b) >= min_pair_chars]
    rows.sort(
        key=lambda r: (
            -min(r.chars_a, r.chars_b),
            -r.longest_span,
            r.a.textid, r.a.juan_seq, r.b.textid, r.b.juan_seq,
        ),
    )
    return rows


def _aggregate_pairs(clusters: list[ParallelCluster]) -> list[JuanPairDuplication]:
    # pair_key -> {bucket_id -> list[(start, end)]}
    intervals: dict[tuple[int, int], dict[int, list[tuple[int, int]]]] = defaultdict(
        lambda: defaultdict(list),
    )
    longest: dict[tuple[int, int], int] = defaultdict(int)
    clusters_per_pair: dict[tuple[int, int], int] = defaultdict(int)
    refs: dict[int, JuanRef] = {}

    for cluster in clusters:
        locs = cluster.locations
        seen_pairs: set[tuple[int, int]] = set()
        for i in range(len(locs)):
            li = locs[i]
            refs.setdefault(li.bucket_id, _ref_from_location(li))
            for j in range(i + 1, len(locs)):
                lj = locs[j]
                refs.setdefault(lj.bucket_id, _ref_from_location(lj))
                key = _pair_key(li.bucket_id, lj.bucket_id)
                intervals[key][li.bucket_id].append((li.start, li.end))
                intervals[key][lj.bucket_id].append((lj.start, lj.end))
                if cluster.length > longest[key]:
                    longest[key] = cluster.length
                seen_pairs.add(key)
        for key in seen_pairs:
            clusters_per_pair[key] += 1

    rows: list[JuanPairDuplication] = []
    for key, side_intervals in intervals.items():
        a_id, b_id = key
        a_ref = refs[a_id]
        b_ref = refs[b_id]
        chars_a = _merged_length(side_intervals[a_id])
        if a_id == b_id:
            chars_b = chars_a
        else:
            chars_b = _merged_length(side_intervals[b_id])
        rows.append(JuanPairDuplication(
            a=a_ref,
            b=b_ref,
            chars_a=chars_a,
            chars_b=chars_b,
            juan_length_a=0,
            juan_length_b=0,
            longest_span=longest[key],
            cluster_count=clusters_per_pair[key],
        ))
    return rows


def _pair_key(a_id: int, b_id: int) -> tuple[int, int]:
    return (a_id, b_id) if a_id <= b_id else (b_id, a_id)


def _ref_from_location(loc: ParallelLocation) -> JuanRef:
    return JuanRef(
        textid=loc.textid,
        juan_seq=loc.juan_seq,
        bucket=loc.bucket,
        bucket_id=loc.bucket_id,
    )


def _merged_length(spans: list[tuple[int, int]]) -> int:
    if not spans:
        return 0
    spans = sorted(spans)
    total = 0
    cur_start, cur_end = spans[0]
    for start, end in spans[1:]:
        if start <= cur_end:
            if end > cur_end:
                cur_end = end
        else:
            total += cur_end - cur_start
            cur_start, cur_end = start, end
    total += cur_end - cur_start
    return total


def _attach_juan_lengths(
    rows: list[JuanPairDuplication],
    index_path: Path | str,
) -> list[JuanPairDuplication]:
    bucket_ids = {r.a.bucket_id for r in rows} | {r.b.bucket_id for r in rows}
    conn = sqlite3.connect(f"file:{Path(index_path)}?mode=ro", uri=True)
    try:
        lengths: dict[int, int] = {}
        placeholders = ",".join("?" * len(bucket_ids))
        for bid, n in conn.execute(
            f"SELECT bucket_id, length(text) FROM bucket "
            f"WHERE bucket_id IN ({placeholders})",
            tuple(bucket_ids),
        ):
            lengths[bid] = n
    finally:
        conn.close()
    for i, r in enumerate(rows):
        rows[i] = JuanPairDuplication(
            a=r.a,
            b=r.b,
            chars_a=r.chars_a,
            chars_b=r.chars_b,
            juan_length_a=lengths.get(r.a.bucket_id, 0),
            juan_length_b=lengths.get(r.b.bucket_id, 0),
            longest_span=r.longest_span,
            cluster_count=r.cluster_count,
        )
    return rows


def write_duplications_report(
    rows: list[JuanPairDuplication],
    out: Path | str | TextIO,
    *,
    format: str = "tsv",
) -> None:
    """Write juan-pair duplication rows as TSV or JSONL."""
    if format not in {"tsv", "jsonl"}:
        raise ValueError("format must be 'tsv' or 'jsonl'")
    if hasattr(out, "write"):
        _write(rows, out, format=format)
        return
    with Path(out).open("w", encoding="utf-8", newline="") as f:
        _write(rows, f, format=format)


def _write(
    rows: list[JuanPairDuplication],
    out: TextIO,
    *,
    format: str,
) -> None:
    if format == "jsonl":
        for r in rows:
            out.write(json.dumps(_row_to_dict(r), ensure_ascii=False) + "\n")
        return
    writer = csv.writer(out, delimiter="\t", lineterminator="\n")
    writer.writerow([
        "textid_a", "juan_seq_a", "bucket_a",
        "textid_b", "juan_seq_b", "bucket_b",
        "chars_a", "chars_b",
        "juan_length_a", "juan_length_b",
        "coverage_a", "coverage_b",
        "longest_span", "cluster_count",
        "intra_juan",
    ])
    for r in rows:
        writer.writerow([
            r.a.textid, r.a.juan_seq, r.a.bucket,
            r.b.textid, r.b.juan_seq, r.b.bucket,
            r.chars_a, r.chars_b,
            r.juan_length_a, r.juan_length_b,
            f"{r.coverage_a:.4f}", f"{r.coverage_b:.4f}",
            r.longest_span, r.cluster_count,
            "1" if r.a.bucket_id == r.b.bucket_id else "0",
        ])


def _row_to_dict(r: JuanPairDuplication) -> dict:
    return {
        "a": {
            "textid": r.a.textid,
            "juan_seq": r.a.juan_seq,
            "bucket": r.a.bucket,
        },
        "b": {
            "textid": r.b.textid,
            "juan_seq": r.b.juan_seq,
            "bucket": r.b.bucket,
        },
        "chars_a": r.chars_a,
        "chars_b": r.chars_b,
        "juan_length_a": r.juan_length_a,
        "juan_length_b": r.juan_length_b,
        "coverage_a": r.coverage_a,
        "coverage_b": r.coverage_b,
        "longest_span": r.longest_span,
        "cluster_count": r.cluster_count,
        "intra_juan": r.a.bucket_id == r.b.bucket_id,
    }
