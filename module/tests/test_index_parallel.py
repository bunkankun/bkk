"""Parallel-passage discovery over the trigram index."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from bkk.index import build_index, merge_bundles
from bkk.index.cli import run as cli_run
from bkk.index.parallel import discover_parallel_passages
from bkk.index.parallel_scan import discover_parallel_passages_scan


def _write_bundle(
    root: Path,
    textid: str,
    body_text: str,
    *,
    front_text: str = "",
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
    (bundle_dir / f"{textid}.manifest.yaml").write_text(
        yaml.safe_dump({
            "canonical_identifier": f"bkk:test/{textid}/v1",
            "editions": [{"short": "X", "label": "x"}],
            "assets": {
                "parts": [
                    {"seq": 1, "filename": f"{textid}_001.yaml", "hash": "sha256:0"},
                ],
            },
            "table_of_contents": toc,
        }, allow_unicode=True),
        encoding="utf-8",
    )
    return bundle_dir


def _merge(root: Path) -> Path:
    out = root / "_corpus.bkkx"
    merge_bundles(root, out)
    return out


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
    ])

    captured = capsys.readouterr()
    assert rc == 0
    assert "anchors written" in captured.err
    rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["text"] == shared


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


def test_parallel_fuzzy_rejects_max_edits_out_of_range(tmp_path):
    _write_bundle(tmp_path, "KR0a0001", "abcdef")
    out = _merge(tmp_path)

    with pytest.raises(ValueError):
        discover_parallel_passages(out, seed="abc", max_edits=5)
