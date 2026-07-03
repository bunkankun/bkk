"""Write provenance-bearing per-juan parallel marker assets."""

from __future__ import annotations

import datetime as dt
import importlib.metadata
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .build import compute_bkkx_hash
from .parallel import ParallelCluster, ParallelLocation


BUCKETS = ("front", "body", "back")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
TEXTID_RE = re.compile(r"^KR(?P<section>[0-9][a-z])(?P<serial>[0-9]{4})$")


class FlowDict(dict):
    """Mapping rendered on one line in generated YAML."""


class _Dumper(yaml.SafeDumper):
    pass


def _represent_flow_dict(
    dumper: yaml.SafeDumper, data: FlowDict,
) -> yaml.MappingNode:
    return dumper.represent_mapping(
        "tag:yaml.org,2002:map", data.items(), flow_style=True,
    )


_Dumper.add_representer(FlowDict, _represent_flow_dict)


def dump_parallel_yaml(data: dict[str, Any]) -> str:
    return yaml.dump(
        data,
        Dumper=_Dumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=10**9,
    )


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class IndexSnapshot:
    path: Path
    signature: tuple[int, int, int, int]
    provenance: dict[str, Any]


def _stat_signature(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _index_schema_version(path: Path) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            raise ValueError(f"{path} has no index schema version")
        return int(row[0])
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"cannot read index metadata from {path}: {exc}") from exc
    finally:
        conn.close()


def _package_version() -> str:
    try:
        return importlib.metadata.version("bkk")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def utc_timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def capture_index_snapshot(
    index_path: Path | str,
    *,
    command: str,
    algorithm: str,
    scan: dict[str, Any],
    generated_at: str | None = None,
) -> IndexSnapshot:
    path = Path(index_path).resolve()
    before = _stat_signature(path)
    index_hash = compute_bkkx_hash(path)
    after = _stat_signature(path)
    if before != after:
        raise RuntimeError(f"index changed while hashing: {path}")
    provenance = {
        "generated_at": generated_at or utc_timestamp(),
        "generator": {
            "command": command,
            "version": _package_version(),
            "algorithm": algorithm,
        },
        "index": {
            "filename": path.name,
            "hash": index_hash,
            "schema_version": _index_schema_version(path),
        },
        "scan": dict(scan),
    }
    return IndexSnapshot(path=path, signature=after, provenance=provenance)


def assert_index_unchanged(snapshot: IndexSnapshot) -> None:
    try:
        current = _stat_signature(snapshot.path)
    except FileNotFoundError as exc:
        raise RuntimeError(f"index disappeared during scan: {snapshot.path}") from exc
    if current != snapshot.signature:
        raise RuntimeError(f"index changed during scan: {snapshot.path}")


def derive_index_name(index_path: Path | str) -> str:
    name = Path(index_path).stem.lstrip("_")
    validate_name(name)
    return name


def validate_name(name: str) -> None:
    if NAME_RE.fullmatch(name) is None:
        raise ValueError(
            "name must match [A-Za-z0-9][A-Za-z0-9._-]*"
        )


def validate_textid(textid: str) -> None:
    if TEXTID_RE.fullmatch(textid) is None:
        raise ValueError(f"text ID {textid!r} must match KR0a0000")


def short_ref(location: ParallelLocation | dict[str, Any]) -> str:
    textid = (
        location.textid
        if isinstance(location, ParallelLocation)
        else location["textid"]
    )
    match = TEXTID_RE.fullmatch(textid)
    if match is None:
        raise ValueError(f"text ID {textid!r} must match KR0a0000")
    serial = str(int(match.group("serial")))
    juan_seq = (
        location.juan_seq
        if isinstance(location, ParallelLocation)
        else location["juan_seq"]
    )
    bucket = (
        location.bucket
        if isinstance(location, ParallelLocation)
        else location["bucket"]
    )
    start = (
        location.start
        if isinstance(location, ParallelLocation)
        else location["start"]
    )
    end = (
        location.end
        if isinstance(location, ParallelLocation)
        else location["end"]
    )
    bucket_ref = "" if bucket == "body" else bucket
    return (
        f"{match.group('section')}{serial}/{juan_seq}/"
        f"{bucket_ref}@{start}+{end - start}"
    )


def write_target_parallel_assets(
    clusters: list[ParallelCluster],
    bundle_dir: Path | str,
    *,
    textid: str,
    target_juan_seq: int | None = None,
    name: str,
    provenance: dict[str, Any],
) -> tuple[int, int, int]:
    """Write target-side directed markers.

    Returns ``(clusters_with_target, directed_markers, files)``.
    """
    validate_name(name)
    validate_textid(textid)
    bundle_dir = Path(bundle_dir)
    output_dir = bundle_dir / "parallels"
    rows: dict[int, dict[str, list[tuple[tuple, FlowDict]]]] = {}
    cluster_count = 0
    marker_count = 0

    for cluster_order, cluster in enumerate(clusters):
        unique: list[ParallelLocation] = []
        seen: set[tuple[str, int, str, int, int]] = set()
        for location in cluster.locations:
            identity = (
                location.textid, location.juan_seq, location.bucket,
                location.start, location.end,
            )
            if identity not in seen:
                seen.add(identity)
                unique.append(location)
        locals_ = [
            (index, location)
            for index, location in enumerate(unique)
            if (
                location.textid == textid
                and (
                    target_juan_seq is None
                    or location.juan_seq == target_juan_seq
                )
            )
        ]
        if not locals_:
            continue
        cluster_count += 1
        for local_order, local in locals_:
            for remote_order, remote in enumerate(unique):
                if local_order == remote_order:
                    continue
                marker = FlowDict({
                    "type": "parallel",
                    "offset": local.start,
                    "length": local.end - local.start,
                    "ref": short_ref(remote),
                    "edit_distance": remote.edit_distance,
                    "toc_label": remote.toc_label,
                })
                sort_key = (
                    local.start,
                    local.end - local.start,
                    cluster_order,
                    local_order,
                    remote_order,
                )
                by_bucket = rows.setdefault(
                    local.juan_seq,
                    {bucket: [] for bucket in BUCKETS},
                )
                by_bucket[local.bucket].append((sort_key, marker))
                marker_count += 1

    rendered: dict[str, str] = {}
    for juan_seq, by_bucket in sorted(rows.items()):
        data = {
            "provenance": provenance,
            "markers": {
                bucket: [
                    marker
                    for _, marker in sorted(by_bucket[bucket], key=lambda row: row[0])
                ]
                for bucket in BUCKETS
            },
        }
        filename = f"{textid}_{juan_seq:03d}.{name}.parallels.yaml"
        rendered[filename] = dump_parallel_yaml(data)

    with tempfile.TemporaryDirectory(
        prefix="bkk-parallel-assets-", dir=bundle_dir,
    ) as temporary:
        staged = Path(temporary)
        for filename, content in rendered.items():
            atomic_write(staged / filename, content)

        if output_dir.is_dir():
            if target_juan_seq is None:
                pattern = f"{textid}_*.{name}.parallels.yaml"
            else:
                pattern = (
                    f"{textid}_{target_juan_seq:03d}."
                    f"{name}.parallels.yaml"
                )
            existing = set(output_dir.glob(pattern))
        else:
            existing = set()
        if rendered:
            output_dir.mkdir(parents=True, exist_ok=True)
        written: set[Path] = set()
        for filename in sorted(rendered):
            destination = output_dir / filename
            os.replace(staged / filename, destination)
            os.chmod(destination, 0o644)
            written.add(destination)
        for stale in existing - written:
            stale.unlink()
        if output_dir.is_dir() and not any(output_dir.iterdir()):
            output_dir.rmdir()

    return cluster_count, marker_count, len(rendered)
