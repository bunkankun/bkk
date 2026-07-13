from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from bkk.index.parallel_assets import dump_parallel_yaml
from bkk.repair.parallels import (
    append_stale_record,
    build_parallel_asset_index,
    parallel_index_path,
    read_stale_records,
    repair_pending_parallel_stale,
)
from bkk.repair.cli import run as repair_run


def _write_asset(root: Path, textid: str, seq: int, name: str, markers: dict) -> Path:
    directory = root / textid
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{textid}_{seq:03d}.{name}.parallels.yaml"
    path.write_text(
        dump_parallel_yaml({"markers": markers}),
        encoding="utf-8",
    )
    return path


def test_parallel_asset_index_records_local_and_remote_refs(tmp_path: Path):
    root = tmp_path / "parallels"
    _write_asset(root, "KR1h0001", 1, "corpus", {
        "front": [],
        "body": [
            {"type": "parallel", "offset": 4, "length": 2, "ref": "6q1/1/@2+2"},
        ],
        "back": [],
    })

    summary = build_parallel_asset_index(root, parallels_root=root)

    assert summary["assets"] == 1
    assert summary["markers"] == 1
    conn = sqlite3.connect(parallel_index_path(root))
    try:
        row = conn.execute(
            "SELECT source_textid, source_seq, local_bucket, local_offset,"
            " remote_textid, remote_seq, remote_bucket, remote_offset "
            "FROM marker",
        ).fetchone()
    finally:
        conn.close()
    assert row == ("KR1h0001", 1, "body", 4, "KR6q0001", 1, "body", 2)


def test_parallel_repair_shifts_safe_refs_and_drops_overlaps(tmp_path: Path):
    root = tmp_path / "parallels"
    asset = _write_asset(root, "KR1h0001", 1, "corpus", {
        "front": [],
        "body": [
            {"type": "parallel", "offset": 0, "length": 1, "ref": "6q1/1/@2+2"},
            {"type": "parallel", "offset": 5, "length": 1, "ref": "6q1/1/@1+3"},
        ],
        "back": [],
    })
    append_stale_record(
        root,
        textid="KR6q0001",
        seq=1,
        bucket="body",
        base_commit_sha="base",
        result_commit_sha="commit",
        text_splices=[{"start": 2, "delete_count": 0, "insert": "新"}],
        login="alice",
        kind="commit",
    )
    build_parallel_asset_index(root, parallels_root=root)

    summary = repair_pending_parallel_stale(root, parallels_root=root)

    assert summary["records_repaired"] == 1
    assert summary["files_changed"] == 1
    assert summary["links_shifted"] == 1
    assert summary["links_dropped"] == 1
    saved = yaml.safe_load(asset.read_text(encoding="utf-8"))
    markers = saved["markers"]["body"]
    assert len(markers) == 1
    assert markers[0]["ref"] == "6q1/1/@3+2"
    records = read_stale_records(root)
    assert records[0]["status"] == "repaired"
    assert records[0]["links_shifted"] == 1
    assert records[0]["links_dropped"] == 1


def test_parallel_repair_cli_builds_index(tmp_path: Path, capsys):
    root = tmp_path / "parallels"
    _write_asset(root, "KR1h0001", 1, "corpus", {
        "front": [],
        "body": [
            {"type": "parallel", "offset": 4, "length": 2, "ref": "6q1/1/@2+2"},
        ],
        "back": [],
    })

    rc = repair_run(["parallel-index", "--parallels-root", str(root)])

    assert rc == 0
    assert parallel_index_path(root).is_file()
    assert "indexed 1 parallel markers" in capsys.readouterr().out
