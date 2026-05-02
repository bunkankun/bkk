"""End-to-end KRP importer test.

Runs the importer against ``import/input/krp/KR3a0013`` (a real mandoku git
repo) and asserts the spirit-of-the-sample invariants: hashes recompute,
offsets stay in range, every page-break id resolves to an imglist entry,
every PUA codepoint decomposes back to a valid KRnnnn id. Finally diffs the
generated tree against ``import/samples/KR3a0013`` and fails on
``unexpected`` divergences.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bkk.importer.diverge import diff_trees, render_report
from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.pua import PUA_BASE, PUA_END, codepoint_to_kr
from bkk.importer.read.krp import _load_imglist, _parse_juan_text, read_krp
from bkk.importer.recipe import load_recipe
from bkk.importer.write.bundle import write_krp_edition, write_krp_master


REPO = Path(__file__).resolve().parents[1]
TEXT_ID = "KR3a0013"


@pytest.fixture(scope="module")
def out_root(tmp_path_factory) -> Path:
    """Run the importer once for the suite."""
    recipe_path = REPO / "recipes" / f"{TEXT_ID}.yaml"
    if not recipe_path.exists():
        pytest.skip(f"recipe not present at {recipe_path}")
    recipe = load_recipe(recipe_path)
    if not recipe.source.repo.exists():
        pytest.skip(f"krp input repo not present at {recipe.source.repo}")

    documentary, master = read_krp(recipe)
    out_dir = tmp_path_factory.mktemp("bkk-krp-out")
    for bundle in documentary:
        write_krp_edition(bundle, out_dir)
    if master is not None:
        write_krp_master(master, out_dir)
    return out_dir / TEXT_ID


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_juan_text_nonempty(out_root: Path):
    juan = _load(out_root / f"{TEXT_ID}_001.yaml")
    assert juan["body"]["text"]


def test_text_hash_recomputes(out_root: Path):
    juan = _load(out_root / f"{TEXT_ID}_001.yaml")
    for bucket in ("front", "body"):
        if bucket in juan:
            assert juan[bucket]["hash"] == sha256_text(juan[bucket]["text"])


def test_marker_offsets_in_range(out_root: Path):
    for juan_path in sorted(out_root.glob(f"{TEXT_ID}_*.yaml")):
        juan = _load(juan_path)
        for bucket in ("front", "body"):
            if bucket not in juan:
                continue
            text_len = len(juan[bucket]["text"])
            for m in juan[bucket]["markers"]:
                assert 0 <= m["offset"] <= text_len, (
                    f"{juan_path.name}/{bucket}.{m['type']} offset "
                    f"{m['offset']} out of range (text_len={text_len})"
                )


def test_juan_self_hash_recomputes(out_root: Path):
    juan = _load(out_root / f"{TEXT_ID}_001.yaml")
    expected = juan["hash"]
    juan_zeroed = dict(juan)
    juan_zeroed["hash"] = ZERO_HASH
    assert sha256_jcs(juan_zeroed) == expected


def test_manifest_hash_recomputes(out_root: Path):
    manifest = _load(out_root / f"{TEXT_ID}.manifest.yaml")
    assert manifest_hash(manifest) == manifest["hash"]


def test_page_break_ids_resolve_to_imglist(out_root: Path):
    """Every page-break in every juan has a known image attribute."""
    recipe = load_recipe(REPO / "recipes" / f"{TEXT_ID}.yaml")
    seqs: list[int] = []
    for juan_path in sorted(out_root.glob(f"{TEXT_ID}_*.yaml")):
        seqs.append(int(juan_path.stem.rsplit("_", 1)[1]))
    imglist = _load_imglist(
        recipe.source.repo,
        recipe.source.imglist.branch,
        recipe.source.imglist.path,
        TEXT_ID, seqs,
    )
    assert imglist, "imglist should not be empty"
    for juan_path in sorted(out_root.glob(f"{TEXT_ID}_*.yaml")):
        juan = _load(juan_path)
        for bucket in ("front", "body"):
            if bucket not in juan:
                continue
            for m in juan[bucket]["markers"]:
                if m["type"] != "page-break":
                    continue
                short = m["id"].split("_")[-1]
                assert short in imglist, (
                    f"{juan_path.name}/{bucket} page-break {m['id']} "
                    "missing from imglist"
                )


def test_pua_codepoints_round_trip_to_kr(out_root: Path):
    for juan_path in sorted(out_root.glob(f"{TEXT_ID}_*.yaml")):
        juan = _load(juan_path)
        for bucket in ("front", "body"):
            if bucket not in juan:
                continue
            for ch in juan[bucket]["text"]:
                cp = ord(ch)
                if PUA_BASE <= cp < PUA_END:
                    assert codepoint_to_kr(cp) is not None


def test_no_unexpected_divergences(out_root: Path):
    sample = REPO / "samples" / TEXT_ID
    if not sample.exists():
        pytest.skip("sample tree not present")
    divergences = diff_trees(sample, out_root)
    unexpected = [d for d in divergences if d.status == "unexpected"]
    if unexpected:
        report = render_report(divergences)
        pytest.fail(
            f"{len(unexpected)} unexpected divergence(s):\n" + report[:4000]
        )


# ---------- _parse_juan_text unit tests (synthetic mandoku-view) -----------


_MIN_JUAN = (
    "# -*- coding: utf-8 -*-\n"
    "#+TITLE: 試験\n"
    "#+PROPERTY: ID KRT0001\n"
    "#+PROPERTY: JUAN 試験篇\n"
    "<pb:KRT0001_TEST_001-1a>¶"
    "　　第一行は標題なり¶"
    "　第二行に内容あり&KR0008;之終¶"
)


def test_parse_min_juan_basic_shape():
    juan = _parse_juan_text(_MIN_JUAN, juan_seq=1, text_id="KRT0001",
                            imglist={"001-1a": "img/test-1a.png"})
    assert juan.seq == 1
    assert len(juan.sections) == 1
    sec = juan.sections[0]
    assert sec.head_text == "試験篇"
    # PUA expansion: &KR0008; → chr(0x105008)
    assert chr(0x105008) in sec.text


def test_parse_min_juan_markers():
    juan = _parse_juan_text(_MIN_JUAN, juan_seq=1, text_id="KRT0001",
                            imglist={"001-1a": "img/test-1a.png"})
    types = [m.type for m in juan.sections[0].markers]
    # Expect: page-break, line-break, indent, line-break, indent
    assert types[0] == "page-break"
    assert types[1] == "line-break"
    assert "indent" in types
    # The page-break carries an image attribute from the imglist.
    pb = juan.sections[0].markers[0]
    assert pb.extras.get("image") == "img/test-1a.png"


def test_parse_min_juan_offsets_in_range():
    juan = _parse_juan_text(_MIN_JUAN, juan_seq=1, text_id="KRT0001",
                            imglist={})
    sec = juan.sections[0]
    text_len = len(sec.text)
    for m in sec.markers:
        assert 0 <= m.offset <= text_len, (
            f"{m.type} offset {m.offset} out of range (text_len={text_len})"
        )
