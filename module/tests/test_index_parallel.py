"""Parallel-passage discovery over the trigram index."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from bkk.index import build_index, merge_bundles
from bkk.index.cli import run as cli_run
from bkk.index.parallel import discover_parallel_passages


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
