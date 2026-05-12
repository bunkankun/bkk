"""Voice-aware search: voice_range table, strict-containment filter, hit tags."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

from bkk.index import Index, build_index, merge_bundles


def _write_bundle(
    root: Path, textid: str, body_text: str,
    *,
    voices: list[dict] | None = None,
    variants: list[dict] | None = None,
    editions: list[dict] | None = None,
) -> Path:
    bundle_dir = root / textid
    bundle_dir.mkdir(parents=True)
    markers: list[dict] = []
    for v in voices or []:
        markers.append({"type": "voice", **v})
    for v in variants or []:
        markers.append({"type": "variant", **v})
    (bundle_dir / f"{textid}_001.yaml").write_text(
        yaml.safe_dump({
            "canonical_identifier": f"bkk:test/{textid}/v1/juan/1",
            "seq": 1,
            "body": {
                "text": body_text,
                "hash": "sha256:0",
                "markers": markers,
            },
            "hash": "sha256:0",
        }, allow_unicode=True),
        encoding="utf-8",
    )
    (bundle_dir / f"{textid}.manifest.yaml").write_text(
        yaml.safe_dump({
            "canonical_identifier": f"bkk:test/{textid}/v1",
            "editions": editions or [{"short": "X", "label": "x"}],
            "assets": {"parts": [
                {"seq": 1, "filename": f"{textid}_001.yaml", "hash": "sha256:0"},
            ]},
            "table_of_contents": [
                {"ref": {"seq": 1, "marker_id": f"{textid}_001-1a",
                         "span": ["body", 0, len(body_text)]},
                 "label": f"{textid} juan"},
            ],
        }, allow_unicode=True),
        encoding="utf-8",
    )
    return bundle_dir


# body layout, 20 chars:
#   0         1
#   0123456789012345678901
#   AAAAACCCCCBBBBBDDDDD
#   |root1||cmt1||root2||cmt2|
#
#    root1   : [0, 5)   "AAAAA"
#    cmt1    : [5, 10)  "CCCCC"   responds-to r1
#    root2   : [10, 15) "BBBBB"
#    cmt2    : [15, 20) "DDDDD"   responds-to r2
ROOT_CMT_BODY = "AAAAACCCCCBBBBBDDDDD"
ROOT_CMT_VOICES = [
    {"offset": 0,  "length": 5, "name": "root",       "id": "r1"},
    {"offset": 5,  "length": 5, "name": "commentary", "id": "c1", "responds-to": "r1"},
    {"offset": 10, "length": 5, "name": "root",       "id": "r2"},
    {"offset": 15, "length": 5, "name": "commentary", "id": "c2", "responds-to": "r2"},
]


def test_voice_range_table_populated(tmp_path):
    bundle = _write_bundle(tmp_path, "KRV0001", ROOT_CMT_BODY,
                           voices=ROOT_CMT_VOICES)
    bkkx = build_index(bundle)

    conn = sqlite3.connect(str(bkkx))
    try:
        rows = conn.execute(
            "SELECT master_offset, length, name, voice_id, responds_to "
            "FROM voice_range ORDER BY master_offset"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [
        (0,  5, "root",       "r1", None),
        (5,  5, "commentary", "c1", "r1"),
        (10, 5, "root",       "r2", None),
        (15, 5, "commentary", "c2", "r2"),
    ]


def test_available_voices(tmp_path):
    bundle = _write_bundle(tmp_path, "KRV0002", ROOT_CMT_BODY,
                           voices=ROOT_CMT_VOICES)
    bkkx = build_index(bundle)
    with Index(bkkx) as ix:
        assert ix.available_voices() == ["commentary", "root"]


def test_root_only_hit_classified_and_filtered(tmp_path):
    bundle = _write_bundle(tmp_path, "KRV0003", ROOT_CMT_BODY,
                           voices=ROOT_CMT_VOICES)
    bkkx = build_index(bundle)
    with Index(bkkx) as ix:
        # 'AAAAA' is exactly root1 [0, 5).
        all_hits = list(ix.search("AAAAA"))
        root_hits = list(ix.search("AAAAA", voices={"root"}))
        cmt_hits = list(ix.search("AAAAA", voices={"commentary"}))
    assert len(all_hits) == 1
    h = all_hits[0]
    assert h.voice == "root"
    assert h.voice_stack == ("root",)
    assert len(root_hits) == 1
    assert root_hits[0].voice == "root"
    assert cmt_hits == []


def test_commentary_only_hit_classified_and_filtered(tmp_path):
    bundle = _write_bundle(tmp_path, "KRV0004", ROOT_CMT_BODY,
                           voices=ROOT_CMT_VOICES)
    bkkx = build_index(bundle)
    with Index(bkkx) as ix:
        # 'CCCCC' is exactly cmt1 [5, 10).
        all_hits = list(ix.search("CCCCC"))
        cmt_hits = list(ix.search("CCCCC", voices={"commentary"}))
        root_hits = list(ix.search("CCCCC", voices={"root"}))
    assert len(all_hits) == 1
    assert all_hits[0].voice == "commentary"
    assert all_hits[0].voice_stack == ("commentary",)
    assert len(cmt_hits) == 1
    assert root_hits == []


def test_cross_boundary_hit_is_mixed_and_filtered_out(tmp_path):
    bundle = _write_bundle(tmp_path, "KRV0005", ROOT_CMT_BODY,
                           voices=ROOT_CMT_VOICES)
    bkkx = build_index(bundle)
    with Index(bkkx) as ix:
        # 'AAACC' starts in root1 at offset 2 and ends in cmt1 at offset 7 —
        # straddles the [0,5)→[5,10) boundary, contained in neither range.
        all_hits = list(ix.search("AAACC"))
        root_hits = list(ix.search("AAACC", voices={"root"}))
        cmt_hits = list(ix.search("AAACC", voices={"commentary"}))
    assert len(all_hits) == 1
    assert all_hits[0].voice == "mixed"
    assert all_hits[0].voice_stack == ()
    assert root_hits == []
    assert cmt_hits == []


def test_count_invariant_no_nesting(tmp_path):
    """With no nested voices: sum of per-voice + mixed + none == all."""
    bundle = _write_bundle(tmp_path, "KRV0006", ROOT_CMT_BODY,
                           voices=ROOT_CMT_VOICES)
    bkkx = build_index(bundle)
    with Index(bkkx) as ix:
        # 'A' matches root1; 'C' matches cmt1; 'AC' is cross-boundary.
        for query in ("A", "C", "AC", "AAAAACCCCC"):
            all_hits = list(ix.search(query))
            n_root = sum(1 for h in all_hits if h.voice == "root")
            n_cmt  = sum(1 for h in all_hits if h.voice == "commentary")
            n_mix  = sum(1 for h in all_hits if h.voice == "mixed")
            n_none = sum(1 for h in all_hits if h.voice == "none")
            assert n_root + n_cmt + n_mix + n_none == len(all_hits), query


def test_unmarked_text_is_none(tmp_path):
    # No voice markers at all: every hit lands in 'none'.
    bundle = _write_bundle(tmp_path, "KRV0007", "ABCDEFG")
    bkkx = build_index(bundle)
    with Index(bkkx) as ix:
        hits = list(ix.search("CDE"))
    assert len(hits) == 1
    assert hits[0].voice == "none"
    assert hits[0].voice_stack == ()


def test_witness_hit_classified_via_master(tmp_path):
    """Variant inside commentary: witness hit projects to master span,
    which lives inside the commentary range, so it gets voice=commentary."""
    bundle = _write_bundle(
        tmp_path, "KRV0008", ROOT_CMT_BODY,
        voices=ROOT_CMT_VOICES,
        # CCCCC at [5, 10) is inside commentary; swap one char in SBCK.
        variants=[{"offset": 6, "length": 1, "content": "C", "SBCK": "x"}],
        editions=[{"short": "SBCK", "label": "SBCK"}],
    )
    bkkx = build_index(bundle)
    with Index(bkkx) as ix:
        # 'CxC' only exists in SBCK; master reads 'CCC' at this offset.
        witness_hits = list(ix.search("CxC"))
    assert len(witness_hits) == 1
    h = witness_hits[0]
    assert h.matched_via == "SBCK"
    assert h.master_offset == 5
    assert h.voice == "commentary"


# Nesting fixture: a sound gloss inside a commentary.
#   0         1
#   0123456789012345
#   AAAAACCGGGGGCCCAA
#   |root1|--cmt1---||root2|
#           |gloss|
#
#  root1: [0, 5)
#  cmt1:  [5, 15)   spans positions 5..14
#  gloss: [7, 12)   nested inside cmt1
#  root2: [15, 17)
NEST_BODY = "AAAAACCGGGGGCCCAA"
NEST_VOICES = [
    {"offset": 0,  "length": 5,  "name": "root",        "id": "r1"},
    {"offset": 5,  "length": 10, "name": "commentary",  "id": "c1", "responds-to": "r1"},
    {"offset": 7,  "length": 5,  "name": "sound-gloss", "id": "g1", "responds-to": "c1"},
    {"offset": 15, "length": 2,  "name": "root",        "id": "r2"},
]


def test_nesting_innermost_wins(tmp_path):
    bundle = _write_bundle(tmp_path, "KRV0009", NEST_BODY,
                           voices=NEST_VOICES)
    bkkx = build_index(bundle)
    with Index(bkkx) as ix:
        # 'GGGGG' is exactly the gloss [7, 12), which is inside cmt1.
        hits = list(ix.search("GGGGG"))
        cmt_hits = list(ix.search("GGGGG", voices={"commentary"}))
        gloss_hits = list(ix.search("GGGGG", voices={"sound-gloss"}))
        root_hits = list(ix.search("GGGGG", voices={"root"}))
    assert len(hits) == 1
    h = hits[0]
    assert h.voice == "sound-gloss"
    assert h.voice_stack == ("commentary", "sound-gloss")
    # Qualifies under both names per nested-filter semantics.
    assert len(cmt_hits) == 1
    assert len(gloss_hits) == 1
    assert root_hits == []


def test_voice_filter_rejects_unknown_voice_via_cli(tmp_path, capsys):
    from bkk.index.cli import run as cli_run
    bundle = _write_bundle(tmp_path, "KRV0010", ROOT_CMT_BODY,
                           voices=ROOT_CMT_VOICES)
    bkkx = build_index(bundle)
    with pytest.raises(SystemExit):
        cli_run(["search", str(bkkx), "AAA", "--voice", "nonsense"])
    err = capsys.readouterr().err
    assert "unknown voice name" in err
    assert "nonsense" in err


def test_build_rejects_overlapping_same_name_voices(tmp_path):
    overlapping = [
        {"offset": 0, "length": 6, "name": "root", "id": "r1"},
        {"offset": 4, "length": 5, "name": "root", "id": "r2"},
    ]
    bundle = _write_bundle(tmp_path, "KRV0011", "ABCDEFGHI",
                           voices=overlapping)
    with pytest.raises(ValueError, match="overlapping voice ranges"):
        build_index(bundle)


def test_build_rejects_voice_out_of_range(tmp_path):
    bad = [{"offset": 0, "length": 99, "name": "root", "id": "r1"}]
    bundle = _write_bundle(tmp_path, "KRV0012", "ABCDE", voices=bad)
    with pytest.raises(ValueError, match="out of range"):
        build_index(bundle)


def test_build_rejects_dangling_responds_to(tmp_path):
    bad = [
        {"offset": 0, "length": 5, "name": "root", "id": "r1"},
        {"offset": 5, "length": 5, "name": "commentary", "id": "c1",
         "responds-to": "r9"},
    ]
    bundle = _write_bundle(tmp_path, "KRV0013", ROOT_CMT_BODY[:10],
                           voices=bad)
    with pytest.raises(ValueError, match="responds-to"):
        build_index(bundle)


def test_merge_propagates_voice_ranges(tmp_path):
    _write_bundle(tmp_path, "KRV0014", ROOT_CMT_BODY,
                  voices=ROOT_CMT_VOICES)
    _write_bundle(tmp_path, "KRV0015", NEST_BODY,
                  voices=NEST_VOICES)
    out = tmp_path / "corpus.bkkx"
    merge_bundles(tmp_path, out)

    conn = sqlite3.connect(str(out))
    try:
        total = conn.execute("SELECT COUNT(*) FROM voice_range").fetchone()[0]
    finally:
        conn.close()
    # 4 ranges from bundle 1 + 4 from bundle 2.
    assert total == 8

    with Index(out) as ix:
        # Voice classification still works post-merge.
        ggg_hits = list(ix.search("GGGGG"))
        assert len(ggg_hits) == 1
        assert ggg_hits[0].voice == "sound-gloss"
        assert ggg_hits[0].voice_stack == ("commentary", "sound-gloss")
        # Both bundles have a root segment matching 'AAAAA'.
        aaaaa_hits = list(ix.search("AAAAA"))
        assert {h.textid for h in aaaaa_hits} == {"KRV0014", "KRV0015"}
        assert all(h.voice == "root" for h in aaaaa_hits)


def test_index_rejects_old_schema(tmp_path):
    """Opening a pre-voice (v2) index file errors out with a rebuild hint."""
    bkkx = tmp_path / "stale.bkkx"
    conn = sqlite3.connect(str(bkkx))
    try:
        # Minimal v2-like meta — no voice_range table, wrong schema version.
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '2')")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(ValueError, match="schema version 2"):
        Index(bkkx)
