"""End-to-end KWIC: build an index from a synthetic bundle, then search."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from bkk.index import Index, build_index


def _write_bundle(root: Path, textid: str, body_text: str,
                  variants: list[dict], editions: list[dict]) -> Path:
    bundle_dir = root / textid
    bundle_dir.mkdir(parents=True)
    (bundle_dir / f"{textid}_001.yaml").write_text(
        yaml.safe_dump({
            "canonical_identifier": f"bkk:test/{textid}/v1/juan/1",
            "seq": 1,
            "body": {
                "text": body_text,
                "hash": "sha256:0",
                "markers": [{"type": "variant", **v} for v in variants],
            },
            "hash": "sha256:0",
        }, allow_unicode=True),
        encoding="utf-8",
    )
    (bundle_dir / f"{textid}.manifest.yaml").write_text(
        yaml.safe_dump({
            "canonical_identifier": f"bkk:test/{textid}/v1",
            "editions": editions,
            "assets": {
                "parts": [{"seq": 1, "filename": f"{textid}_001.yaml", "hash": "sha256:0"}],
            },
            "table_of_contents": [
                {
                    "ref": {"seq": 1, "marker_id": "test_001-1a",
                            "span": ["body", 0, len(body_text)]},
                    "label": "Test Juan",
                },
            ],
        }, allow_unicode=True),
        encoding="utf-8",
    )
    return bundle_dir


def test_master_substring_match(tmp_path):
    body = "ABCDEFGHIJ"
    bundle = _write_bundle(tmp_path, "TEST0001", body, [],
                           editions=[{"short": "X", "label": "x"}])
    bkkx = build_index(bundle)
    with Index(bkkx) as ix:
        hits = list(ix.search("CDE", context=2))
    assert len(hits) == 1
    h = hits[0]
    assert h.master_offset == 2
    assert h.master_length == 3
    assert h.matched_via == "master"
    assert (h.left, h.match, h.right) == ("AB", "CDE", "FG")
    assert h.toc_label == "Test Juan"


def test_variant_only_character_finds_master_position(tmp_path):
    # The example from INDEX.md: master '嘗', SBCK variant '甞'.
    body = "專然未嘗不盡天下之議"
    variants = [{"offset": 3, "length": 1, "content": "嘗", "SBCK": "甞"}]
    bundle = _write_bundle(tmp_path, "TEST0002", body, variants,
                           editions=[{"short": "SBCK", "label": "SBCK"}])
    bkkx = build_index(bundle)

    with Index(bkkx) as ix:
        master_hits = list(ix.search("嘗不盡"))
        witness_hits = list(ix.search("甞不盡"))

    assert len(master_hits) == 1
    assert len(witness_hits) == 1

    m, w = master_hits[0], witness_hits[0]
    # Same master offset for both queries — that's the whole point.
    assert m.master_offset == w.master_offset == 3
    assert m.master_length == 3
    assert w.master_length == 3
    assert m.matched_via == "master"
    assert w.matched_via == "SBCK"
    # Both lines render the master text in the match window.
    assert m.match == w.match == "嘗不盡"
    # The witness hit reports what was actually matched in the witness text.
    assert w.matched_text == "甞不盡"
    # Both hits surface the variant overlay so the renderer can flag it.
    overlays_m = [(o.witness, o.witness_form, o.master_offset)
                  for o in m.overlays]
    overlays_w = [(o.witness, o.witness_form, o.master_offset)
                  for o in w.overlays]
    assert ("SBCK", "甞", 3) in overlays_m
    assert ("SBCK", "甞", 3) in overlays_w


def test_witness_filter(tmp_path):
    body = "ABCDE"
    variants = [
        {"offset": 1, "length": 1, "content": "B", "SBCK": "b"},
        {"offset": 3, "length": 1, "content": "D", "WYG": "d"},
    ]
    bundle = _write_bundle(
        tmp_path, "TEST0003", body, variants,
        editions=[{"short": "SBCK", "label": "s"}, {"short": "WYG", "label": "w"}],
    )
    bkkx = build_index(bundle)
    with Index(bkkx) as ix:
        # 'd' appears only in WYG; with witnesses={'SBCK'} it must not match.
        sbck_only = list(ix.search("d", witnesses={"SBCK"}))
        assert sbck_only == []
        wyg_only = list(ix.search("d", witnesses={"WYG"}))
        assert len(wyg_only) == 1
        assert wyg_only[0].matched_via == "WYG"


@pytest.mark.skipif(
    not Path("/home/Shared/bkk/bkbooks/KR1a0024/KR1a0024.manifest.yaml").exists(),
    reason="KR1a0024 fixture not available",
)
def test_real_bundle_kr1a0024(tmp_path):
    # Build into tmp so we don't litter the shared fixture dir.
    src = Path("/home/Shared/bkk/bkbooks/KR1a0024")
    bkkx = tmp_path / "KR1a0024.bkkx"
    build_index(src, bkkx)
    with Index(bkkx) as ix:
        master_hits = list(ix.search("嘗不盡"))
        witness_hits = list(ix.search("甞不盡"))

    # The juan/offset reported in INDEX.md.
    expected = next((h for h in master_hits if h.juan_seq == 1 and h.master_offset == 24307), None)
    assert expected is not None, f"missing master hit at juan 1 offset 24307; got {[(h.juan_seq, h.master_offset) for h in master_hits]}"
    assert expected.match == "嘗不盡"
    # Same hit found by the witness-only query.
    twin = next((h for h in witness_hits if h.juan_seq == 1 and h.master_offset == 24307), None)
    assert twin is not None, "missing witness hit at juan 1 offset 24307"
    assert twin.matched_via == "SBCK"
    assert twin.match == "嘗不盡"
    assert twin.matched_text == "甞不盡"
