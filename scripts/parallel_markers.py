#!/usr/bin/env python3
"""Convert ``bkk index parallel-scan`` JSONL into parallel marker assets.

For every location in a cluster, the converter creates one directed marker
for every other location.  Markers are grouped into per-text, per-juan files:

    <output>/<textid>/<textid>_<seq>.<name>.parallels.yaml

Example:

    python scripts/parallel_markers.py tail-index-4.out \
        --output /tmp/parallel-markers --name KR6q \
        --index /data/bkk/_corpus.bkkx
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sqlite3
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO

from bkk.index.parallel_assets import (
    BUCKETS,
    TEXTID_RE,
    FlowDict,
    atomic_write,
    assert_index_unchanged,
    capture_index_snapshot,
    dump_parallel_yaml,
    short_ref,
    validate_name,
)


LOGGER = logging.getLogger("parallel_markers")


class InputError(ValueError):
    """A malformed parallel-scan input record."""


def _require_int(
    value: Any, field: str, line_number: int, *, minimum: int = 0,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InputError(f"line {line_number}: {field} must be an integer")
    if value < minimum:
        raise InputError(
            f"line {line_number}: {field} must be at least {minimum}"
        )
    return value


def _validate_location(
    value: Any, line_number: int, location_index: int,
) -> dict[str, Any]:
    prefix = f"locations[{location_index}]"
    if not isinstance(value, dict):
        raise InputError(f"line {line_number}: {prefix} must be an object")

    textid = value.get("textid")
    if not isinstance(textid, str) or TEXTID_RE.fullmatch(textid) is None:
        raise InputError(
            f"line {line_number}: {prefix}.textid must match KR0a0000"
        )

    juan_seq = _require_int(
        value.get("juan_seq"), f"{prefix}.juan_seq", line_number,
    )
    bucket = value.get("bucket")
    if bucket not in BUCKETS:
        raise InputError(
            f"line {line_number}: {prefix}.bucket must be one of "
            f"{', '.join(BUCKETS)}"
        )
    start = _require_int(value.get("start"), f"{prefix}.start", line_number)
    end = _require_int(value.get("end"), f"{prefix}.end", line_number)
    if end <= start:
        raise InputError(
            f"line {line_number}: {prefix}.end must be greater than start"
        )
    edit_distance = _require_int(
        value.get("edit_distance"),
        f"{prefix}.edit_distance",
        line_number,
    )
    toc_label = value.get("toc_label")
    if toc_label is not None and not isinstance(toc_label, str):
        raise InputError(
            f"line {line_number}: {prefix}.toc_label must be a string or null"
        )

    return {
        "textid": textid,
        "juan_seq": juan_seq,
        "bucket": bucket,
        "start": start,
        "end": end,
        "edit_distance": edit_distance,
        "toc_label": toc_label,
    }


def _validate_cluster(value: Any, line_number: int) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        raise InputError(f"line {line_number}: cluster must be an object")
    cluster_id = value.get("cluster_id")
    if not isinstance(cluster_id, str) or not cluster_id:
        raise InputError(
            f"line {line_number}: cluster_id must be a non-empty string"
        )
    raw_locations = value.get("locations")
    if not isinstance(raw_locations, list) or len(raw_locations) < 2:
        raise InputError(
            f"line {line_number}: locations must contain at least two entries"
        )

    locations = [
        _validate_location(location, line_number, index)
        for index, location in enumerate(raw_locations)
    ]
    unique_locations: list[dict[str, Any]] = []
    seen_identities: set[tuple[str, int, str, int, int]] = set()
    duplicate_count = 0
    for location in locations:
        identity = (
            location["textid"],
            location["juan_seq"],
            location["bucket"],
            location["start"],
            location["end"],
        )
        if identity in seen_identities:
            duplicate_count += 1
            continue
        seen_identities.add(identity)
        unique_locations.append(location)

    if duplicate_count:
        LOGGER.warning(
            "line %d: cluster %s contains %d duplicate location(s); "
            "keeping the first occurrence",
            line_number,
            cluster_id,
            duplicate_count,
        )
    if len(unique_locations) < 2:
        LOGGER.warning(
            "line %d: cluster %s has fewer than two distinct locations; "
            "skipping",
            line_number,
            cluster_id,
        )
        return []
    return unique_locations


def _input_lines(
    stream: TextIO,
    *,
    hasher: Any | None = None,
) -> Iterator[tuple[int, str]]:
    for line_number, line in enumerate(stream, 1):
        if hasher is not None:
            hasher.update(line.encode("utf-8"))
        if line.strip():
            yield line_number, line


def _create_spool(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = OFF")
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute(
        """
        CREATE TABLE marker (
            textid TEXT NOT NULL,
            juan_seq INTEGER NOT NULL,
            bucket TEXT NOT NULL,
            local_offset INTEGER NOT NULL,
            local_length INTEGER NOT NULL,
            cluster_order INTEGER NOT NULL,
            local_order INTEGER NOT NULL,
            remote_order INTEGER NOT NULL,
            ref TEXT NOT NULL,
            edit_distance INTEGER NOT NULL,
            toc_label TEXT
        )
        """
    )
    return conn


def _spool_input(
    stream: TextIO, conn: sqlite3.Connection, *, hasher: Any | None = None,
) -> tuple[int, int]:
    cluster_count = 0
    marker_count = 0
    insert_sql = (
        "INSERT INTO marker "
        "(textid, juan_seq, bucket, local_offset, local_length, "
        "cluster_order, local_order, remote_order, ref, edit_distance, "
        "toc_label) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )

    for line_number, line in _input_lines(stream, hasher=hasher):
        try:
            raw_cluster = json.loads(line)
        except json.JSONDecodeError as exc:
            raise InputError(
                f"line {line_number}: invalid JSON: {exc.msg}"
            ) from exc
        locations = _validate_cluster(raw_cluster, line_number)
        rows: list[tuple[Any, ...]] = []
        for local_order, local in enumerate(locations):
            for remote_order, remote in enumerate(locations):
                if local_order == remote_order:
                    continue
                rows.append((
                    local["textid"],
                    local["juan_seq"],
                    local["bucket"],
                    local["start"],
                    local["end"] - local["start"],
                    cluster_count,
                    local_order,
                    remote_order,
                    short_ref(remote),
                    remote["edit_distance"],
                    remote["toc_label"],
                ))
        conn.executemany(insert_sql, rows)
        cluster_count += 1
        marker_count += len(rows)

    conn.execute(
        "CREATE INDEX marker_file_order ON marker "
        "(textid, juan_seq, bucket, local_offset, local_length, "
        "cluster_order, local_order, remote_order)"
    )
    conn.commit()
    return cluster_count, marker_count


def _output_files(conn: sqlite3.Connection) -> Iterator[tuple[str, int]]:
    yield from conn.execute(
        "SELECT DISTINCT textid, juan_seq FROM marker "
        "ORDER BY textid, juan_seq"
    )


def _markers_for_bucket(
    conn: sqlite3.Connection, textid: str, juan_seq: int, bucket: str,
) -> list[FlowDict]:
    rows = conn.execute(
        "SELECT local_offset, local_length, ref, edit_distance, toc_label "
        "FROM marker "
        "WHERE textid = ? AND juan_seq = ? AND bucket = ? "
        "ORDER BY local_offset, local_length, cluster_order, "
        "local_order, remote_order",
        (textid, juan_seq, bucket),
    )
    return [
        FlowDict({
            "type": "parallel",
            "offset": offset,
            "length": length,
            "ref": ref,
            "edit_distance": edit_distance,
            "toc_label": toc_label,
        })
        for offset, length, ref, edit_distance, toc_label in rows
    ]


def _write_outputs(
    conn: sqlite3.Connection,
    output_dir: Path,
    name: str,
    provenance: dict[str, Any],
) -> int:
    file_count = 0
    for textid, juan_seq in _output_files(conn):
        data = {
            "provenance": provenance,
            "markers": {
                bucket: _markers_for_bucket(conn, textid, juan_seq, bucket)
                for bucket in BUCKETS
            }
        }
        filename = f"{textid}_{juan_seq:03d}.{name}.parallels.yaml"
        atomic_write(
            output_dir / textid / filename,
            dump_parallel_yaml(data),
        )
        file_count += 1
    return file_count


def convert(
    input_stream: TextIO,
    output_dir: Path,
    name: str,
    *,
    index_path: Path,
    input_name: str,
    temp_dir: Path | None = None,
) -> tuple[int, int, int]:
    """Convert one JSONL stream and return cluster, marker, and file counts."""
    validate_name(name)
    if temp_dir is not None and not temp_dir.is_dir():
        raise ValueError(f"temporary directory does not exist: {temp_dir}")

    snapshot = capture_index_snapshot(
        index_path,
        command="scripts/parallel_markers.py",
        algorithm="imported-jsonl-v1",
        scan={"input": input_name, "input_hash": None, "name": name},
    )
    with tempfile.TemporaryDirectory(
        prefix="bkk-parallel-markers-", dir=temp_dir,
    ) as workspace:
        conn = _create_spool(Path(workspace) / "markers.sqlite3")
        try:
            input_hasher = hashlib.sha256()
            cluster_count, marker_count = _spool_input(
                input_stream, conn, hasher=input_hasher,
            )
            snapshot.provenance["scan"]["input_hash"] = (
                f"sha256:{input_hasher.hexdigest()}"
            )
            assert_index_unchanged(snapshot)
            file_count = _write_outputs(
                conn, output_dir, name, snapshot.provenance,
            )
        finally:
            conn.close()
    return cluster_count, marker_count, file_count


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input",
        help="parallel-scan JSONL path, or - to read standard input",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="root directory for generated marker subfolders",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="run/subcorpus name inserted before .parallels.yaml",
    )
    parser.add_argument(
        "--index",
        type=Path,
        required=True,
        help="source .bkkx index used to generate the JSONL",
    )
    parser.add_argument(
        "--temp-dir",
        type=Path,
        help="directory for the temporary SQLite spool (default: system temp)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s: %(message)s",
    )
    try:
        if args.input == "-":
            counts = convert(
                sys.stdin,
                args.output,
                args.name,
                index_path=args.index,
                input_name="-",
                temp_dir=args.temp_dir,
            )
        else:
            with Path(args.input).open(encoding="utf-8") as stream:
                counts = convert(
                    stream,
                    args.output,
                    args.name,
                    index_path=args.index,
                    input_name=Path(args.input).name,
                    temp_dir=args.temp_dir,
                )
    except (InputError, OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    clusters, markers, files = counts
    print(
        f"clusters: {clusters:,}; directed markers: {markers:,}; "
        f"files: {files:,}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
