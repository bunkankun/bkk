"""Deferred repair support for generated parallel-passage assets."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from bkk.edit.offsets import OffsetRebaseConflict, rebase_content_span
from bkk.index.parallel_assets import atomic_write, dump_parallel_yaml


STALE_LEDGER = "_parallel_stale.jsonl"
ASSET_INDEX = "_parallel_assets.sqlite"
BUCKETS = ("front", "body", "back")

_REF_RE = re.compile(
    r"^(?P<section>[0-9][a-z])(?P<serial>[0-9]{1,4})/"
    r"(?P<seq>[0-9]+)/(?P<bucket>front|back)?@"
    r"(?P<offset>[0-9]+)\+(?P<length>[1-9][0-9]*)$"
)
_ASSET_RE = re.compile(
    r"(?P<textid>[A-Za-z0-9._-]+)_(?P<seq>[0-9]{3})\.(?P<source>.+)\.parallels\.yaml$"
)
_TEXTID_RE = re.compile(r"KR(?P<section>[0-9][a-z])(?P<serial>[0-9]{4})")


@dataclass(frozen=True)
class ParallelRoots:
    state_root: Path
    parallels_root: Path | None = None
    corpus_root: Path | None = None


def default_state_root(parallels_root: Path | None, corpus_root: Path | None) -> Path:
    if parallels_root is not None:
        return Path(parallels_root)
    if corpus_root is not None:
        return Path(corpus_root)
    raise ValueError("provide parallels_root or corpus_root")


def stale_ledger_path(state_root: Path | str) -> Path:
    return Path(state_root) / STALE_LEDGER


def parallel_index_path(state_root: Path | str) -> Path:
    return Path(state_root) / ASSET_INDEX


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _splice_dict(edit: Any) -> dict[str, Any]:
    if isinstance(edit, dict):
        return {
            "start": int(edit.get("start", 0)),
            "delete_count": int(edit.get("delete_count", 0)),
            "insert": str(edit.get("insert", "")),
        }
    return {
        "start": int(getattr(edit, "start")),
        "delete_count": int(getattr(edit, "delete_count")),
        "insert": str(getattr(edit, "insert")),
    }


def append_stale_record(
    state_root: Path | str,
    *,
    textid: str,
    seq: int,
    bucket: str,
    base_commit_sha: str,
    result_commit_sha: str | None,
    text_splices: Iterable[Any],
    login: str,
    kind: str,
) -> dict[str, Any]:
    root = Path(state_root)
    root.mkdir(parents=True, exist_ok=True)
    record = {
        "id": uuid.uuid4().hex,
        "status": "pending",
        "created_at": _utc(),
        "updated_at": _utc(),
        "textid": textid,
        "seq": seq,
        "bucket": bucket,
        "base_commit_sha": base_commit_sha,
        "result_commit_sha": result_commit_sha,
        "text_splices": [_splice_dict(edit) for edit in text_splices],
        "login": login,
        "kind": kind,
    }
    path = stale_ledger_path(root)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def read_stale_records(state_root: Path | str) -> list[dict[str, Any]]:
    path = stale_ledger_path(state_root)
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def write_stale_records(state_root: Path | str, records: list[dict[str, Any]]) -> None:
    path = stale_ledger_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def pending_stale_records(state_root: Path | str) -> list[dict[str, Any]]:
    return [
        record for record in read_stale_records(state_root)
        if record.get("status") in ("pending", "repairing", "failed")
    ]


def has_pending_stale_for(state_root: Path | str, textid: str, seq: int) -> bool:
    return any(
        record.get("textid") == textid and record.get("seq") == seq
        for record in pending_stale_records(state_root)
    )


def _parse_asset_name(path: Path) -> tuple[str, int, str] | None:
    match = _ASSET_RE.fullmatch(path.name)
    if match is None:
        return None
    return match.group("textid"), int(match.group("seq")), match.group("source")


def parse_parallel_ref(ref: Any) -> tuple[str, int, str, int, int] | None:
    if not isinstance(ref, str):
        return None
    match = _REF_RE.fullmatch(ref)
    if match is None:
        return None
    return (
        f"KR{match.group('section')}{int(match.group('serial')):04d}",
        int(match.group("seq")),
        match.group("bucket") or "body",
        int(match.group("offset")),
        int(match.group("length")),
    )


def format_parallel_ref(textid: str, seq: int, bucket: str, offset: int, length: int) -> str:
    match = _TEXTID_RE.fullmatch(textid)
    if match is None:
        bucket_ref = "" if bucket == "body" else bucket
        return f"{textid}/{seq}/{bucket_ref}@{offset}+{length}"
    bucket_ref = "" if bucket == "body" else bucket
    return f"{match.group('section')}{int(match.group('serial'))}/{seq}/{bucket_ref}@{offset}+{length}"


def _asset_paths(parallels_root: Path | None, corpus_root: Path | None) -> list[Path]:
    paths: dict[str, Path] = {}
    if parallels_root is not None and parallels_root.is_dir():
        for path in parallels_root.glob("*/*.parallels.yaml"):
            paths[str(path.resolve())] = path
    if corpus_root is not None and corpus_root.is_dir():
        for path in corpus_root.glob("**/parallels/*.parallels.yaml"):
            paths[str(path.resolve())] = path
    return [paths[key] for key in sorted(paths)]


def _load_asset(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return raw if isinstance(raw, dict) else {}


def build_parallel_asset_index(
    state_root: Path | str,
    *,
    parallels_root: Path | str | None = None,
    corpus_root: Path | str | None = None,
) -> dict[str, int]:
    state_root = Path(state_root)
    state_root.mkdir(parents=True, exist_ok=True)
    index_path = parallel_index_path(state_root)
    paths = _asset_paths(
        Path(parallels_root) if parallels_root is not None else None,
        Path(corpus_root) if corpus_root is not None else None,
    )

    if index_path.exists():
        index_path.unlink()
    conn = sqlite3.connect(index_path)
    rows: list[tuple[Any, ...]] = []
    try:
        conn.executescript(
            """
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE marker(
              path TEXT NOT NULL,
              source_name TEXT NOT NULL,
              source_textid TEXT NOT NULL,
              source_seq INTEGER NOT NULL,
              local_bucket TEXT NOT NULL,
              local_offset INTEGER NOT NULL,
              local_length INTEGER NOT NULL,
              remote_textid TEXT NOT NULL,
              remote_seq INTEGER NOT NULL,
              remote_bucket TEXT NOT NULL,
              remote_offset INTEGER NOT NULL,
              remote_length INTEGER NOT NULL,
              marker_index INTEGER NOT NULL
            );
            CREATE INDEX idx_marker_local ON marker(source_textid, source_seq, local_bucket, local_offset);
            CREATE INDEX idx_marker_remote ON marker(remote_textid, remote_seq, remote_bucket, remote_offset);
            CREATE INDEX idx_marker_path ON marker(path);
            """
        )
        conn.execute("INSERT INTO meta(key, value) VALUES (?, ?)", ("schema_version", "1"))
        for path in paths:
            parsed_name = _parse_asset_name(path)
            if parsed_name is None:
                continue
            source_textid, source_seq, source_name = parsed_name
            data = _load_asset(path)
            buckets = data.get("markers")
            if not isinstance(buckets, dict):
                continue
            for bucket in BUCKETS:
                markers = buckets.get(bucket)
                if not isinstance(markers, list):
                    continue
                for index, marker in enumerate(markers):
                    if not isinstance(marker, dict) or marker.get("type") != "parallel":
                        continue
                    local_offset = marker.get("offset")
                    local_length = marker.get("length")
                    remote = parse_parallel_ref(marker.get("ref"))
                    if (
                        isinstance(local_offset, bool)
                        or not isinstance(local_offset, int)
                        or isinstance(local_length, bool)
                        or not isinstance(local_length, int)
                        or local_length < 1
                        or remote is None
                    ):
                        continue
                    rows.append((
                        str(path),
                        source_name,
                        source_textid,
                        source_seq,
                        bucket,
                        local_offset,
                        local_length,
                        remote[0],
                        remote[1],
                        remote[2],
                        remote[3],
                        remote[4],
                        index,
                    ))
        if rows:
            conn.executemany(
                "INSERT INTO marker(path, source_name, source_textid, source_seq,"
                " local_bucket, local_offset, local_length, remote_textid, remote_seq,"
                " remote_bucket, remote_offset, remote_length, marker_index)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
        conn.commit()
    finally:
        conn.close()
    return {"assets": len(paths), "markers": len(rows), "index_path": str(index_path)}


def _candidate_paths(index_path: Path, record: dict[str, Any]) -> list[Path]:
    starts = [
        int(edit.get("start"))
        for edit in (record.get("text_splices") or [])
        if isinstance(edit, dict) and isinstance(edit.get("start"), int)
    ]
    first_edit = min(starts) if starts else 0
    conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT path FROM marker
            WHERE (
              source_textid = ? AND source_seq = ? AND local_bucket = ?
              AND local_offset + local_length >= ?
            ) OR (
              remote_textid = ? AND remote_seq = ? AND remote_bucket = ?
              AND remote_offset + remote_length >= ?
            )
            """,
            (
                record.get("textid"), record.get("seq"), record.get("bucket"), first_edit,
                record.get("textid"), record.get("seq"), record.get("bucket"), first_edit,
            ),
        ).fetchall()
    finally:
        conn.close()
    return [Path(row[0]) for row in rows]


def _repair_asset_for_record(
    data: dict[str, Any],
    *,
    source_textid: str,
    source_seq: int,
    record: dict[str, Any],
) -> tuple[dict[str, Any], bool, int, int]:
    edited_textid = record.get("textid")
    edited_seq = record.get("seq")
    edited_bucket = record.get("bucket")
    splices = record.get("text_splices") or []
    next_data = json.loads(json.dumps(data, ensure_ascii=False))
    buckets = next_data.get("markers")
    if not isinstance(buckets, dict):
        return next_data, False, 0, 0

    shifted = 0
    dropped = 0
    changed = False
    for bucket in BUCKETS:
        markers = buckets.get(bucket)
        if not isinstance(markers, list):
            continue
        kept: list[Any] = []
        for marker in markers:
            if not isinstance(marker, dict) or marker.get("type") != "parallel":
                kept.append(marker)
                continue
            drop = False
            marker_changed = False
            local_offset = marker.get("offset")
            local_length = marker.get("length")
            if (
                source_textid == edited_textid
                and source_seq == edited_seq
                and bucket == edited_bucket
                and isinstance(local_offset, int)
                and not isinstance(local_offset, bool)
                and isinstance(local_length, int)
                and not isinstance(local_length, bool)
                and local_length >= 1
            ):
                try:
                    span = rebase_content_span(local_offset, local_length, splices)
                except OffsetRebaseConflict:
                    drop = True
                else:
                    if marker.get("offset") != span.start:
                        marker["offset"] = span.start
                        marker_changed = True
                    if marker.get("length") != span.length:
                        marker["length"] = span.length
                        marker_changed = True

            remote = parse_parallel_ref(marker.get("ref"))
            if remote is not None and (
                remote[0] == edited_textid
                and remote[1] == edited_seq
                and remote[2] == edited_bucket
            ):
                try:
                    span = rebase_content_span(remote[3], remote[4], splices)
                except OffsetRebaseConflict:
                    drop = True
                else:
                    ref = format_parallel_ref(remote[0], remote[1], remote[2], span.start, span.length)
                    if marker.get("ref") != ref:
                        marker["ref"] = ref
                        marker_changed = True

            if drop:
                dropped += 1
                changed = True
                continue
            if marker_changed:
                shifted += 1
                changed = True
            kept.append(marker)
        buckets[bucket] = kept
    return next_data, changed, shifted, dropped


def _mark_record(records: list[dict[str, Any]], record_id: str, status: str, **extra: Any) -> None:
    for record in records:
        if record.get("id") == record_id:
            record["status"] = status
            record["updated_at"] = _utc()
            record.update(extra)
            return


def repair_pending_parallel_stale(
    state_root: Path | str,
    *,
    parallels_root: Path | str | None = None,
    corpus_root: Path | str | None = None,
    rebuild_index: bool = False,
) -> dict[str, Any]:
    state_root = Path(state_root)
    index_path = parallel_index_path(state_root)
    if rebuild_index or not index_path.exists():
        build_parallel_asset_index(
            state_root,
            parallels_root=parallels_root,
            corpus_root=corpus_root,
        )

    records = read_stale_records(state_root)
    pending = [r for r in records if r.get("status") in ("pending", "repairing", "failed")]
    summary = {
        "stale_records": len(pending),
        "files_scanned": 0,
        "files_changed": 0,
        "links_shifted": 0,
        "links_dropped": 0,
        "records_repaired": 0,
        "index_path": str(index_path),
    }
    for record in pending:
        record_id = str(record.get("id"))
        _mark_record(records, record_id, "repairing")
        try:
            paths = _candidate_paths(index_path, record)
            changed_files: dict[Path, str] = {}
            shifted = 0
            dropped = 0
            for path in paths:
                parsed_name = _parse_asset_name(path)
                if parsed_name is None or not path.exists():
                    continue
                summary["files_scanned"] += 1
                data = _load_asset(path)
                repaired, changed, s_count, d_count = _repair_asset_for_record(
                    data,
                    source_textid=parsed_name[0],
                    source_seq=parsed_name[1],
                    record=record,
                )
                if changed:
                    changed_files[path] = dump_parallel_yaml(repaired)
                    shifted += s_count
                    dropped += d_count
            for path, content in changed_files.items():
                atomic_write(path, content)
            summary["files_changed"] += len(changed_files)
            summary["links_shifted"] += shifted
            summary["links_dropped"] += dropped
            summary["records_repaired"] += 1
            _mark_record(
                records,
                record_id,
                "repaired",
                repaired_at=_utc(),
                files_changed=len(changed_files),
                links_shifted=shifted,
                links_dropped=dropped,
            )
        except Exception as exc:  # noqa: BLE001
            _mark_record(records, record_id, "failed", error=f"{type(exc).__name__}: {exc}")
    write_stale_records(state_root, records)
    if summary["files_changed"]:
        build_parallel_asset_index(
            state_root,
            parallels_root=parallels_root,
            corpus_root=corpus_root,
        )
    return summary
