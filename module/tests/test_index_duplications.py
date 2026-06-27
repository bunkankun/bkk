"""Juan-pair duplication aggregation over parallel-scan clusters."""

from __future__ import annotations

from pathlib import Path

import yaml

from bkk.index import build_index, merge_bundles
from bkk.index.cli import run as cli_run
from bkk.index.duplications import (
    REPORT_VERSION,
    JuanPairDuplication,
    ReportFormatError,
    _aggregate_pairs,
    _merge_spans,
    _merged_length,
    find_duplicated_juan,
    read_duplications_report,
    update_action,
)
from bkk.index.parallel import ParallelCluster, ParallelLocation


def _loc(bucket_id: int, textid: str, juan_seq: int, start: int, end: int) -> ParallelLocation:
    return ParallelLocation(
        textid=textid,
        juan_seq=juan_seq,
        bucket="body",
        bucket_id=bucket_id,
        start=start,
        end=end,
        toc_label=None,
        left="",
        right="",
    )


def _cluster(length: int, locations: list[ParallelLocation]) -> ParallelCluster:
    return ParallelCluster(
        cluster_id="x",
        length=length,
        occurrence_count=len(locations),
        text="x" * length,
        locations=tuple(locations),
    )


def test_merged_length_collapses_overlaps():
    assert _merged_length([]) == 0
    assert _merged_length([(0, 10)]) == 10
    assert _merged_length([(0, 10), (5, 15)]) == 15
    assert _merged_length([(0, 10), (10, 20)]) == 20  # touching counts as merged
    assert _merged_length([(0, 10), (20, 30)]) == 20
    assert _merged_length([(0, 5), (3, 4)]) == 5  # second is contained


def test_merge_spans():
    assert _merge_spans([]) == ()
    assert _merge_spans([(0, 10)]) == ((0, 10),)
    assert _merge_spans([(0, 10), (5, 15)]) == ((0, 15),)
    assert _merge_spans([(0, 10), (10, 20)]) == ((0, 20),)
    assert _merge_spans([(20, 30), (0, 10)]) == ((0, 10), (20, 30))
    assert _merge_spans([(0, 5), (3, 4)]) == ((0, 5),)


def test_aggregate_pairs_cross_juan():
    cluster = _cluster(
        500,
        [_loc(1, "A", 1, 0, 500), _loc(2, "B", 1, 100, 600)],
    )
    rows = _aggregate_pairs([cluster])
    assert len(rows) == 1
    row = rows[0]
    # a is always the smaller bucket_id, so a = A, b = B.
    assert row.a.textid == "A"
    assert row.b.textid == "B"
    assert row.chars_a == 500
    assert row.chars_b == 500
    assert row.longest_span == 500
    assert row.longest_a == (0, 500)
    assert row.longest_b == (100, 600)
    assert row.spans_a == ((0, 500),)
    assert row.spans_b == ((100, 600),)
    assert row.cluster_count == 1


def test_aggregate_pairs_cross_juan_swaps_to_smaller_bucket_id():
    # Cluster locations supplied in (high, low) order — aggregator must
    # canonicalise so a is the smaller bucket_id.
    cluster = _cluster(
        500,
        [_loc(7, "B", 1, 100, 600), _loc(3, "A", 1, 0, 500)],
    )
    rows = _aggregate_pairs([cluster])
    assert len(rows) == 1
    row = rows[0]
    assert row.a.bucket_id == 3 and row.a.textid == "A"
    assert row.b.bucket_id == 7 and row.b.textid == "B"
    assert row.longest_a == (0, 500)
    assert row.longest_b == (100, 600)


def test_aggregate_pairs_intra_juan_merges_unique_positions():
    # One juan with the same 300-char block at positions 0 and 1000.
    cluster = _cluster(
        300,
        [_loc(1, "A", 1, 0, 300), _loc(1, "A", 1, 1000, 1300)],
    )
    rows = _aggregate_pairs([cluster])
    assert len(rows) == 1
    row = rows[0]
    assert row.a.bucket_id == row.b.bucket_id == 1
    assert row.chars_a == 600
    assert row.chars_b == 600
    assert row.longest_span == 300
    # Intra-juan: spans_a / spans_b cover both copies, but longest_a / longest_b
    # are the two distinct copies of the longest cluster.
    assert row.spans_a == ((0, 300), (1000, 1300))
    assert row.spans_b == row.spans_a
    assert row.longest_a == (0, 300)
    assert row.longest_b == (1000, 1300)
    assert row.cluster_count == 1


def test_aggregate_pairs_two_clusters_same_pair_dedup_overlap():
    # Two clusters covering overlapping spans in juan A vs. juan B.
    # Side-A spans: [0,300) and [200,500) → merged into one [0,500) span.
    c1 = _cluster(300, [_loc(1, "A", 1, 0, 300), _loc(2, "B", 1, 0, 300)])
    c2 = _cluster(300, [_loc(1, "A", 1, 200, 500), _loc(2, "B", 1, 200, 500)])
    rows = _aggregate_pairs([c1, c2])
    assert len(rows) == 1
    row = rows[0]
    assert row.chars_a == 500
    assert row.chars_b == 500
    assert row.cluster_count == 2
    assert row.longest_span == 300
    assert row.spans_a == ((0, 500),)
    assert row.spans_b == ((0, 500),)


def test_aggregate_pairs_three_occurrences_one_pair():
    # Cluster with three locations across three juan → three pairs.
    cluster = _cluster(
        200,
        [
            _loc(1, "A", 1, 0, 200),
            _loc(2, "B", 1, 0, 200),
            _loc(3, "C", 1, 0, 200),
        ],
    )
    rows = _aggregate_pairs([cluster])
    pair_keys = {tuple(sorted([r.a.textid, r.b.textid])) for r in rows}
    assert pair_keys == {("A", "B"), ("A", "C"), ("B", "C")}
    for r in rows:
        assert r.chars_a == 200
        assert r.chars_b == 200


# ---- end-to-end ----------------------------------------------------------

def _write_bundle(root: Path, textid: str, body_text: str) -> Path:
    bundle_dir = root / textid
    bundle_dir.mkdir(parents=True)
    juan = {
        "canonical_identifier": f"bkk:test/{textid}/v1/juan/1",
        "seq": 1,
        "body": {"text": body_text, "hash": "sha256:0", "markers": []},
        "hash": "sha256:0",
    }
    (bundle_dir / f"{textid}_001.yaml").write_text(
        yaml.safe_dump(juan, allow_unicode=True),
        encoding="utf-8",
    )
    (bundle_dir / f"{textid}.manifest.yaml").write_text(
        yaml.safe_dump({
            "canonical_identifier": f"bkk:test/{textid}/v1",
            "editions": [{"short": "X", "label": "x"}],
            "assets": {
                "parts": [
                    {"seq": 1, "filename": f"{textid}_001.yaml", "hash": "sha256:0"},
                ],
            },
            "table_of_contents": [
                {
                    "ref": {
                        "seq": 1,
                        "marker_id": f"{textid}_001-body",
                        "span": ["body", 0, len(body_text)],
                    },
                    "label": f"{textid} body",
                },
            ],
        }, allow_unicode=True),
        encoding="utf-8",
    )
    return bundle_dir


def _merge(root: Path) -> Path:
    out = root / "_corpus.bkkx"
    merge_bundles(root, out)
    return out


def _long_block(seed: str, length: int) -> str:
    # Deterministic non-repeating-trigram block.
    chars = []
    i = 0
    while len(chars) < length:
        chars.append(seed)
        chars.append(f"{i:04d}-")
        i += 1
    return "".join(chars)[:length]


def test_find_duplicated_juan_cross_juan(tmp_path):
    shared = _long_block("Q", 400)
    _write_bundle(tmp_path, "KR0a0001", f"aaa{shared}bbb")
    _write_bundle(tmp_path, "KR0a0002", f"ccc{shared}ddd")
    out = _merge(tmp_path)

    rows = find_duplicated_juan(
        out,
        min_length=200,
        min_pair_chars=100,
    )

    assert len(rows) == 1
    row = rows[0]
    assert {row.a.textid, row.b.textid} == {"KR0a0001", "KR0a0002"}
    assert row.chars_a >= 400
    assert row.chars_b >= 400
    assert row.longest_span >= 400
    assert row.juan_length_a > 0
    assert row.juan_length_b > 0
    assert row.coverage_a > 0.0


def test_find_duplicated_juan_filters_below_threshold(tmp_path):
    shared = _long_block("R", 250)
    _write_bundle(tmp_path, "KR0a0001", f"aaa{shared}bbb")
    _write_bundle(tmp_path, "KR0a0002", f"ccc{shared}ddd")
    out = _merge(tmp_path)

    rows = find_duplicated_juan(
        out,
        min_length=200,
        min_pair_chars=500,  # above the 250-char shared block
    )

    assert rows == []


def test_duplications_cli_writes_tsv(tmp_path):
    shared = _long_block("S", 400)
    _write_bundle(tmp_path, "KR0a0001", f"aaa{shared}bbb")
    _write_bundle(tmp_path, "KR0a0002", f"ccc{shared}ddd")
    out = _merge(tmp_path)
    report = tmp_path / "dups.tsv"

    rc = cli_run([
        "duplications",
        str(out),
        "--out", str(report),
        "--min-length", "200",
        "--min-pair-chars", "100",
        "--quiet",
    ])

    assert rc == 0
    lines = report.read_text(encoding="utf-8").splitlines()
    assert lines[0] == f"# bkk-duplications version={REPORT_VERSION}"
    assert lines[1].split("\t")[0] == "textid_a"
    assert len(lines) == 3
    fields = lines[2].split("\t")
    textids = {fields[0], fields[3]}
    assert textids == {"KR0a0001", "KR0a0002"}


def test_read_duplications_report_roundtrip(tmp_path):
    shared = _long_block("T", 400)
    _write_bundle(tmp_path, "KR0a0001", f"aaa{shared}bbb")
    _write_bundle(tmp_path, "KR0a0002", f"ccc{shared}ddd")
    out = _merge(tmp_path)
    report = tmp_path / "dups.tsv"
    cli_run([
        "duplications", str(out),
        "--out", str(report),
        "--min-length", "200", "--min-pair-chars", "100", "--quiet",
    ])

    rows = read_duplications_report(report)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == 1
    assert {row["textid_a"], row["textid_b"]} == {"KR0a0001", "KR0a0002"}
    assert row["intra_juan"] is False
    assert row["longest_span"] >= 400
    la_start, la_end = row["longest_a"]
    assert la_end - la_start == row["longest_span"]
    assert row["spans_a"] and isinstance(row["spans_a"][0], tuple)
    assert row["action"] is None


def test_update_action_atomic(tmp_path):
    shared = _long_block("U", 400)
    _write_bundle(tmp_path, "KR0a0001", f"aaa{shared}bbb")
    _write_bundle(tmp_path, "KR0a0002", f"ccc{shared}ddd")
    out = _merge(tmp_path)
    report = tmp_path / "dups.tsv"
    cli_run([
        "duplications", str(out),
        "--out", str(report),
        "--min-length", "200", "--min-pair-chars", "100", "--quiet",
    ])

    update_action(report, 1, "delete_b_juan", actor="alice", at="2026-06-28T10:00:00Z")
    rows = read_duplications_report(report)
    assert rows[0]["action"] == "delete_b_juan"
    assert rows[0]["action_actor"] == "alice"
    assert rows[0]["action_at"] == "2026-06-28T10:00:00Z"


def test_update_action_rejects_bad_action(tmp_path):
    shared = _long_block("V", 400)
    _write_bundle(tmp_path, "KR0a0001", f"aaa{shared}bbb")
    _write_bundle(tmp_path, "KR0a0002", f"ccc{shared}ddd")
    out = _merge(tmp_path)
    report = tmp_path / "dups.tsv"
    cli_run([
        "duplications", str(out),
        "--out", str(report),
        "--min-length", "200", "--min-pair-chars", "100", "--quiet",
    ])

    import pytest
    with pytest.raises(ValueError):
        update_action(report, 1, "nuke_everything", actor="x", at="t")
    with pytest.raises(ValueError):
        update_action(report, 999, "keep", actor="x", at="t")


def test_read_report_rejects_unversioned(tmp_path):
    bad = tmp_path / "old.tsv"
    bad.write_text("textid_a\ttextid_b\n", encoding="utf-8")
    import pytest
    with pytest.raises(ReportFormatError):
        read_duplications_report(bad)
