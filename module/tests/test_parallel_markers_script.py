"""JSONL parallel-scan conversion into namespaced marker files."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "parallel_markers.py"


def _location(
    textid: str,
    juan: int,
    bucket: str,
    start: int,
    end: int,
    *,
    edit_distance: int,
    toc_label: str | None,
) -> dict:
    return {
        "textid": textid,
        "juan_seq": juan,
        "bucket": bucket,
        "start": start,
        "end": end,
        "toc_label": toc_label,
        "left": "",
        "right": "",
        "edit_distance": edit_distance,
    }


def _cluster(cluster_id: str, locations: list[dict]) -> dict:
    return {
        "cluster_id": cluster_id,
        "length": 4,
        "occurrence_count": len(locations),
        "representative_edits": 2,
        "text": "甲乙丙丁",
        "locations": locations,
    }


def _run(
    input_path: Path, output: Path, name: str = "KR6q",
) -> subprocess.CompletedProcess[str]:
    index_path = output.parent / "_corpus.bkkx"
    if not index_path.exists():
        conn = sqlite3.connect(index_path)
        try:
            conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
            conn.executemany(
                "INSERT INTO meta(key, value) VALUES (?, ?)",
                [("schema_version", "3"), ("kind", "corpus")],
            )
            conn.commit()
        finally:
            conn.close()
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(input_path),
            "--output",
            str(output),
            "--name",
            name,
            "--index",
            str(index_path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def _write_jsonl(path: Path, clusters: list[dict]) -> None:
    path.write_text(
        "".join(
            json.dumps(cluster, ensure_ascii=False) + "\n"
            for cluster in clusters
        ),
        encoding="utf-8",
    )


def test_converter_emits_directed_markers_grouped_by_local_juan(tmp_path: Path):
    input_path = tmp_path / "parallels.jsonl"
    output = tmp_path / "markers"
    _write_jsonl(input_path, [
        _cluster("parallel-1", [
            _location(
                "KR6q0001", 1, "body", 10, 14,
                edit_distance=0, toc_label="本地",
            ),
            _location(
                "KR6q0002", 2, "front", 20, 25,
                edit_distance=2, toc_label="遠端",
            ),
            _location(
                "KR6q0001", 1, "body", 30, 34,
                edit_distance=1, toc_label=None,
            ),
        ]),
    ])

    result = _run(input_path, output)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        "clusters: 1; directed markers: 6; files: 2"
    )
    local_path = (
        output / "KR6q0001" / "KR6q0001_001.KR6q.parallels.yaml"
    )
    remote_path = (
        output / "KR6q0002" / "KR6q0002_002.KR6q.parallels.yaml"
    )
    assert local_path.is_file()
    assert remote_path.is_file()
    assert local_path.stat().st_mode & 0o777 == 0o644
    assert remote_path.stat().st_mode & 0o777 == 0o644

    local = yaml.safe_load(local_path.read_text(encoding="utf-8"))
    assert local["provenance"]["generator"]["algorithm"] == "imported-jsonl-v1"
    assert local["provenance"]["index"]["filename"] == "_corpus.bkkx"
    assert local["provenance"]["index"]["hash"].startswith("sha256:")
    assert local["provenance"]["scan"]["input"] == "parallels.jsonl"
    assert local["provenance"]["scan"]["input_hash"].startswith("sha256:")
    assert local["markers"]["front"] == []
    assert local["markers"]["back"] == []
    assert local["markers"]["body"] == [
        {
            "type": "parallel",
            "offset": 10,
            "length": 4,
            "ref": "6q2/2/front@20+5",
            "edit_distance": 2,
            "toc_label": "遠端",
        },
        {
            "type": "parallel",
            "offset": 10,
            "length": 4,
            "ref": "6q1/1/@30+4",
            "edit_distance": 1,
            "toc_label": None,
        },
        {
            "type": "parallel",
            "offset": 30,
            "length": 4,
            "ref": "6q1/1/@10+4",
            "edit_distance": 0,
            "toc_label": "本地",
        },
        {
            "type": "parallel",
            "offset": 30,
            "length": 4,
            "ref": "6q2/2/front@20+5",
            "edit_distance": 2,
            "toc_label": "遠端",
        },
    ]
    remote = yaml.safe_load(remote_path.read_text(encoding="utf-8"))
    assert [marker["ref"] for marker in remote["markers"]["front"]] == [
        "6q1/1/@10+4",
        "6q1/1/@30+4",
    ]


def test_converter_overwrites_only_the_selected_run_name(tmp_path: Path):
    input_path = tmp_path / "parallels.jsonl"
    output = tmp_path / "markers"
    locations = [
        _location(
            "KR1h0004", 8, "body", 1, 3,
            edit_distance=0, toc_label="甲",
        ),
        _location(
            "KR1h0005", 3, "back", 4, 7,
            edit_distance=1, toc_label="乙",
        ),
    ]
    _write_jsonl(input_path, [_cluster("parallel-1", locations)])
    first = _run(input_path, output, "first")
    assert first.returncode == 0, first.stderr

    first_path = (
        output / "KR1h0004" / "KR1h0004_008.first.parallels.yaml"
    )
    other_path = (
        output / "KR1h0004" / "KR1h0004_008.other.parallels.yaml"
    )
    other_path.write_text("untouched\n", encoding="utf-8")
    first_path.write_text("stale\n", encoding="utf-8")

    second = _run(input_path, output, "first")

    assert second.returncode == 0, second.stderr
    assert yaml.safe_load(first_path.read_text(encoding="utf-8"))["markers"]
    assert other_path.read_text(encoding="utf-8") == "untouched\n"


def test_converter_validates_whole_input_before_writing(tmp_path: Path):
    input_path = tmp_path / "parallels.jsonl"
    output = tmp_path / "markers"
    valid = _cluster("parallel-1", [
        _location(
            "KR6q0001", 1, "body", 0, 2,
            edit_distance=0, toc_label="甲",
        ),
        _location(
            "KR6q0002", 1, "body", 0, 2,
            edit_distance=0, toc_label="乙",
        ),
    ])
    input_path.write_text(
        json.dumps(valid, ensure_ascii=False) + "\n{not-json}\n",
        encoding="utf-8",
    )

    result = _run(input_path, output)

    assert result.returncode == 2
    assert "line 2: invalid JSON" in result.stderr
    assert not output.exists()


def test_converter_logs_and_collapses_duplicate_locations(tmp_path: Path):
    input_path = tmp_path / "parallels.jsonl"
    output = tmp_path / "markers"
    location = _location(
        "KR6q0001", 1, "body", 0, 2,
        edit_distance=0, toc_label="甲",
    )
    other = _location(
        "KR6q0002", 1, "body", 4, 6,
        edit_distance=1, toc_label="乙",
    )
    _write_jsonl(
        input_path,
        [_cluster("parallel-1", [location, dict(location), other])],
    )

    duplicate = _run(input_path, output)

    assert duplicate.returncode == 0
    assert duplicate.stdout.strip() == (
        "clusters: 1; directed markers: 2; files: 2"
    )
    assert (
        "WARNING: line 1: cluster parallel-1 contains 1 duplicate "
        "location(s); keeping the first occurrence"
    ) in duplicate.stderr
    local_path = (
        output / "KR6q0001" / "KR6q0001_001.KR6q.parallels.yaml"
    )
    local = yaml.safe_load(local_path.read_text(encoding="utf-8"))
    assert len(local["markers"]["body"]) == 1


def test_converter_skips_cluster_with_only_one_distinct_location(tmp_path: Path):
    input_path = tmp_path / "parallels.jsonl"
    output = tmp_path / "markers"
    location = _location(
        "KR6q0001", 1, "body", 0, 2,
        edit_distance=0, toc_label="甲",
    )
    _write_jsonl(
        input_path, [_cluster("parallel-1", [location, dict(location)])],
    )

    result = _run(input_path, output)

    assert result.returncode == 0
    assert result.stdout.strip() == (
        "clusters: 1; directed markers: 0; files: 0"
    )
    assert "has fewer than two distinct locations; skipping" in result.stderr
    assert not output.exists()


def test_converter_rejects_unsafe_name(tmp_path: Path):
    input_path = tmp_path / "parallels.jsonl"
    output = tmp_path / "markers"
    input_path.write_text("", encoding="utf-8")

    unsafe_name = _run(input_path, output, "../escape")

    assert unsafe_name.returncode == 2
    assert "name must match" in unsafe_name.stderr
    assert not output.exists()


def test_converter_rejects_invalid_spans_before_writing(tmp_path: Path):
    input_path = tmp_path / "parallels.jsonl"
    output = tmp_path / "markers"
    invalid = _location(
        "KR6q0001", 1, "body", 4, 4,
        edit_distance=0, toc_label="甲",
    )
    valid = _location(
        "KR6q0002", 1, "body", 0, 2,
        edit_distance=0, toc_label="乙",
    )
    _write_jsonl(
        input_path, [_cluster("parallel-1", [invalid, valid])],
    )

    result = _run(input_path, output)

    assert result.returncode == 2
    assert "end must be greater than start" in result.stderr
    assert not output.exists()
