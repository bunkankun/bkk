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
import time

import pytest
import yaml

from bkk.importer.diverge import diff_trees, render_report
from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.classify import split_front_by_opening_indent
from bkk.importer.ir import Bundle
from bkk.importer.pua import PUA_BASE, PUA_END, codepoint_to_kr
import bkk.importer.read.krp as krp_read
from bkk.importer.read.krp import (
    _load_imginfo, _load_imglist, _parse_juan_text, read_krp,
)
from bkk.importer.recipe import load_recipe
from bkk.importer.write.bundle import write_krp_edition, write_krp_master
from bkk.marker_assets import hydrate_juan_markers, load_marker_asset


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


def _load_hydrated(bundle_dir: Path, seq: int) -> dict:
    manifest = _load(bundle_dir / f"{TEXT_ID}.manifest.yaml")
    juan = _load(bundle_dir / f"{TEXT_ID}_{seq:03d}.yaml")
    return hydrate_juan_markers(
        juan, load_marker_asset(bundle_dir, manifest, seq),
    )


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
        seq = int(juan_path.stem.rsplit("_", 1)[1])
        juan = _load_hydrated(out_root, seq)
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
        seq = int(juan_path.stem.rsplit("_", 1)[1])
        juan = _load_hydrated(out_root, seq)
        for bucket in ("front", "body"):
            if bucket not in juan:
                continue
            for m in juan[bucket]["markers"]:
                if m["type"] != "page-break":
                    continue
                parts = m["id"].split("_")
                edition, short = parts[1], parts[-1]
                assert (edition, short) in imglist, (
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
                            imglist={("TEST", "001-1a"): "img/test-1a.png"},
                            edition_short="TEST")
    assert juan.seq == 1
    assert len(juan.sections) == 1
    sec = juan.sections[0]
    assert sec.head_text == "試験篇"
    # PUA expansion: &KR0008; → chr(0x105008)
    assert chr(0x105008) in sec.text


def test_parse_min_juan_markers():
    juan = _parse_juan_text(_MIN_JUAN, juan_seq=1, text_id="KRT0001",
                            imglist={("TEST", "001-1a"): "img/test-1a.png"},
                            edition_short="TEST")
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
                            imglist={}, edition_short="TEST")
    sec = juan.sections[0]
    text_len = len(sec.text)
    for m in sec.markers:
        assert 0 <= m.offset <= text_len, (
            f"{m.type} offset {m.offset} out of range (text_len={text_len})"
        )


_MD_JUAN = (
    "# -*- coding: utf-8 -*-\n"
    "#+TITLE: 試験\n"
    "#+PROPERTY: ID KRT0001\n"
    "#+PROPERTY: JUAN 試験篇\n"
    "<md:KRT0001_OTHER_001-1a>¶"
    "<pb:KRT0001_TEST_001-1a>¶"
    "　　第一行は標題なり¶"
)


def test_md_markers_are_dropped():
    """<md:...> chunks (cross-edition refs) emit no marker and no text."""
    juan = _parse_juan_text(_MD_JUAN, juan_seq=1, text_id="KRT0001",
                            imglist={}, edition_short="TEST")
    sec = juan.sections[0]
    # No marker carries the OTHER edition's id, and the chunk leaves no
    # textual residue.
    assert all("OTHER" not in m.id for m in sec.markers)
    assert "OTHER" not in sec.text
    assert "<md:" not in sec.text
    # The real <pb:> marker that follows still lands.
    pb_ids = [m.id for m in sec.markers if m.type == "page-break"]
    assert pb_ids == ["KRT0001_TEST_001-1a"]


def test_imginfo_uses_remote_urls_not_versions_metadata(monkeypatch):
    """[Versions] may carry BASEEDITION metadata; image URLs live in [Remote]."""
    def fake_git_show(repo: Path, branch: str, path: str) -> str:
        assert branch == "_data"
        assert path == "imglist/imginfo.cfg"
        return (
            "[Versions]\n"
            "BASEEDITION=HFL\n"
            "[Remote]\n"
            "T=http://img.kanripo.org/\n"
            "CBETA=http://example.test/cbeta/\n"
        )

    monkeypatch.setattr(krp_read, "_git_show", fake_git_show)

    assert _load_imginfo(Path("/fake/repo"), "_data") == {
        "T": "http://img.kanripo.org/",
        "CBETA": "http://example.test/cbeta/",
    }


_HEAD_COMMENT_JUAN = (
    "# -*- coding: utf-8 -*-\n"
    "#+TITLE: 試験\n"
    "#+PROPERTY: ID KRT0001\n"
    "#+PROPERTY: JUAN 0\n"
    "<pb:KRT0001_TEST_001-1a>¶\n"
    "** 1 第一章\n"
    "\n"
    "天下皆知美¶\n"
    "\n"
    "# src: synthetic source note\n"
    "# dating: 8120\n"
    "斯惡已¶\n"
)


def test_heading_and_comment_lines_become_markers():
    """`** ...` headings → ``head`` markers; `# ...` comments → ``comment``
    markers. Body text stays free of org metadata."""
    juan = _parse_juan_text(_HEAD_COMMENT_JUAN, juan_seq=1, text_id="KRT0001",
                            imglist={}, edition_short="TEST")
    sec = juan.sections[0]
    # Body is pure CJK content — no `**`, no `#`.
    assert sec.text == "天下皆知美斯惡已"
    assert "**" not in sec.text
    assert "#" not in sec.text
    # head marker carries level + content.
    heads = [m for m in sec.markers if m.type == "head"]
    assert len(heads) == 1
    assert heads[0].extras["level"] == 2
    assert heads[0].content == "1 第一章"
    assert heads[0].id == "KRT0001_TEST_001-h1"
    assert heads[0].offset == 0
    # comment markers preserve the full source line including leading `#`.
    comments = [m for m in sec.markers if m.type == "comment"]
    assert [c.content for c in comments] == [
        "# src: synthetic source note",
        "# dating: 8120",
    ]
    # Both comments sit at the offset between the two content runs.
    boundary = len("天下皆知美")
    assert all(c.offset == boundary for c in comments)
    paragraph_breaks = [m for m in sec.markers if m.type == "paragraph-break"]
    assert [(m.offset, m.content) for m in paragraph_breaks] == [
        (0, "\n\n"),
        (boundary, "\n\n"),
    ]
    source_newlines = [m for m in sec.markers if m.type == "kr:newline"]
    assert source_newlines
    assert all(m.content == "\n" for m in source_newlines)
    # Marker offsets stay monotonic and within text bounds.
    text_len = len(sec.text)
    last = -1
    for m in sec.markers:
        assert 0 <= m.offset <= text_len
        assert m.offset >= last
        last = m.offset


_STAR_OUTLINE_JUAN = (
    "# -*- coding: utf-8 -*-\n"
    "#+TITLE: 試験\n"
    "#+PROPERTY: ID KRT0001\n"
    "#+PROPERTY: JUAN 3\n"
    "試験卷下¶\n"
    "述者名¶\n"
    "  * 說聽\n"
    "  * 躁靜\n"
    "** 說聽¶\n"
    "說法第一段¶\n"
    "** 躁靜¶\n"
    "躁靜第二段¶\n"
)


def test_krp_front_split_prefers_level_two_heading():
    """KRP front matter stops at the first real Mandoku body heading."""
    juan = _parse_juan_text(_STAR_OUTLINE_JUAN, juan_seq=3, text_id="KRT0001",
                            imglist={}, edition_short="TEST")
    juan.sections = split_front_by_opening_indent(juan.sections)

    front, body = juan.sections
    assert front.bucket == "front"
    assert body.bucket == "body"
    assert front.text == "試験卷下述者名說聽躁靜"
    assert [
        (m.type, m.offset, m.content)
        for m in front.markers
        if m.type == "kr:newline"
    ] == [
        ("kr:newline", 4, "\n"),
        ("kr:newline", 7, "\n"),
        ("kr:newline", 9, "\n"),
        ("kr:newline", 11, "\n"),
    ]
    assert body.text == "說法第一段躁靜第二段"
    assert [m.content for m in body.markers if m.type == "head"] == [
        "說聽", "躁靜",
    ]


def test_krp_toc_uses_mandoku_heading_markers(tmp_path: Path):
    juan = _parse_juan_text(_STAR_OUTLINE_JUAN, juan_seq=3, text_id="KRT0001",
                            imglist={}, edition_short="TEST")
    juan.sections = split_front_by_opening_indent(juan.sections)
    bundle = Bundle(
        text_id="KRT0001",
        juans=[juan],
        metadata={"title": "試験"},
        edition_short="krp",
    )

    write_krp_master(bundle, tmp_path)
    manifest = _load(tmp_path / "KRT0001" / "KRT0001.manifest.yaml")
    toc = manifest["table_of_contents"]

    assert [entry["label"] for entry in toc] == ["說聽", "躁靜"]
    assert [entry["ref"]["marker_id"] for entry in toc] == [
        "KRT0001_TEST_003-h1",
        "KRT0001_TEST_003-h2",
    ]
    assert [entry["ref"]["span"] for entry in toc] == [
        ["body", 0, 5],
        ["body", 5, 10],
    ]


_NON_CJK_JUAN = (
    "# -*- coding: utf-8 -*-\n"
    "#+TITLE: 試験\n"
    "#+PROPERTY: ID KRT0001\n"
    "#+PROPERTY: JUAN 試験篇\n"
    "<pb:KRT0001_TEST_001-1a>¶"
    "大目揵連¶"
    "\tMahāmaudgalyāyana.¶"
    "摩訶迦旃延¶"
)


def test_non_cjk_run_becomes_marker():
    """Inline Latin glosses (e.g. Sanskrit transliterations) coalesce into a
    single ``kr:non-cjk`` marker per contiguous run; ASCII whitespace is
    dropped; body text stays CJK+PUA-only."""
    juan = _parse_juan_text(_NON_CJK_JUAN, juan_seq=1, text_id="KRT0001",
                            imglist={}, edition_short="TEST")
    sec = juan.sections[0]
    # No Latin or ASCII period bleeds into body text.
    assert "M" not in sec.text
    assert "." not in sec.text
    assert sec.text == "大目揵連摩訶迦旃延"
    non_cjk = [m for m in sec.markers if m.type == "kr:non-cjk"]
    assert len(non_cjk) == 1
    assert non_cjk[0].content == "Mahāmaudgalyāyana."
    # Marker sits at the boundary between the two CJK runs.
    assert non_cjk[0].offset == len("大目揵連")


def test_body_text_is_cjk_pua_only():
    """The body-text invariant: every char in ``sec.text`` is allowed."""
    from bkk.importer.charset import is_allowed_body_char
    juan = _parse_juan_text(_NON_CJK_JUAN, juan_seq=1, text_id="KRT0001",
                            imglist={}, edition_short="TEST")
    for sec in juan.sections:
        for ch in sec.text:
            assert is_allowed_body_char(ch), (
                f"non-CJK/PUA char {ch!r} (U+{ord(ch):04X}) leaked into body"
            )


_DIRTY_HEAD_JUAN = (
    "# -*- coding: utf-8 -*-\n"
    "#+TITLE: 試験\n"
    "#+PROPERTY: ID KRT0001\n"
    "#+PROPERTY: JUAN 試験篇 [draft]\n"
    "<pb:KRT0001_TEST_001-1a>¶"
    "本文¶"
)


def test_head_text_filtered_to_cjk():
    """Latin / brackets / spaces in JUAN directives are stripped from the
    TOC label, leaving a CJK-only slug."""
    juan = _parse_juan_text(_DIRTY_HEAD_JUAN, juan_seq=1, text_id="KRT0001",
                            imglist={}, edition_short="TEST")
    assert juan.sections[0].head_text == "試験篇"


def test_large_punctuated_juan_parses_in_linear_time():
    """Marker offsets must not rescan all preceding text for each punctuation."""
    repeats = 20_000
    source = ("甲，" * repeats) + "¶"
    started = time.perf_counter()
    juan = _parse_juan_text(
        source,
        juan_seq=1,
        text_id="KRT0001",
        imglist={},
        edition_short="TEST",
    )
    elapsed = time.perf_counter() - started

    section = juan.sections[0]
    punctuation = [m for m in section.markers if m.type == "punctuation"]
    assert len(section.text) == repeats
    assert len(punctuation) == repeats
    assert punctuation[-1].offset == repeats
    assert elapsed < 3.0
