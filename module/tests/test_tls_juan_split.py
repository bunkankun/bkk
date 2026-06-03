"""Tests for the TLS juan-splitting pipeline.

Covers the two helpers (``_juan_label_from_marker_id``,
``_split_sections_into_juans``) in :mod:`bkk.importer.read.tls`, plus a
smoke test that drives an in-memory multi-juan ``Bundle`` through
``write_bundle`` and asserts the file layout and annotation partitioning.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from lxml import etree

from bkk.importer.hashing import ZERO_HASH, sha256_jcs
from bkk.importer.ir import Annotation, Bundle, Juan, Marker, Section
from bkk.importer.read.tls import (
    TEI_NS,
    XML_NS,
    _juan_label_from_marker_id,
    _section_from_div,
    _split_sections_into_juans,
)
from bkk.importer.write.bundle import write_bundle


# ---------- _juan_label_from_marker_id -------------------------------------


@pytest.mark.parametrize("mid, text_id, expected", [
    ("KR6q0053_T_001-0495a.4-h", "KR6q0053", "001"),
    ("KR1f0001_tls_002-1a.3-h", "KR1f0001", "002"),
    # Wrong text id prefix.
    ("OTHER_T_001-x", "KR6q0053", None),
    # Too few components (no edition_location separator).
    ("KR6q0053_T", "KR6q0053", None),
    # Empty string.
    ("", "KR6q0053", None),
    # Location lacks a hyphen — the whole location is the label.
    ("KR6q0053_T_xyz", "KR6q0053", "xyz"),
])
def test_juan_label_extraction(mid, text_id, expected):
    assert _juan_label_from_marker_id(mid, text_id) == expected


# ---------- _split_sections_into_juans -------------------------------------


def _seg(text_id: str, location: str, offset: int, edition: str = "T") -> Marker:
    return Marker(
        type="tls:seg",
        offset=offset,
        content="",
        id=f"{text_id}_{edition}_{location}",
    )


def _pb(text_id: str, location: str, offset: int, edition: str = "T") -> Marker:
    return Marker(
        type="page-break",
        offset=offset,
        content="",
        id=f"{text_id}_{edition}_{location}",
    )


def _section(head: str, head_id: str, text: str, markers: list[Marker]) -> Section:
    return Section(
        head_text=head,
        head_marker_id=head_id,
        text=text,
        markers=markers,
    )


def test_single_juan_passes_through():
    text_id = "KR6q0053"
    sections = [
        _section(
            "序", "KR6q0053_T_001-0495a.4-h", "abcdef",
            [
                _seg(text_id, "001-0495a.4", 0),
                _seg(text_id, "001-0495a.5", 3),
            ],
        ),
        _section(
            "正文", "KR6q0053_T_001-0496a.1-h", "ghijkl",
            [_seg(text_id, "001-0496a.1", 0)],
        ),
    ]

    groups = _split_sections_into_juans(sections, text_id)

    assert len(groups) == 1
    label, secs = groups[0]
    assert label == "001"
    # Sections returned unchanged (same objects), order preserved.
    assert secs == sections


def test_two_juan_boundary_between_sections():
    text_id = "KR6q0053"
    sec_a = _section(
        "序", "KR6q0053_T_001-0495a.4-h", "abcdef",
        [_seg(text_id, "001-0495a.4", 0)],
    )
    sec_b = _section(
        "卷二", "KR6q0053_T_002-0500a.1-h", "ghijkl",
        [_seg(text_id, "002-0500a.1", 0)],
    )

    groups = _split_sections_into_juans([sec_a, sec_b], text_id)

    assert [lbl for lbl, _ in groups] == ["001", "002"]
    assert groups[0][1] == [sec_a]
    assert groups[1][1] == [sec_b]


def test_mid_section_juan_boundary_splits_section():
    text_id = "KR6q0053"
    sec = _section(
        "卷一", "KR6q0053_T_001-0495a.1-h", "abcdefghij",
        [
            _seg(text_id, "001-0495a.1", 0),
            _seg(text_id, "001-0495a.2", 3),
            # Page break opening juan 002 in the middle of the section.
            _pb(text_id, "002-0500a", 6),
            _seg(text_id, "002-0500a.1", 6),
        ],
    )

    groups = _split_sections_into_juans([sec], text_id)

    assert [lbl for lbl, _ in groups] == ["001", "002"]
    front_secs = groups[0][1]
    back_secs = groups[1][1]
    assert len(front_secs) == 1
    assert len(back_secs) == 1

    front, back = front_secs[0], back_secs[0]
    # Text is sliced at the boundary offset.
    assert front.text == "abcdef"
    assert back.text == "ghij"
    # Both halves carry the original head metadata (split semantics).
    assert front.head_text == "卷一"
    assert back.head_text == "卷一"
    # Markers are partitioned and the back marker offsets are re-based.
    front_marker_ids = [m.id for m in front.markers]
    back_marker_ids = [m.id for m in back.markers]
    assert front_marker_ids == [
        f"{text_id}_T_001-0495a.1",
        f"{text_id}_T_001-0495a.2",
    ]
    assert back_marker_ids == [
        f"{text_id}_T_002-0500a",
        f"{text_id}_T_002-0500a.1",
    ]
    assert all(0 <= m.offset <= len(back.text) for m in back.markers)
    assert back.markers[0].offset == 0  # was 6, rebased


def test_section_without_id_bearing_markers_inherits_label():
    text_id = "KR6q0053"
    sec_a = _section(
        "卷一", "KR6q0053_T_001-0495a.1-h", "abc",
        [_seg(text_id, "001-0495a.1", 0)],
    )
    # Sec_b has only a paragraph-break marker — no id-bearing marker.
    sec_b = _section(
        "", "", "def",
        [Marker(type="paragraph-break", offset=0, content="", id="")],
    )

    groups = _split_sections_into_juans([sec_a, sec_b], text_id)

    assert len(groups) == 1
    assert groups[0][0] == "001"
    assert groups[0][1] == [sec_a, sec_b]


def test_first_section_without_markers_defaults_to_001():
    text_id = "KR6q0053"
    sec = _section(
        "", "", "abc",
        [Marker(type="paragraph-break", offset=0, content="", id="")],
    )

    groups = _split_sections_into_juans([sec], text_id)

    assert groups == [("001", [sec])]


def test_juan_detection_locked_to_base_edition():
    """Markers from non-base editions don't trigger juan boundaries.

    The base edition is the edition of the first ``tls:seg`` marker. Here
    juan 002 is mentioned only by a variant-edition page-break (``K``)
    interleaved in the section; the base-edition (``T``) markers all stay
    in juan 001, so the section must not split.
    """
    text_id = "KR6q0053"
    sec = _section(
        "卷一", "KR6q0053_T_001-0495a.1-h", "abcdefghij",
        [
            _seg(text_id, "001-0495a.1", 0, edition="T"),
            _seg(text_id, "001-0495a.2", 3, edition="T"),
            # Variant-edition page-break with a different juan label —
            # must be ignored.
            _pb(text_id, "002-0500a", 5, edition="K"),
            _seg(text_id, "001-0495a.3", 6, edition="T"),
        ],
    )

    groups = _split_sections_into_juans([sec], text_id)

    assert len(groups) == 1
    assert groups[0][0] == "001"
    assert groups[0][1] == [sec]


def test_base_edition_only_tls_seg_pb_doesnt_pin_edition():
    """If the first marker is a page-break of edition X but the first seg is
    edition Y, the base edition is Y — pb-X markers no longer drive splits."""
    text_id = "KR6q0053"
    sec = _section(
        "卷一", "KR6q0053_T_001-0495a.1-h", "abcdef",
        [
            # Page break in some auxiliary edition appears first.
            _pb(text_id, "001-0001a", 0, edition="aux"),
            # First seg is in T — that's the base edition.
            _seg(text_id, "001-0495a.1", 0, edition="T"),
            # An aux-edition pb that names a different juan must not split.
            _pb(text_id, "999-0000a", 3, edition="aux"),
            _seg(text_id, "001-0495a.2", 3, edition="T"),
        ],
    )

    groups = _split_sections_into_juans([sec], text_id)

    assert [lbl for lbl, _ in groups] == ["001"]


def test_non_numeric_label_round_trips_through_build_juans():
    """Non-numeric juan labels fall back to enumeration for ``Juan.seq``."""
    from bkk.importer.read.tls import _build_juans

    text_id = "KR6q0053"
    sec_a = _section(
        "A", "KR6q0053_T_alpha-1-h", "abc",
        [_seg(text_id, "alpha-1", 0)],
    )
    sec_b = _section(
        "B", "KR6q0053_T_beta-1-h", "def",
        [_seg(text_id, "beta-1", 0)],
    )

    juans = _build_juans([sec_a, sec_b], [], text_id)

    assert [j.seq for j in juans] == [1, 2]
    assert juans[0].sections == [sec_a]
    assert juans[1].sections == [sec_b]


# ---------- end-to-end multi-juan write_bundle smoke test ------------------


def _build_multi_juan_bundle(text_id: str = "KR0test01") -> Bundle:
    """Two-juan bundle with one annotation per juan."""
    sec1 = _section(
        "卷一", f"{text_id}_T_001-0001a.1-h", "甲乙丙丁",
        [
            _seg(text_id, "001-0001a.1", 0),
            _seg(text_id, "001-0001a.2", 2),
        ],
    )
    sec2 = _section(
        "卷二", f"{text_id}_T_002-0002a.1-h", "戊己庚辛",
        [
            _seg(text_id, "002-0002a.1", 0),
            _seg(text_id, "002-0002a.2", 2),
        ],
    )

    ann1 = Annotation(
        marker_id=f"{text_id}_T_001-0001a.2",
        offset=0,
        length=1,
        payload={
            "id": "ann-juan1",
            "concept": "X",
            "form": {"orth": "乙"},
            "sense": {"syn_func": "noun"},
        },
        provenance="swl",
        tls_seg_id=f"{text_id}_T_001-0001a.2",
        tls_pos=1,
    )
    ann2 = Annotation(
        marker_id=f"{text_id}_T_002-0002a.1",
        offset=0,
        length=1,
        payload={
            "id": "ann-juan2",
            "concept": "Y",
            "form": {"orth": "戊"},
            "sense": {"syn_func": "verb"},
        },
        provenance="swl",
        tls_seg_id=f"{text_id}_T_002-0002a.1",
        tls_pos=1,
    )

    juans = [
        Juan(seq=1, sections=[sec1], annotations=[ann1]),
        Juan(seq=2, sections=[sec2], annotations=[ann2]),
    ]
    return Bundle(
        text_id=text_id,
        juans=juans,
        metadata={"title": "Test", "source": {"repository": "synthetic"}},
        edition_short="T",
    )


def test_write_bundle_emits_per_juan_files(tmp_path: Path):
    bundle = _build_multi_juan_bundle()
    archive_root = tmp_path / "bkk-annotations"
    write_bundle(bundle, tmp_path, annotations_root=archive_root)

    root = tmp_path / bundle.text_id
    juan1 = root / f"{bundle.text_id}_001.yaml"
    juan2 = root / f"{bundle.text_id}_002.yaml"
    ann1 = archive_root / bundle.text_id / f"{bundle.text_id}_001.ann.jsonl"
    ann2 = archive_root / bundle.text_id / f"{bundle.text_id}_002.ann.jsonl"
    manifest = root / f"{bundle.text_id}.manifest.yaml"

    assert juan1.exists()
    assert juan2.exists()
    assert ann1.exists()
    assert ann2.exists()
    assert manifest.exists()

    # Manifest no longer references annotations.
    mf = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    assert "annotations" not in mf.get("assets", {})
    parts = mf["assets"]["parts"]
    assert [p["seq"] for p in parts] == [1, 2]
    assert [p["filename"] for p in parts] == [
        f"{bundle.text_id}_001.yaml",
        f"{bundle.text_id}_002.yaml",
    ]
    for part in parts:
        loaded = yaml.safe_load((root / part["filename"]).read_text(encoding="utf-8"))
        zeroed = dict(loaded)
        zeroed["hash"] = ZERO_HASH
        assert sha256_jcs(zeroed) == part["hash"]

    # Archive JSONL files are partitioned: each juan has only its own annotation.
    a1_ids = {
        json.loads(line)["id"]
        for line in ann1.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    a2_ids = {
        json.loads(line)["id"]
        for line in ann2.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert a1_ids == {"ann-juan1"}
    assert a2_ids == {"ann-juan2"}


# ---------- TOC label CJK-only invariant -----------------------------------


def _build_div_xml(seg_text: str) -> etree._Element:
    """Build a TEI ``<div><head><seg>...</seg></head></div>`` element."""
    nsmap = {None: TEI_NS}
    div = etree.Element(f"{{{TEI_NS}}}div", nsmap=nsmap)
    head = etree.SubElement(div, f"{{{TEI_NS}}}head")
    head.set(f"{{{XML_NS}}}id", "KRtest_T_001-0001a.1-h-h")
    seg = etree.SubElement(head, f"{{{TEI_NS}}}seg")
    seg.set(f"{{{XML_NS}}}id", "KRtest_T_001-0001a.1-h")
    seg.text = seg_text
    return div


def test_section_head_text_strips_non_cjk():
    """Whitespace, ASCII, punctuation in a head's <seg> are stripped from
    ``head_text`` so the TOC label is CJK-only. The body text (which feeds
    into the section.text stream) is left untouched at this layer."""
    div = _build_div_xml("  臨濟 abc，慧照\n禪師  ")
    section, _div_entry, _markers_info, _nested = _section_from_div(div)
    assert section.head_text == "臨濟慧照禪師"


def test_section_head_text_pure_cjk_unchanged():
    div = _build_div_xml("勘辨")
    section, _div_entry, _markers_info, _nested = _section_from_div(div)
    assert section.head_text == "勘辨"
