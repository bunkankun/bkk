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


def test_witness_kwic_for_long_replacement(tmp_path):
    """Witness hits whose match lives inside a long variant reading expose
    KWIC drawn from the witness text, so callers can show where in the
    variant reading the match actually sits.
    """
    body = "前文短後文"
    # Witness TKD replaces the 1-char master span '短' with a longer reading
    # whose only occurrence of '方便' is buried in the middle.
    variants = [{"offset": 2, "length": 1, "content": "短", "TKD": "甲乙方便丙丁"}]
    bundle = _write_bundle(
        tmp_path, "TEST_LONG_VARIANT", body, variants,
        editions=[{"short": "TKD", "label": "TKD"}],
    )
    bkkx = build_index(bundle)
    with Index(bkkx) as ix:
        hits = list(ix.search("方便", context=2))

    assert len(hits) == 1
    h = hits[0]
    assert h.matched_via == "TKD"
    # Master anchor: still the 1-char span that the variant replaces.
    assert h.master_offset == 2
    assert h.master_length == 1
    assert h.match == "短"
    # Witness KWIC: drawn from the witness text, with 方便 actually in it.
    # The window extends outward past the variant boundary into the master
    # surroundings (前文/後文) so the witness line shares anchor chars with
    # the master line.
    assert h.matched_text == "方便"
    assert h.witness_left == "前文甲乙"
    assert h.witness_right == "丙丁後文"
    # Variant boundary offsets split anchor (master) from interior (variant).
    assert h.witness_left_variant_offset == 2  # "前文" before, "甲乙" after
    assert h.witness_right_variant_end == 2    # "丙丁" before, "後文" after


def test_witness_kwic_anchor_extends_past_long_variant(tmp_path):
    """When the variant is wider than ``context`` on its own, the witness
    window still reaches a few chars into the surrounding master text so
    the witness line shares anchor chars with the master line.
    """
    body = "前文短後文"
    # Long variant: a 22-char reading replacing the single master char '短',
    # with the query '方便' buried deep in the middle so a context=2 window
    # would otherwise stay entirely inside the variant.
    long_form = "A" * 10 + "方便" + "B" * 10
    variants = [{"offset": 2, "length": 1, "content": "短", "TKD": long_form}]
    bundle = _write_bundle(
        tmp_path, "TEST_LONG_VAR_ANCHOR", body, variants,
        editions=[{"short": "TKD", "label": "TKD"}],
    )
    bkkx = build_index(bundle)
    with Index(bkkx) as ix:
        hits = list(ix.search("方便", context=2))

    assert len(hits) == 1
    h = hits[0]
    assert h.matched_via == "TKD"
    # Witness left starts with '前文' (master anchor) followed by the
    # variant prefix that fits before the match.
    assert h.witness_left.startswith("前文")
    assert h.witness_left.endswith("A" * 10)
    # Witness right ends with '後文' (master anchor) preceded by the
    # variant suffix.
    assert h.witness_right.startswith("B" * 10)
    assert h.witness_right.endswith("後文")
    # The variant boundary offset splits the master anchor (前文/後文) from
    # the variant interior (A…/B…) so the frontend can collapse the interior.
    assert h.witness_left[:h.witness_left_variant_offset] == "前文"
    assert h.witness_left[h.witness_left_variant_offset:] == "A" * 10
    assert h.witness_right[:h.witness_right_variant_end] == "B" * 10
    assert h.witness_right[h.witness_right_variant_end:] == "後文"


def test_witness_kwic_empty_for_master_hit(tmp_path):
    """Master-text hits have no witness KWIC."""
    body = "ABCDEFGHIJ"
    bundle = _write_bundle(tmp_path, "TEST_MASTER_KWIC", body, [],
                           editions=[{"short": "X", "label": "x"}])
    bkkx = build_index(bundle)
    with Index(bkkx) as ix:
        hits = list(ix.search("CDE", context=2))
    assert len(hits) == 1
    h = hits[0]
    assert h.matched_via == "master"
    assert h.witness_left == ""
    assert h.witness_right == ""


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
