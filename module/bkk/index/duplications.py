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


REPORT_VERSION = 2


@dataclass(frozen=True)
class JuanPairDuplication:
    """One (juan_a, juan_b) row. For intra-juan rows, ``a == b``.

    ``longest_a`` / ``longest_b`` are the ``(start, end)`` offsets of the
    longest single cluster on each side. ``spans_a`` / ``spans_b`` are the
    merged non-overlapping spans covering all duplicated character ranges on
    each side. For intra-juan rows, ``spans_a == spans_b`` (same bucket) but
    ``longest_a`` and ``longest_b`` are the two distinct copies of the
    longest cluster within that bucket.
    """

    a: JuanRef
    b: JuanRef
    chars_a: int
    chars_b: int
    juan_length_a: int
    juan_length_b: int
    longest_span: int
    longest_a: tuple[int, int]
    longest_b: tuple[int, int]
    spans_a: tuple[tuple[int, int], ...]
    spans_b: tuple[tuple[int, int], ...]
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
    work_db: Path | str | None = None,
    force_work_db: bool = False,
    jobs: int = 1,
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
        work_db=work_db,
        force_work_db=force_work_db,
        jobs=jobs,
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
    longest_len: dict[tuple[int, int], int] = defaultdict(int)
    # pair_key -> ((a_start, a_end), (b_start, b_end)) for the longest cluster.
    longest_offsets: dict[
        tuple[int, int], tuple[tuple[int, int], tuple[int, int]]
    ] = {}
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
                if li.bucket_id <= lj.bucket_id:
                    a_loc, b_loc = li, lj
                else:
                    a_loc, b_loc = lj, li
                key = (a_loc.bucket_id, b_loc.bucket_id)
                intervals[key][a_loc.bucket_id].append((a_loc.start, a_loc.end))
                intervals[key][b_loc.bucket_id].append((b_loc.start, b_loc.end))
                if cluster.length > longest_len[key]:
                    longest_len[key] = cluster.length
                    longest_offsets[key] = (
                        (a_loc.start, a_loc.end),
                        (b_loc.start, b_loc.end),
                    )
                seen_pairs.add(key)
        for key in seen_pairs:
            clusters_per_pair[key] += 1

    rows: list[JuanPairDuplication] = []
    for key, side_intervals in intervals.items():
        a_id, b_id = key
        a_ref = refs[a_id]
        b_ref = refs[b_id]
        spans_a = _merge_spans(side_intervals[a_id])
        if a_id == b_id:
            spans_b = spans_a
        else:
            spans_b = _merge_spans(side_intervals[b_id])
        chars_a = sum(e - s for s, e in spans_a)
        chars_b = sum(e - s for s, e in spans_b)
        la, lb = longest_offsets[key]
        rows.append(JuanPairDuplication(
            a=a_ref,
            b=b_ref,
            chars_a=chars_a,
            chars_b=chars_b,
            juan_length_a=0,
            juan_length_b=0,
            longest_span=longest_len[key],
            longest_a=la,
            longest_b=lb,
            spans_a=spans_a,
            spans_b=spans_b,
            cluster_count=clusters_per_pair[key],
        ))
    return rows


def _ref_from_location(loc: ParallelLocation) -> JuanRef:
    return JuanRef(
        textid=loc.textid,
        juan_seq=loc.juan_seq,
        bucket=loc.bucket,
        bucket_id=loc.bucket_id,
    )


def _merge_spans(spans: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Sort and merge overlapping/touching half-open intervals."""
    if not spans:
        return ()
    spans = sorted(spans)
    out: list[tuple[int, int]] = []
    cur_start, cur_end = spans[0]
    for start, end in spans[1:]:
        if start <= cur_end:
            if end > cur_end:
                cur_end = end
        else:
            out.append((cur_start, cur_end))
            cur_start, cur_end = start, end
    out.append((cur_start, cur_end))
    return tuple(out)


def _merged_length(spans: list[tuple[int, int]]) -> int:
    return sum(e - s for s, e in _merge_spans(spans))


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
            longest_a=r.longest_a,
            longest_b=r.longest_b,
            spans_a=r.spans_a,
            spans_b=r.spans_b,
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


TSV_HEADER: tuple[str, ...] = (
    "textid_a", "juan_seq_a", "bucket_a",
    "textid_b", "juan_seq_b", "bucket_b",
    "chars_a", "chars_b",
    "juan_length_a", "juan_length_b",
    "coverage_a", "coverage_b",
    "longest_span", "cluster_count",
    "intra_juan",
    "longest_a_start", "longest_a_end",
    "longest_b_start", "longest_b_end",
    "spans_a_json", "spans_b_json",
    "action", "action_actor", "action_at",
)


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
    out.write(f"# bkk-duplications version={REPORT_VERSION}\n")
    writer = csv.writer(out, delimiter="\t", lineterminator="\n")
    writer.writerow(TSV_HEADER)
    for r in rows:
        writer.writerow(_row_to_tsv(r))


def _row_to_tsv(r: JuanPairDuplication) -> list[str | int]:
    return [
        r.a.textid, r.a.juan_seq, r.a.bucket,
        r.b.textid, r.b.juan_seq, r.b.bucket,
        r.chars_a, r.chars_b,
        r.juan_length_a, r.juan_length_b,
        f"{r.coverage_a:.4f}", f"{r.coverage_b:.4f}",
        r.longest_span, r.cluster_count,
        "1" if r.a.bucket_id == r.b.bucket_id else "0",
        r.longest_a[0], r.longest_a[1],
        r.longest_b[0], r.longest_b[1],
        json.dumps([list(s) for s in r.spans_a], separators=(",", ":")),
        json.dumps([list(s) for s in r.spans_b], separators=(",", ":")),
        "", "", "",  # action, action_actor, action_at
    ]


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
        "longest_a": list(r.longest_a),
        "longest_b": list(r.longest_b),
        "spans_a": [list(s) for s in r.spans_a],
        "spans_b": [list(s) for s in r.spans_b],
    }


# ---- reading / mutating an existing report --------------------------------

class ReportFormatError(ValueError):
    """Raised when dups.tsv has the wrong version or schema."""


def read_duplications_report(path: Path | str) -> list[dict]:
    """Read a v2 TSV report and return one dict per row (action fields included).

    Row ``id`` is 1-based, matching the row's position in the file (excluding
    the version comment and the header). IDs are stable across in-place
    rewrites that preserve row order (which :func:`update_action` does).
    """
    rows: list[dict] = []
    with Path(path).open("r", encoding="utf-8", newline="") as f:
        first = f.readline()
        if not first.startswith("# bkk-duplications version="):
            raise ReportFormatError(
                f"{path}: missing version comment; rewrite with current bkk "
                f"(expected '# bkk-duplications version={REPORT_VERSION}')"
            )
        version = first.rstrip("\n").split("=", 1)[1].strip()
        if version != str(REPORT_VERSION):
            raise ReportFormatError(
                f"{path}: report version {version}, expected {REPORT_VERSION}"
            )
        reader = csv.reader(f, delimiter="\t")
        header = next(reader, None)
        if header is None or tuple(header) != TSV_HEADER:
            raise ReportFormatError(f"{path}: header mismatch")
        for idx, raw in enumerate(reader, start=1):
            if len(raw) != len(TSV_HEADER):
                raise ReportFormatError(
                    f"{path}: row {idx} has {len(raw)} fields, expected "
                    f"{len(TSV_HEADER)}"
                )
            rows.append(_parse_row(idx, raw))
    return rows


def _parse_row(row_id: int, raw: list[str]) -> dict:
    f = dict(zip(TSV_HEADER, raw, strict=True))
    return {
        "id": row_id,
        "textid_a": f["textid_a"],
        "juan_seq_a": int(f["juan_seq_a"]),
        "bucket_a": f["bucket_a"],
        "textid_b": f["textid_b"],
        "juan_seq_b": int(f["juan_seq_b"]),
        "bucket_b": f["bucket_b"],
        "chars_a": int(f["chars_a"]),
        "chars_b": int(f["chars_b"]),
        "juan_length_a": int(f["juan_length_a"]),
        "juan_length_b": int(f["juan_length_b"]),
        "coverage_a": float(f["coverage_a"]),
        "coverage_b": float(f["coverage_b"]),
        "longest_span": int(f["longest_span"]),
        "cluster_count": int(f["cluster_count"]),
        "intra_juan": f["intra_juan"] == "1",
        "longest_a": (int(f["longest_a_start"]), int(f["longest_a_end"])),
        "longest_b": (int(f["longest_b_start"]), int(f["longest_b_end"])),
        "spans_a": [tuple(s) for s in json.loads(f["spans_a_json"] or "[]")],
        "spans_b": [tuple(s) for s in json.loads(f["spans_b_json"] or "[]")],
        "action": f["action"] or None,
        "action_actor": f["action_actor"] or None,
        "action_at": f["action_at"] or None,
    }


VALID_ACTIONS: frozenset[str] = frozenset({
    "keep",
    "delete_a_juan", "delete_b_juan",
    "delete_a_span", "delete_b_span",
    "delete_span",
})


def update_action(
    path: Path | str,
    row_id: int,
    action: str,
    actor: str,
    at: str,
) -> None:
    """Atomically rewrite ``row_id``'s action/actor/at columns in place.

    Reads all rows, mutates the target, writes to a sibling temp file, then
    ``os.replace``s it. Caller is responsible for holding any cross-process
    lock (e.g. ``fcntl.flock``) around the call.
    """
    if action not in VALID_ACTIONS:
        raise ValueError(f"invalid action {action!r}; valid: {sorted(VALID_ACTIONS)}")
    path = Path(path)
    with path.open("r", encoding="utf-8", newline="") as f:
        first = f.readline()
        if not first.startswith("# bkk-duplications version="):
            raise ReportFormatError(
                f"{path}: missing version comment; refusing to rewrite"
            )
        reader = csv.reader(f, delimiter="\t")
        header = next(reader, None)
        if header is None or tuple(header) != TSV_HEADER:
            raise ReportFormatError(f"{path}: header mismatch")
        rows = list(reader)
    if not 1 <= row_id <= len(rows):
        raise ValueError(f"row_id {row_id} out of range [1, {len(rows)}]")
    target = rows[row_id - 1]
    target[TSV_HEADER.index("action")] = action
    target[TSV_HEADER.index("action_actor")] = actor
    target[TSV_HEADER.index("action_at")] = at
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        f.write(first if first.endswith("\n") else first + "\n")
        writer = csv.writer(f, delimiter="\t", lineterminator="\n")
        writer.writerow(TSV_HEADER)
        writer.writerows(rows)
    import os
    os.replace(tmp, path)
