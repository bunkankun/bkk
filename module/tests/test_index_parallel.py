"""Parallel-passage discovery over the trigram index."""

from __future__ import annotations

import json
import os
import io
import sqlite3
from pathlib import Path

import pytest
import yaml

from bkk.index import build_index, merge_bundles
from bkk.index import parallel_assets
import bkk.index.parallel_scan as parallel_scan
from bkk.index.build import compute_bkkx_hash
from bkk.index.cli import run as cli_run
from bkk.index.parallel import discover_parallel_passages, write_parallel_report
from bkk.index.parallel_fuzzy_from_scan import discover_fuzzy_from_scan
from bkk.index.parallel_lookup import (
    ParallelLookup,
    ParallelLookupStaleError,
    build_parallel_lookup,
)
from bkk.index.parallel_scan import discover_parallel_passages_scan


def _write_bundle(
    root: Path,
    textid: str,
    body_text: str,
    *,
    front_text: str = "",
    second_body_text: str | None = None,
) -> Path:
    bundle_dir = root / textid
    bundle_dir.mkdir(parents=True)
    juan = {
        "canonical_identifier": f"bkk:test/{textid}/v1/juan/1",
        "seq": 1,
        "body": {"text": body_text, "hash": "sha256:0", "markers": []},
        "hash": "sha256:0",
    }
    if front_text:
        juan["front"] = {"text": front_text, "hash": "sha256:0", "markers": []}
    (bundle_dir / f"{textid}_001.yaml").write_text(
        yaml.safe_dump(juan, allow_unicode=True),
        encoding="utf-8",
    )
    parts = [
        {"seq": 1, "filename": f"{textid}_001.yaml", "hash": "sha256:0"},
    ]
    if second_body_text is not None:
        second = {
            "canonical_identifier": f"bkk:test/{textid}/v1/juan/2",
            "seq": 2,
            "body": {
                "text": second_body_text,
                "hash": "sha256:0",
                "markers": [],
            },
            "hash": "sha256:0",
        }
        (bundle_dir / f"{textid}_002.yaml").write_text(
            yaml.safe_dump(second, allow_unicode=True),
            encoding="utf-8",
        )
        parts.append({
            "seq": 2,
            "filename": f"{textid}_002.yaml",
            "hash": "sha256:0",
        })
    toc = [
        {
            "ref": {
                "seq": 1,
                "marker_id": f"{textid}_001-body",
                "span": ["body", 0, len(body_text)],
            },
            "label": f"{textid} body",
        },
    ]
    if front_text:
        toc.append({
            "ref": {
                "seq": 1,
                "marker_id": f"{textid}_001-front",
                "span": ["front", 0, len(front_text)],
            },
            "label": f"{textid} front",
        })
    if second_body_text is not None:
        toc.append({
            "ref": {
                "seq": 2,
                "marker_id": f"{textid}_002-body",
                "span": ["body", 0, len(second_body_text)],
            },
            "label": f"{textid} body 2",
        })
    (bundle_dir / f"{textid}.manifest.yaml").write_text(
        yaml.safe_dump({
            "canonical_identifier": f"bkk:test/{textid}/v1",
            "editions": [{"short": "X", "label": "x"}],
            "assets": {"parts": parts},
            "table_of_contents": toc,
        }, allow_unicode=True),
        encoding="utf-8",
    )
    return bundle_dir


def _merge(root: Path) -> Path:
    out = root / "_corpus.bkkx"
    merge_bundles(root, out)
    return out


def _cluster_signature(clusters):
    return [
        (
            cluster.text,
            cluster.length,
            cluster.occurrence_count,
            [
                (
                    loc.textid,
                    loc.juan_seq,
                    loc.bucket,
                    loc.bucket_id,
                    loc.start,
                    loc.end,
                )
                for loc in cluster.locations
            ],
        )
        for cluster in clusters
    ]


def test_parallel_finds_repeated_passage_across_texts(tmp_path):
    shared = "SHARED-PASSAGE-ALPHA"
    _write_bundle(tmp_path, "KR0a0001", f"aaa{shared}bbb")
    _write_bundle(tmp_path, "KR0a0002", f"ccc{shared}ddd")
    out = _merge(tmp_path)

    clusters = discover_parallel_passages(out, seed="PAS", min_length=12)

    assert len(clusters) == 1
    c = clusters[0]
    assert c.text == shared
    assert c.length == len(shared)
    assert c.occurrence_count == 2
    assert [loc.textid for loc in c.locations] == ["KR0a0001", "KR0a0002"]
    assert [loc.start for loc in c.locations] == [3, 3]
    assert c.locations[0].toc_label == "KR0a0001 body"


def test_parallel_clusters_three_occurrences(tmp_path):
    shared = "TRIPLE-PASSAGE"
    _write_bundle(tmp_path, "KR0a0001", f"aa{shared}bb")
    _write_bundle(tmp_path, "KR0a0002", f"cc{shared}dd")
    _write_bundle(tmp_path, "KR0a0003", f"ee{shared}ff")
    out = _merge(tmp_path)

    clusters = discover_parallel_passages(out, seed="PAS", min_length=12)

    assert len(clusters) == 1
    assert clusters[0].text == shared
    assert clusters[0].occurrence_count == 3
    assert [loc.textid for loc in clusters[0].locations] == [
        "KR0a0001",
        "KR0a0002",
        "KR0a0003",
    ]


def test_parallel_skips_overlapping_self_repeat(tmp_path):
    _write_bundle(tmp_path, "KR0a0001", "AAAAAAA")
    out = _merge(tmp_path)

    clusters = discover_parallel_passages(out, seed="AAA", min_length=6)

    assert clusters == []


def test_parallel_omits_short_repeats(tmp_path):
    _write_bundle(tmp_path, "KR0a0001", "aaaSHORTbbb")
    _write_bundle(tmp_path, "KR0a0002", "cccSHORTddd")
    out = _merge(tmp_path)

    clusters = discover_parallel_passages(out, seed="SHO", min_length=6)

    assert clusters == []


def test_parallel_suppresses_contained_clusters_by_default(tmp_path):
    # The shorter repeat appears inside the longer repeat at the first two
    # locations and once by itself at a third location.
    _write_bundle(tmp_path, "KR0a0001", "aaAAAABBBBCCCCbb")
    _write_bundle(tmp_path, "KR0a0002", "ccAAAABBBBCCCCdd")
    _write_bundle(tmp_path, "KR0a0003", "eeBBBBff")
    out = _merge(tmp_path)

    default_clusters = discover_parallel_passages(out, seed="BBB", min_length=4)
    all_clusters = discover_parallel_passages(
        out, seed="BBB", min_length=4, include_contained=True,
    )

    assert [c.text for c in default_clusters] == ["AAAABBBBCCCC"]
    assert {c.text for c in all_clusters} >= {"AAAABBBBCCCC", "BBBB"}


def test_parallel_bucket_default_body_and_all(tmp_path):
    shared = "FRONT-ONLY-PASSAGE"
    _write_bundle(tmp_path, "KR0a0001", "body one", front_text=f"aa{shared}")
    _write_bundle(tmp_path, "KR0a0002", "body two", front_text=f"bb{shared}")
    out = _merge(tmp_path)

    body_clusters = discover_parallel_passages(out, seed="FRO", min_length=12)
    all_clusters = discover_parallel_passages(
        out, seed="FRO", bucket="all", min_length=12,
    )

    assert body_clusters == []
    assert [c.text for c in all_clusters] == [shared]
    assert {loc.bucket for loc in all_clusters[0].locations} == {"front"}


def test_parallel_cli_writes_jsonl(tmp_path):
    shared = "CLI-PASSAGE-XYZ"
    _write_bundle(tmp_path, "KR0a0001", f"aa{shared}bb")
    _write_bundle(tmp_path, "KR0a0002", f"cc{shared}dd")
    out = _merge(tmp_path)
    report = tmp_path / "parallels.jsonl"

    rc = cli_run([
        "parallel",
        str(out),
        "CLI",
        "--out",
        str(report),
        "--min-length",
        "12",
    ])

    assert rc == 0
    rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["text"] == shared
    assert rows[0]["occurrence_count"] == 2
    assert rows[0]["locations"][0]["textid"] == "KR0a0001"


def test_parallel_cli_requires_seed_unless_full_scan(tmp_path):
    _write_bundle(tmp_path, "KR0a0001", "abcdef")
    out = _merge(tmp_path)

    with pytest.raises(SystemExit) as exc:
        cli_run(["parallel", str(out)])

    assert exc.value.code == 2


def test_parallel_target_scans_whole_index_and_writes_target_assets(tmp_path):
    shared = "TARGET-SHARED-PASSAGE"
    remote_only = "REMOTE-ONLY-PASSAGE"
    target = _write_bundle(
        tmp_path, "KR0a0001", f"aa{shared}bb",
    )
    _write_bundle(
        tmp_path, "KR0a0002", f"cc{shared}dd{remote_only}ee",
    )
    _write_bundle(
        tmp_path, "KR0a0003", f"ff{remote_only}gg",
    )
    out = _merge(tmp_path)
    targeted = discover_parallel_passages(
        out,
        target_textid="KR0a0001",
        min_length=12,
    )
    assert [cluster.text for cluster in targeted] == [shared]

    rc = cli_run([
        "parallel",
        str(out),
        "--text-id",
        "KR0a0001",
        "--min-length",
        "12",
    ])

    assert rc == 0
    path = (
        target / "parallels"
        / "KR0a0001_001.corpus.parallels.yaml"
    )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["provenance"]["generator"]["command"] == "bkk index parallel"
    assert data["provenance"]["generator"]["algorithm"] == "targeted-trigram-v1"
    assert data["provenance"]["index"] == {
        "filename": "_corpus.bkkx",
        "hash": compute_bkkx_hash(out),
        "schema_version": 3,
    }
    assert data["provenance"]["scan"]["text_id"] == "KR0a0001"
    assert data["provenance"]["scan"]["min_length"] == 12
    assert data["provenance"]["generated_at"].endswith("Z")
    markers = data["markers"]["body"]
    assert len(markers) == 1
    assert markers[0]["ref"].startswith("0a2/1/")
    assert not (tmp_path / "KR0a0002" / "parallels").exists()
    assert not (tmp_path / "KR0a0003" / "parallels").exists()


def test_parallel_index_hash_is_cached(tmp_path, monkeypatch):
    _write_bundle(tmp_path, "KR0a0001", "abcdef")
    out = _merge(tmp_path)
    real_compute = parallel_assets.compute_bkkx_hash
    calls = 0

    def counted_compute(path):
        nonlocal calls
        calls += 1
        return real_compute(path)

    monkeypatch.setattr(parallel_assets, "compute_bkkx_hash", counted_compute)
    first = parallel_assets.capture_index_snapshot(
        out, command="test", algorithm="test-v1", scan={},
    )
    second = parallel_assets.capture_index_snapshot(
        out, command="test", algorithm="test-v1", scan={},
    )

    assert calls == 1
    assert (
        first.provenance["index"]["hash"]
        == second.provenance["index"]["hash"]
    )
    cache_path = parallel_assets.index_hash_cache_path(out)
    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached["version"] == 1
    assert cached["hash"] == first.provenance["index"]["hash"]
    assert cache_path.stat().st_mode & 0o777 == 0o644


def test_parallel_index_hash_cache_invalidates_and_recovers(tmp_path, monkeypatch):
    _write_bundle(tmp_path, "KR0a0001", "abcdef")
    out = _merge(tmp_path)
    real_compute = parallel_assets.compute_bkkx_hash
    calls = 0

    def counted_compute(path):
        nonlocal calls
        calls += 1
        return real_compute(path)

    monkeypatch.setattr(parallel_assets, "compute_bkkx_hash", counted_compute)
    parallel_assets.capture_index_snapshot(
        out, command="test", algorithm="test-v1", scan={},
    )
    stat = out.stat()
    os.utime(
        out,
        ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000),
    )
    parallel_assets.capture_index_snapshot(
        out, command="test", algorithm="test-v1", scan={},
    )
    assert calls == 2

    parallel_assets.index_hash_cache_path(out).write_text(
        "{not-json}\n", encoding="utf-8",
    )
    parallel_assets.capture_index_snapshot(
        out, command="test", algorithm="test-v1", scan={},
    )
    assert calls == 3


def test_parallel_target_uses_configured_index_and_removes_stale_file(
    tmp_path, monkeypatch,
):
    _write_bundle(tmp_path, "KR0a0001", "no repeated passage here")
    _write_bundle(tmp_path, "KR0a0002", "entirely different content")
    out = _merge(tmp_path)
    parallels = tmp_path / "KR0a0001" / "parallels"
    parallels.mkdir()
    stale = parallels / "KR0a0001_002.corpus.parallels.yaml"
    stale.write_text("stale\n", encoding="utf-8")
    other = parallels / "KR0a0001_002.other.parallels.yaml"
    other.write_text("keep\n", encoding="utf-8")
    monkeypatch.setattr(
        "bkk.config.load_rc",
        lambda: {"index": {"out": out}, "global": {"corpus": tmp_path}},
    )

    rc = cli_run([
        "parallel",
        "--text-id",
        "0a1",
        "--min-length",
        "12",
    ])

    assert rc == 0
    assert not stale.exists()
    assert other.read_text(encoding="utf-8") == "keep\n"


@pytest.mark.parametrize(
    ("selector", "name"),
    [
        ("KR1h0004/1", "canonical"),
        ("1h4/1", "shortcut"),
    ],
)
def test_parallel_target_can_scan_one_juan(
    tmp_path, selector, name,
):
    first = "JUAN-ONE-PARALLEL"
    second = "JUAN-TWO-PARALLEL"
    target = _write_bundle(
        tmp_path,
        "KR1h0004",
        f"aa{first}bb",
        second_body_text=f"cc{second}dd",
    )
    _write_bundle(
        tmp_path,
        "KR1h0005",
        f"xx{first}yy{second}zz",
    )
    out = _merge(tmp_path)

    clusters = discover_parallel_passages(
        out,
        target_textid="KR1h0004",
        target_juan_seq=1,
        min_length=12,
    )
    assert [cluster.text for cluster in clusters] == [first]

    parallels = target / "parallels"
    parallels.mkdir()
    unrelated = parallels / f"KR1h0004_002.{name}.parallels.yaml"
    unrelated.write_text("keep\n", encoding="utf-8")

    rc = cli_run([
        "parallel",
        str(out),
        "--text-id",
        selector,
        "--name",
        name,
        "--min-length",
        "12",
    ])

    assert rc == 0
    generated = parallels / f"KR1h0004_001.{name}.parallels.yaml"
    data = yaml.safe_load(generated.read_text(encoding="utf-8"))
    assert data["provenance"]["scan"]["text_id"] == "KR1h0004"
    assert data["provenance"]["scan"]["juan"] == 1
    assert len(data["markers"]["body"]) == 1
    assert data["markers"]["body"][0]["ref"].startswith("1h5/1/")
    assert unrelated.read_text(encoding="utf-8") == "keep\n"


def test_parallel_two_character_seed_extends_to_full_passage(tmp_path):
    shared = "LEFTXYRIGHT"
    _write_bundle(tmp_path, "KR0a0001", f"aa{shared}bb")
    _write_bundle(tmp_path, "KR0a0002", f"cc{shared}dd")
    out = _merge(tmp_path)

    clusters = discover_parallel_passages(out, seed="XY", min_length=6)

    assert len(clusters) == 1
    assert clusters[0].text == shared


def test_parallel_six_character_seed_extends_to_full_passage(tmp_path):
    shared = "ALPHABETA-PASSAGE"
    _write_bundle(tmp_path, "KR0a0001", f"qq{shared}ww")
    _write_bundle(tmp_path, "KR0a0002", f"ee{shared}rr ALPZZZ noise")
    _write_bundle(tmp_path, "KR0a0003", "ALPnope filler")
    out = _merge(tmp_path)

    clusters = discover_parallel_passages(out, seed="ALPHAB", min_length=10)

    assert len(clusters) == 1
    assert clusters[0].text == shared
    assert clusters[0].occurrence_count == 2


def test_parallel_seed_postings_cap_prevents_blowup(tmp_path):
    _write_bundle(tmp_path, "KR0a0001", "AxxAxxA")
    _write_bundle(tmp_path, "KR0a0002", "AyyAyyA")
    out = _merge(tmp_path)

    try:
        discover_parallel_passages(out, seed="A", max_postings=3)
    except ValueError as e:
        assert "more than 3 times" in str(e)
    else:
        raise AssertionError("expected seed posting cap to raise")


def test_parallel_scan_finds_repeated_passage(tmp_path):
    shared = "SCAN-PASSAGE-ALPHA"
    _write_bundle(tmp_path, "KR0a0001", f"aaa{shared}bbb")
    _write_bundle(tmp_path, "KR0a0002", f"ccc{shared}ddd")
    out = _merge(tmp_path)

    clusters, stats = discover_parallel_passages_scan(
        out,
        min_length=10,
        anchor_length=5,
        partitions=4,
        work_dir=tmp_path,
    )

    assert stats.bucket_count == 2
    assert stats.anchors_written > 0
    assert len(clusters) == 1
    assert clusters[0].text == shared
    assert clusters[0].occurrence_count == 2


def test_parallel_scan_extends_at_bucket_boundaries(tmp_path):
    shared = "BOUNDARY-PASSAGE"
    _write_bundle(tmp_path, "KR0a0001", f"{shared}tail")
    _write_bundle(tmp_path, "KR0a0002", f"{shared}end")
    out = _merge(tmp_path)

    clusters, _stats = discover_parallel_passages_scan(
        out,
        min_length=10,
        anchor_length=5,
        partitions=2,
        work_dir=tmp_path,
    )

    assert [c.text for c in clusters] == [shared]
    assert [loc.start for loc in clusters[0].locations] == [0, 0]


def test_parallel_scan_skips_common_anchor_group(tmp_path):
    for i in range(6):
        _write_bundle(tmp_path, f"KR0a{i:04d}", "xxCOMMON-ANCHOR-yy")
    out = _merge(tmp_path)

    clusters, stats = discover_parallel_passages_scan(
        out,
        min_length=8,
        anchor_length=6,
        max_anchor_occurrences=3,
        partitions=1,
        work_dir=tmp_path,
    )

    assert clusters == []
    assert stats.skipped_anchor_groups > 0
    assert stats.candidate_spans == 0


def test_parallel_scan_partitioned_matches_unpartitioned(tmp_path):
    shared = "PARTITIONED-PASSAGE"
    _write_bundle(tmp_path, "KR0a0001", f"aa{shared}bb")
    _write_bundle(tmp_path, "KR0a0002", f"cc{shared}dd")
    _write_bundle(tmp_path, "KR0a0003", f"ee{shared}ff")
    out = _merge(tmp_path)

    one_part, _ = discover_parallel_passages_scan(
        out,
        min_length=10,
        anchor_length=5,
        partitions=1,
        work_dir=tmp_path,
    )
    many_parts, _ = discover_parallel_passages_scan(
        out,
        min_length=10,
        anchor_length=5,
        partitions=5,
        work_dir=tmp_path,
    )

    assert [(c.text, c.occurrence_count) for c in many_parts] == [
        (c.text, c.occurrence_count) for c in one_part
    ]


def test_parallel_scan_jobs_matches_serial(tmp_path):
    first = "JOBS-PASSAGE-ALPHA"
    second = "JOBS-PASSAGE-BETA"
    _write_bundle(tmp_path, "KR0a0001", f"aa{first}bb{second}cc")
    _write_bundle(tmp_path, "KR0a0002", f"dd{first}ee")
    _write_bundle(tmp_path, "KR0a0003", f"ff{second}gg{first}hh")
    out = _merge(tmp_path)

    serial, serial_stats = discover_parallel_passages_scan(
        out,
        min_length=10,
        anchor_length=5,
        partitions=7,
        work_dir=tmp_path,
    )
    parallel, parallel_stats = discover_parallel_passages_scan(
        out,
        min_length=10,
        anchor_length=5,
        partitions=7,
        jobs=2,
        work_dir=tmp_path,
    )

    assert _cluster_signature(parallel) == _cluster_signature(serial)
    assert parallel_stats.candidate_spans == serial_stats.candidate_spans
    assert parallel_stats.skipped_anchor_groups == serial_stats.skipped_anchor_groups


def test_parallel_scan_work_db_reuses_candidate_spans(tmp_path):
    shared = "WORK-DB-PASSAGE"
    _write_bundle(tmp_path, "KR0a0001", f"aa{shared}bb")
    _write_bundle(tmp_path, "KR0a0002", f"cc{shared}dd")
    out = _merge(tmp_path)
    work_db = tmp_path / "scan-work.sqlite3"
    first_progress = io.StringIO()
    second_progress = io.StringIO()

    first, first_stats = discover_parallel_passages_scan(
        out,
        min_length=10,
        anchor_length=5,
        partitions=3,
        work_dir=tmp_path,
        work_db=work_db,
        progress=first_progress,
    )
    second, second_stats = discover_parallel_passages_scan(
        out,
        min_length=10,
        anchor_length=5,
        partitions=3,
        work_dir=tmp_path,
        work_db=work_db,
        progress=second_progress,
    )

    assert work_db.exists()
    assert _cluster_signature(second) == _cluster_signature(first)
    assert second_stats.anchors_written == first_stats.anchors_written
    assert second_stats.candidate_spans == first_stats.candidate_spans
    assert second_stats.anchor_seconds == 0.0
    assert "anchors written" in first_progress.getvalue()
    assert "reusing work DB" in second_progress.getvalue()


def test_parallel_scan_work_db_rejects_mismatch_unless_forced(tmp_path):
    shared = "WORK-DB-MISMATCH"
    _write_bundle(tmp_path, "KR0a0001", f"aa{shared}bb")
    _write_bundle(tmp_path, "KR0a0002", f"cc{shared}dd")
    out = _merge(tmp_path)
    work_db = tmp_path / "scan-work.sqlite3"
    discover_parallel_passages_scan(
        out,
        min_length=10,
        anchor_length=5,
        partitions=3,
        work_dir=tmp_path,
        work_db=work_db,
    )

    with pytest.raises(ValueError, match="metadata mismatch"):
        discover_parallel_passages_scan(
            out,
            min_length=11,
            anchor_length=5,
            partitions=3,
            work_dir=tmp_path,
            work_db=work_db,
        )

    clusters, _stats = discover_parallel_passages_scan(
        out,
        min_length=11,
        anchor_length=5,
        partitions=3,
        work_dir=tmp_path,
        work_db=work_db,
        force_work_db=True,
    )
    assert [cluster.text for cluster in clusters] == [shared]


def test_parallel_scan_stats_and_progress_include_timings(tmp_path):
    shared = "TIMING-PASSAGE"
    _write_bundle(tmp_path, "KR0a0001", f"aa{shared}bb")
    _write_bundle(tmp_path, "KR0a0002", f"cc{shared}dd")
    out = _merge(tmp_path)
    progress = io.StringIO()

    _clusters, stats = discover_parallel_passages_scan(
        out,
        min_length=10,
        anchor_length=5,
        partitions=3,
        work_dir=tmp_path,
        progress=progress,
    )

    assert stats.anchor_seconds >= 0.0
    assert stats.partition_seconds >= 0.0
    assert stats.cluster_seconds >= 0.0
    assert stats.total_seconds >= 0.0
    log = progress.getvalue()
    assert "anchors written" in log
    assert ": loading" in log
    assert "partition processing" in log


def test_parallel_scan_emits_heartbeat_inside_large_groups(
    tmp_path, monkeypatch,
):
    shared = "HEARTBEAT-PASSAGE"
    _write_bundle(tmp_path, "KR0a0001", f"aa{shared}bb")
    _write_bundle(tmp_path, "KR0a0002", f"cc{shared}dd")
    _write_bundle(tmp_path, "KR0a0003", f"ee{shared}ff")
    out = _merge(tmp_path)
    progress = io.StringIO()
    monkeypatch.setattr(parallel_scan, "_GROUP_HEARTBEAT_SECONDS", 0.0)
    monkeypatch.setattr(parallel_scan, "_PAIR_HEARTBEAT_CHECK_INTERVAL", 1)

    discover_parallel_passages_scan(
        out,
        min_length=10,
        anchor_length=5,
        partitions=1,
        work_dir=tmp_path,
        progress=progress,
    )

    assert "heartbeat:" in progress.getvalue()


def test_parallel_scan_cli_writes_jsonl_and_progress(tmp_path, capsys):
    shared = "CLI-SCAN-PASSAGE"
    _write_bundle(tmp_path, "KR0a0001", f"aa{shared}bb")
    _write_bundle(tmp_path, "KR0a0002", f"cc{shared}dd")
    out = _merge(tmp_path)
    report = tmp_path / "scan.jsonl"

    rc = cli_run([
        "parallel-scan",
        str(out),
        "--out",
        str(report),
        "--work-dir",
        str(tmp_path),
        "--min-length",
        "10",
        "--anchor-length",
        "5",
        "--partitions",
        "3",
        "--jobs",
        "2",
    ])

    captured = capsys.readouterr()
    assert rc == 0
    assert "anchors written" in captured.err
    assert "partition workers:" in captured.err
    rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["text"] == shared


def test_parallel_scan_cli_rejects_nonpositive_jobs(tmp_path):
    _write_bundle(tmp_path, "KR0a0001", "abcdef")
    out = _merge(tmp_path)

    with pytest.raises(SystemExit) as exc:
        cli_run(["parallel-scan", str(out), "--jobs", "0"])

    assert exc.value.code == 2


def test_parallel_full_scan_disabled_for_corpus_index(tmp_path):
    _write_bundle(tmp_path, "KR0a0001", "abcdef")
    out = _merge(tmp_path)

    with pytest.raises(SystemExit) as exc:
        cli_run(["parallel", str(out), "--full-scan"])

    assert exc.value.code == 2


def test_parallel_fuzzy_finds_substitution(tmp_path):
    # Two passages differ by one character on the right of the seed.
    _write_bundle(tmp_path, "KR0a0001", "qqABCDEFGKEY1234X678ww")
    _write_bundle(tmp_path, "KR0a0002", "rrABCDEFGKEY1234Y678ee")
    out = _merge(tmp_path)

    exact = discover_parallel_passages(out, seed="KEY", min_length=16)
    fuzzy = discover_parallel_passages(
        out, seed="KEY", min_length=16, max_edits=1,
    )

    assert exact == []
    assert len(fuzzy) == 1
    c = fuzzy[0]
    assert c.length == 18
    assert c.occurrence_count == 2
    assert c.representative_edits == 1
    assert {loc.edit_distance for loc in c.locations} == {0, 1}


def test_parallel_fuzzy_finds_deletion(tmp_path):
    # Bundle 2 drops the '4' after KEY; right-side spans now differ in length.
    _write_bundle(tmp_path, "KR0a0001", "qqABCDEFGKEY1234567ww")
    _write_bundle(tmp_path, "KR0a0002", "rrABCDEFGKEY123567ee")
    out = _merge(tmp_path)

    clusters = discover_parallel_passages(
        out, seed="KEY", min_length=15, max_edits=1,
    )

    assert len(clusters) == 1
    c = clusters[0]
    assert c.occurrence_count == 2
    assert c.representative_edits == 1
    starts = {loc.textid: (loc.start, loc.end) for loc in c.locations}
    span_1 = starts["KR0a0001"][1] - starts["KR0a0001"][0]
    span_2 = starts["KR0a0002"][1] - starts["KR0a0002"][0]
    assert {span_1, span_2} == {17, 16}
    longer_text = "ABCDEFGKEY1234567"
    assert c.text == longer_text


def test_parallel_fuzzy_max_edits_budget(tmp_path):
    # Three substitutions: two on the left of the seed (C->x, G->y) and one
    # on the right (K->z). With budget 2 no split covers the full passage at
    # min_length=18; budget 3 does.
    _write_bundle(tmp_path, "KR0a0001", "aaABCDEFGHKEYIJKLMNOPbb")
    _write_bundle(tmp_path, "KR0a0002", "ccABxDEFyHKEYIJzLMNOPdd")
    out = _merge(tmp_path)

    too_tight = discover_parallel_passages(
        out, seed="KEY", min_length=18, max_edits=2,
    )
    enough = discover_parallel_passages(
        out, seed="KEY", min_length=18, max_edits=3,
    )

    assert too_tight == []
    assert len(enough) == 1
    assert enough[0].length == 19
    assert enough[0].representative_edits == 3


def test_parallel_fuzzy_representative_is_longest(tmp_path):
    # Three occurrences: bundle 1 is the longest (representative); bundles 2-3
    # are each one substitution away.
    _write_bundle(tmp_path, "KR0a0001", "aaABCDEFGKEYHIJKLMNOPbb")
    _write_bundle(tmp_path, "KR0a0002", "ccABCDEFGKEYHIJxLMNOPdd")
    _write_bundle(tmp_path, "KR0a0003", "eeABCDEFGKEYHyJKLMNOPff")
    out = _merge(tmp_path)

    clusters = discover_parallel_passages(
        out, seed="KEY", min_length=18, max_edits=1,
    )

    assert len(clusters) == 1
    c = clusters[0]
    assert c.text == "ABCDEFGKEYHIJKLMNOP"
    assert c.occurrence_count == 3
    by_textid = {loc.textid: loc.edit_distance for loc in c.locations}
    assert by_textid == {"KR0a0001": 0, "KR0a0002": 1, "KR0a0003": 1}


def test_parallel_fuzzy_cli_emits_edit_distance(tmp_path):
    _write_bundle(tmp_path, "KR0a0001", "qqABCDEFGKEY1234X678ww")
    _write_bundle(tmp_path, "KR0a0002", "rrABCDEFGKEY1234Y678ee")
    out = _merge(tmp_path)
    report = tmp_path / "fuzzy.jsonl"

    rc = cli_run([
        "parallel",
        str(out),
        "KEY",
        "--out", str(report),
        "--min-length", "16",
        "--max-edits", "1",
    ])

    assert rc == 0
    rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["representative_edits"] == 1
    assert {loc["edit_distance"] for loc in rows[0]["locations"]} == {0, 1}


def test_parallel_fuzzy_from_scan_extends_jsonl_candidates(tmp_path):
    _write_bundle(tmp_path, "KR0a0001", "qqABCDEFGKEY1234X678ww")
    _write_bundle(tmp_path, "KR0a0002", "rrABCDEFGKEY1234Y678ee")
    out = _merge(tmp_path)
    exact_report = tmp_path / "exact.jsonl"
    exact_clusters, _stats = discover_parallel_passages_scan(
        out,
        min_length=10,
        anchor_length=5,
        partitions=3,
        work_dir=tmp_path,
    )
    write_parallel_report(exact_clusters, exact_report, format="jsonl")

    fuzzy = discover_fuzzy_from_scan(
        out,
        exact_report,
        max_edits=1,
        min_length=16,
    )

    assert len(fuzzy) == 1
    assert fuzzy[0].representative_edits == 1
    assert {loc.edit_distance for loc in fuzzy[0].locations} == {0, 1}
    assert fuzzy[0].length == 18


def test_parallel_fuzzy_from_scan_cli_writes_jsonl(tmp_path):
    _write_bundle(tmp_path, "KR0a0001", "qqABCDEFGKEY1234X678ww")
    _write_bundle(tmp_path, "KR0a0002", "rrABCDEFGKEY1234Y678ee")
    out = _merge(tmp_path)
    exact_report = tmp_path / "exact.jsonl"
    fuzzy_report = tmp_path / "fuzzy-from-scan.jsonl"
    exact_clusters, _stats = discover_parallel_passages_scan(
        out,
        min_length=10,
        anchor_length=5,
        partitions=3,
        work_dir=tmp_path,
    )
    write_parallel_report(exact_clusters, exact_report, format="jsonl")

    rc = cli_run([
        "parallel-fuzzy-from-scan",
        str(out),
        str(exact_report),
        "--out", str(fuzzy_report),
        "--max-edits", "1",
        "--min-length", "16",
        "--quiet",
    ])

    assert rc == 0
    rows = [json.loads(line) for line in fuzzy_report.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["representative_edits"] == 1
    assert {loc["edit_distance"] for loc in rows[0]["locations"]} == {0, 1}


def _apply_diff(rep_text: str, diff) -> str:
    """Reconstruct an occurrence by applying ``diff`` ops to the cluster's
    representative text."""
    out: list[str] = []
    i = 0
    for op in diff:
        tag = op[0]
        if tag == "=":
            n = op[1]
            out.append(rep_text[i:i + n])
            i += n
        elif tag == "s":
            assert rep_text[i] == op[1]
            out.append(op[2])
            i += 1
        elif tag == "i":
            out.append(op[1])
        elif tag == "d":
            assert rep_text[i] == op[1]
            i += 1
        else:
            raise AssertionError(f"unknown op: {op!r}")
    assert i == len(rep_text)
    return "".join(out)


def test_parallel_fuzzy_locations_carry_text_and_diff(tmp_path):
    _write_bundle(tmp_path, "KR0a0001", "qqABCDEFGKEY1234X678ww")
    _write_bundle(tmp_path, "KR0a0002", "rrABCDEFGKEY1234Y678ee")
    _write_bundle(tmp_path, "KR0a0003", "ssABCDEFGKEY134Y678oo")  # one deletion + one sub
    out = _merge(tmp_path)

    clusters = discover_parallel_passages(
        out, seed="KEY", min_length=16, max_edits=2,
    )

    assert len(clusters) == 1
    c = clusters[0]
    by_textid = {loc.textid: loc for loc in c.locations}
    for textid, loc in by_textid.items():
        if loc.edit_distance == 0:
            # Exact occurrence: text & diff intentionally empty.
            assert loc.text == ""
            assert loc.diff == ()
        else:
            assert loc.text, f"{textid} should carry its own text"
            assert loc.diff, f"{textid} should carry an alignment"
            # Number of non-equal ops equals the edit distance.
            non_eq = sum(1 for op in loc.diff if op[0] != "=")
            assert non_eq == loc.edit_distance
            # Diff applied to the cluster representative reproduces loc.text.
            assert _apply_diff(c.text, loc.diff) == loc.text


def test_parallel_fuzzy_rejects_max_edits_out_of_range(tmp_path):
    _write_bundle(tmp_path, "KR0a0001", "abcdef")
    out = _merge(tmp_path)

    with pytest.raises(ValueError):
        discover_parallel_passages(out, seed="abc", max_edits=5)


def test_parallel_lookup_build_and_find_at(tmp_path):
    shared = "LOOKUP-PASSAGE"
    _write_bundle(tmp_path, "KR0a0001", f"aa{shared}bb")
    _write_bundle(tmp_path, "KR0a0002", f"cc{shared}dd")
    out = _merge(tmp_path)
    lookup_path = tmp_path / "_corpus.bkkp"

    stats = build_parallel_lookup(
        out,
        lookup_path,
        min_length=8,
        anchor_length=4,
        max_edits=0,
        partitions=4,
    )

    assert stats.lookup_path == lookup_path
    assert stats.clusters == 1
    with ParallelLookup(out, lookup_path) as lookup:
        clusters = lookup.find_at(
            "KR0a0001",
            1,
            4,
            "body",
            min_length=8,
            include_self=False,
        )
        outside = lookup.find_at(
            "KR0a0001",
            1,
            0,
            "body",
            min_length=8,
            include_self=False,
        )

    assert outside == []
    assert len(clusters) == 1
    assert clusters[0].text == shared
    assert clusters[0].occurrence_count == 1
    assert [(loc.textid, loc.start, loc.end) for loc in clusters[0].locations] == [
        ("KR0a0002", 2, 2 + len(shared)),
    ]


def test_parallel_lookup_runtime_filters_and_modes(tmp_path):
    rep = "LOOKUP-ABCDE-ZZ"
    fuzzy = "LOOKUP-ABXDE-ZZ"
    _write_bundle(tmp_path, "KR0a0001", f"aa{rep}bb")
    _write_bundle(tmp_path, "KR0a0002", f"cc{fuzzy}dd")
    out = _merge(tmp_path)
    lookup_path = tmp_path / "_corpus.bkkp"
    build_parallel_lookup(
        out,
        lookup_path,
        min_length=6,
        anchor_length=4,
        max_edits=1,
        partitions=4,
        include_contained=True,
    )

    with ParallelLookup(out, lookup_path) as lookup:
        exact_only = lookup.find_at(
            "KR0a0001", 1, 4, "body", min_length=6, max_edits=0,
        )
        fuzzy_match = lookup.find_at(
            "KR0a0001", 1, 4, "body", min_length=6, max_edits=1,
        )
        too_long = lookup.find_at(
            "KR0a0001", 1, 4, "body", min_length=len(rep) + 1, max_edits=1,
        )
        overlap_end = lookup.find_at(
            "KR0a0001", 1, 2 + len(rep), "body",
            min_length=6, max_edits=1, mode="overlap",
        )
        cover_end = lookup.find_at(
            "KR0a0001", 1, 2 + len(rep), "body",
            min_length=6, max_edits=1, mode="cover",
        )

    assert exact_only == []
    assert too_long == []
    assert overlap_end == []
    assert len(cover_end) == 1
    assert len(fuzzy_match) == 1
    assert fuzzy_match[0].locations[0].textid == "KR0a0002"
    assert fuzzy_match[0].locations[0].edit_distance == 1
    assert fuzzy_match[0].locations[0].text == fuzzy


def test_parallel_lookup_rejects_stale_sidecar(tmp_path):
    shared = "STALE-LOOKUP-PASSAGE"
    _write_bundle(tmp_path, "KR0a0001", f"aa{shared}bb")
    _write_bundle(tmp_path, "KR0a0002", f"cc{shared}dd")
    out = _merge(tmp_path)
    lookup_path = tmp_path / "_corpus.bkkp"
    build_parallel_lookup(
        out,
        lookup_path,
        min_length=8,
        anchor_length=4,
        max_edits=0,
        partitions=4,
    )

    conn = sqlite3.connect(out)
    try:
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('parallel_lookup_test', 'stale')"
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(ParallelLookupStaleError):
        ParallelLookup(out, lookup_path)


def test_parallel_lookup_cli_writes_jsonl(tmp_path):
    shared = "CLI-LOOKUP-PASSAGE"
    _write_bundle(tmp_path, "KR0a0001", f"aa{shared}bb")
    _write_bundle(tmp_path, "KR0a0002", f"cc{shared}dd")
    out = _merge(tmp_path)
    lookup_path = tmp_path / "_corpus.bkkp"

    rc = cli_run([
        "parallel-lookup-build",
        str(out),
        "--out",
        str(lookup_path),
        "--min-length",
        "8",
        "--anchor-length",
        "4",
        "--max-edits",
        "0",
        "--partitions",
        "4",
        "--quiet",
    ])
    assert rc == 0

    report = tmp_path / "lookup.jsonl"
    rc = cli_run([
        "parallel-lookup-at",
        str(out),
        "KR0a0001",
        "1",
        "body",
        "4",
        "--lookup",
        str(lookup_path),
        "--min-length",
        "8",
        "--out",
        str(report),
    ])

    assert rc == 0
    rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["text"] == shared
    assert rows[0]["locations"][0]["textid"] == "KR0a0002"


def test_parallel_lookup_sketch_prefilter_tables_are_populated(tmp_path):
    shared = "SKETCH-LOOKUP-PASSAGE"
    _write_bundle(tmp_path, "KR0a0001", f"aa{shared}bb")
    _write_bundle(tmp_path, "KR0a0002", f"cc{shared}dd")
    out = _merge(tmp_path)
    lookup_path = tmp_path / "_corpus.bkkp"

    build_parallel_lookup(
        out,
        lookup_path,
        min_length=8,
        anchor_length=4,
        max_edits=0,
        partitions=4,
        enable_sketch_prefilter=True,
        sketch_k_gram=3,
        sketch_size=16,
        lsh_bands=4,
    )

    conn = sqlite3.connect(lookup_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM psketch").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM plsh_band").fetchone()[0] == 8
        meta = dict(conn.execute("SELECT key, value FROM meta"))
        assert meta["enable_sketch_prefilter"] == "1"
    finally:
        conn.close()
