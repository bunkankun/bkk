"""Tests for nested ``<div>`` handling and juan-label width normalization
in the TLS reader/exporter.

KR1a0171 surfaced two bugs that the KR6q0053 fixture didn't exercise:

1. Each juan's bulk content is wrapped in nested ``<div>`` chapter blocks.
   The old reader only walked direct ``<head>``/``<p>``/``<pb>`` children of
   the juan div, so >95% of paragraphs were silently dropped.
2. Marker xml:ids encode the juan with two digits (``..._01-...``) where
   the BKK spec uses three. The downstream identifier should be normalized.

These tests pin both invariants on small synthetic inputs that don't depend
on the full corpus.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from lxml import etree

from bkk.importer.ir import Annotation, Marker, Section
from bkk.importer.read.tls import (
    JUAN_LABEL_WIDTH,
    TEI_NS,
    XML_NS,
    _normalize_juan_label_width,
    _normalize_marker_id,
    _section_from_div,
    read_tls,
)


# ---------- _section_from_div: nested div recursion ------------------------


def _div_xml(body: str) -> etree._Element:
    """Parse ``body`` as the inner XML of a single TEI ``<div>`` element and
    return that ``<div>``."""
    src = f"""<div xmlns="{TEI_NS}" xmlns:xml="{XML_NS}">{body}</div>"""
    return etree.fromstring(src)


def test_nested_div_emits_paired_div_markers():
    """Each nested ``<div>`` produces a balanced ``tls:div-start`` /
    ``tls:div-end`` pair around its content, with the id matching the
    nested div's head xml:id."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="T_T_001-d1h1s1">序</seg></head>
        <p><seg xml:id="T_T_001-d1d1p1s1">前言</seg></p>
        <div>
          <head><seg xml:id="T_T_001-d2h1s1">章一</seg></head>
          <p><seg xml:id="T_T_001-d2d1p1s1">內文</seg></p>
        </div>
    """))

    section, juan_entry, _markers_info, nested = _section_from_div(div)

    div_starts = [m for m in section.markers if m.type == "tls:div-start"]
    div_ends = [m for m in section.markers if m.type == "tls:div-end"]
    assert len(div_starts) == 1
    assert len(div_ends) == 1
    assert div_starts[0].id == "T_T_001-d2h1s1"
    assert div_ends[0].id == "T_T_001-d2h1s1"
    # tls:div-start lands before the nested div's head; tls:div-end after
    # its last seg.
    seq = [m.type for m in section.markers]
    ds = seq.index("tls:div-start")
    de = seq.index("tls:div-end")
    assert ds < de
    assert seq[ds + 1] == "tls:head"  # nested head immediately after start
    # The nested div's attrs land in nested_divs_info under its head id.
    assert "T_T_001-d2h1s1" in nested
    # The juan div carries only its own head_attrs/p_attrs (none here, both
    # head and p are bare), and the section.head_text is taken from the
    # outermost head only.
    assert section.head_text == "序"
    assert section.head_marker_id == "T_T_001-d1h1s1"


def test_nested_div_does_not_clobber_section_head():
    """Even though nested divs have their own ``<head>``, only the outermost
    div's head sets ``section.head_text`` / ``section.head_marker_id``."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="OUT">外</seg></head>
        <div>
          <head><seg xml:id="INNER">內</seg></head>
          <p><seg xml:id="P1">x</seg></p>
        </div>
    """))

    section, _juan_entry, _markers_info, _nested = _section_from_div(div)

    assert section.head_marker_id == "OUT"
    assert section.head_text == "外"
    # Both heads emit tls:head markers in document order.
    head_ids = [m.id for m in section.markers if m.type == "tls:head"]
    assert head_ids == ["OUT", "INNER"]


def test_deeply_nested_divs():
    """Three-deep nesting produces three balanced div-start/div-end pairs."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="L0">A</seg></head>
        <div>
          <head><seg xml:id="L1">B</seg></head>
          <div>
            <head><seg xml:id="L2">C</seg></head>
            <p><seg xml:id="P">leaf</seg></p>
          </div>
        </div>
    """))

    section, _juan_entry, _markers_info, nested = _section_from_div(div)

    starts = [m.id for m in section.markers if m.type == "tls:div-start"]
    ends = [m.id for m in section.markers if m.type == "tls:div-end"]
    assert starts == ["L1", "L2"]
    assert ends == ["L2", "L1"]  # closed in reverse order (well-nested)
    assert set(nested.keys()) == {"L1", "L2"}


def test_flat_div_no_div_markers():
    """A juan with no nested divs (KR6q0053 shape) emits no
    ``tls:div-start`` / ``tls:div-end`` markers at all — the diff against
    existing flat-div bundles must stay byte-identical."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="H">頭</seg></head>
        <p><seg xml:id="S1">內文一</seg></p>
        <p><seg xml:id="S2">內文二</seg></p>
    """))

    section, _juan_entry, _markers_info, nested = _section_from_div(div)

    assert nested == {}
    types = {m.type for m in section.markers}
    assert "tls:div-start" not in types
    assert "tls:div-end" not in types


# ---------- juan-label width normalizer ------------------------------------


def test_normalize_marker_id_pads_two_digit_label():
    out = _normalize_marker_id("KR1a0171_tls_01-d1d2d1p1s1", "KR1a0171")
    assert out == "KR1a0171_tls_001-d1d2d1p1s1"


def test_normalize_marker_id_pads_one_digit_label():
    out = _normalize_marker_id("KR1a0171_tls_5-x", "KR1a0171")
    assert out == "KR1a0171_tls_005-x"


def test_normalize_marker_id_noop_on_canonical_input():
    mid = "KR6q0053_T_001-0495a.4-h"
    assert _normalize_marker_id(mid, "KR6q0053") == mid


def test_normalize_marker_id_noop_on_wider_label():
    """Labels already 3+ digits are left alone."""
    assert _normalize_marker_id(
        "KR1a0171_tls_0042-x", "KR1a0171",
    ) == "KR1a0171_tls_0042-x"


def test_normalize_marker_id_skips_non_numeric_labels():
    """Non-numeric labels (rare, but supported by the splitter) are left
    untouched — padding only makes sense for digit-only labels."""
    assert _normalize_marker_id(
        "KR3fa002_tls_alpha-1b", "KR3fa002",
    ) == "KR3fa002_tls_alpha-1b"


def test_normalize_marker_id_skips_other_text_id():
    """The normalizer must not rewrite ids belonging to a different text."""
    assert _normalize_marker_id(
        "OTHER_T_01-x", "KR1a0171",
    ) == "OTHER_T_01-x"


def test_normalize_juan_label_width_mutates_all_marker_carriers():
    """The bulk normalizer rewrites Marker.id, Section.head_marker_id,
    divs_info / markers_info keys, and Annotation.seg_id consistently."""
    text_id = "T1"
    sec = Section(
        head_text="A", head_marker_id="T1_T_01-h",
        text="x", markers=[
            Marker(type="tls:head", offset=0, id="T1_T_01-h"),
            Marker(type="tls:seg", offset=0, id="T1_T_01-s1"),
            Marker(type="tls:div-start", offset=1, id="T1_T_01-d1h"),
        ],
    )
    divs_info = {"T1_T_01-h": {"div_attrs": {"n": "01"}}}
    markers_info = {"T1_T_01-s1": {"type": "tls:seg", "attrs": {}}}
    annotations = [
        Annotation(
            marker_id="T1_T_01-s1", offset=0, length=1, payload={},
            tls_seg_id="T1_T_01-s1", tls_pos=None,
        ),
    ]
    annotations_info = {"a1": {"seg_id": "T1_T_01-s1", "tree": {}}}

    _normalize_juan_label_width(
        [sec], divs_info, markers_info, annotations, annotations_info,
        text_id,
    )

    assert sec.head_marker_id == "T1_T_001-h"
    assert [m.id for m in sec.markers] == [
        "T1_T_001-h", "T1_T_001-s1", "T1_T_001-d1h",
    ]
    assert "T1_T_001-h" in divs_info and "T1_T_01-h" not in divs_info
    assert "T1_T_001-s1" in markers_info
    assert annotations[0].marker_id == "T1_T_001-s1"
    assert annotations_info["a1"]["seg_id"] == "T1_T_001-s1"


# ---------- end-to-end nested-div round-trip --------------------------------


_NESTED_TEXT_XML = textwrap.dedent(f"""\
    <?xml version="1.0" encoding="UTF-8"?>
    <TEI xmlns="{TEI_NS}" xmlns:xml="{XML_NS}" xml:id="KR0test01">
      <teiHeader>
        <fileDesc>
          <titleStmt><title>Test</title></titleStmt>
          <publicationStmt><publisher>x</publisher></publicationStmt>
          <sourceDesc><p>x</p></sourceDesc>
        </fileDesc>
      </teiHeader>
      <text>
        <body>
          <div n="01" type="juan">
            <head><seg xml:id="KR0test01_T_01-d1h1s1">卷一序</seg></head>
            <p><seg xml:id="KR0test01_T_01-d1d1p1s1">前言內容</seg></p>
            <div>
              <head><seg xml:id="KR0test01_T_01-d1d2h1s1">章一</seg></head>
              <p><seg xml:id="KR0test01_T_01-d1d2d1p1s1">章內第一段</seg></p>
              <p><seg xml:id="KR0test01_T_01-d1d2d1p2s1">章內第二段</seg></p>
            </div>
            <div>
              <head><seg xml:id="KR0test01_T_01-d1d3h1s1">章二</seg></head>
              <p><seg xml:id="KR0test01_T_01-d1d3d1p1s1">章二內容</seg></p>
            </div>
          </div>
        </body>
      </text>
    </TEI>
""")


def _write_nested_xml(tmp_path: Path) -> Path:
    p = tmp_path / "KR0test01.xml"
    p.write_text(_NESTED_TEXT_XML, encoding="utf-8")
    return p


def test_read_tls_captures_nested_div_content(tmp_path: Path):
    """All seg text from nested divs makes it into the bundle's juan text —
    not just the prefatory paragraph above the first nested div."""
    bundle = read_tls(_write_nested_xml(tmp_path), None, None, "KR0test01")

    assert len(bundle.juans) == 1
    juan = bundle.juans[0]
    assert juan.seq == 1

    full_text = "".join(sec.text for sec in juan.sections)
    for fragment in (
        "卷一序", "前言內容",
        "章一", "章內第一段", "章內第二段",
        "章二", "章二內容",
    ):
        assert fragment in full_text, f"missing: {fragment}"


def test_read_tls_normalizes_short_juan_labels(tmp_path: Path):
    """The synthetic source uses ``_01-`` ids; after import every marker id
    in the bundle uses the canonical 3-digit form."""
    bundle = read_tls(_write_nested_xml(tmp_path), None, None, "KR0test01")

    for juan in bundle.juans:
        for sec in juan.sections:
            assert "_01-" not in sec.head_marker_id
            for m in sec.markers:
                if m.id:
                    assert "_01-" not in m.id, (
                        f"unnormalized id remained: {m.id}"
                    )
                    if "_T_" in m.id:
                        # The label between _T_ and the first - is exactly 3
                        # digits.
                        label = m.id.split("_T_", 1)[1].split("-", 1)[0]
                        assert label == "001"

    # Sidecar dicts are normalized too.
    info = bundle.source_info or {}
    for d in (info.get("divs", {}), info.get("markers", {})):
        for k in d:
            assert "_01-" not in k


def test_round_trip_nested_div(tmp_path: Path):
    """Bundle → exporter → re-parsed XML preserves the nested-div hierarchy
    (head texts in the right divs, paragraphs split at the right level)."""
    from bkk.exporter.tls import build_text_xml

    bundle = read_tls(_write_nested_xml(tmp_path), None, None, "KR0test01")
    out = build_text_xml(bundle)
    rebuilt = etree.fromstring(out)

    body = rebuilt.find(f".//{{{TEI_NS}}}body")
    juans = body.findall(f"{{{TEI_NS}}}div")
    assert len(juans) == 1

    juan = juans[0]
    # Juan-level head + 1 prefatory <p> + 2 nested <div>.
    direct_heads = juan.findall(f"{{{TEI_NS}}}head")
    direct_p = juan.findall(f"{{{TEI_NS}}}p")
    nested_divs = juan.findall(f"{{{TEI_NS}}}div")
    assert len(direct_heads) == 1
    assert len(direct_p) == 1
    assert len(nested_divs) == 2

    # Each nested div has its own <head> + 1-2 <p>.
    h_chap1 = nested_divs[0].find(f"{{{TEI_NS}}}head/{{{TEI_NS}}}seg")
    h_chap2 = nested_divs[1].find(f"{{{TEI_NS}}}head/{{{TEI_NS}}}seg")
    assert h_chap1.text == "章一"
    assert h_chap2.text == "章二"
    assert len(nested_divs[0].findall(f"{{{TEI_NS}}}p")) == 2
    assert len(nested_divs[1].findall(f"{{{TEI_NS}}}p")) == 1

    # All seg ids carry the canonical 3-digit juan label (the source had _01-).
    for seg in rebuilt.iter(f"{{{TEI_NS}}}seg"):
        sid = seg.get(f"{{{XML_NS}}}id") or ""
        if "_T_" in sid:
            label = sid.split("_T_", 1)[1].split("-", 1)[0]
            assert label == "001", f"id not normalized: {sid}"


def test_juan_label_width_constant_is_three():
    """Sanity: the constant the rest of the pipeline consults matches spec."""
    assert JUAN_LABEL_WIDTH == 3
