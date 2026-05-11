"""Tests covering the five "information-loss" shapes the TLS importer must
preserve from source TEI.

The shapes (matching the design plan):

1. xml:id capture on every id-bearing element (div, p, note, head, seg).
2. Nested ``<head>`` → TOC entry at the right level (verified at the
   marker-extras level; the TOC builder picks them up).
3. Inline ``<note place="inline">`` brackets via ``tls:note-start`` /
   ``tls:note-end`` markers (paren content; text doesn't leak to head
   labels).
4. ``<seg type=T>`` run-folding: consecutive same-type segs collapse into
   a single ``tls:seg-start`` / ``tls:seg-end`` range.
5. ``body.text`` invariant: only CJK ideographs and BKK PUA codepoints —
   ASCII/whitespace/full-width-space residue moves to typed markers.

Each test uses a synthetic ``<div>`` so it pins one invariant without
depending on the full corpus.
"""

from __future__ import annotations

import textwrap

from lxml import etree

from bkk.importer.charset import is_allowed_body_char
from bkk.importer.read.tls import (
    TEI_NS,
    XML_NS,
    _section_from_div,
)


def _div_xml(body: str) -> etree._Element:
    src = f"""<div xmlns="{TEI_NS}" xmlns:xml="{XML_NS}">{body}</div>"""
    return etree.fromstring(src)


def _div_xml_recover(body: str) -> etree._Element:
    """Variant of ``_div_xml`` that uses a recovery-mode parser so duplicate
    xml:ids (and other source defects) survive into the importer's
    ``_dedup_id`` machinery instead of being rejected at parse time."""
    parser = etree.XMLParser(recover=True)
    src = f"""<div xmlns="{TEI_NS}" xmlns:xml="{XML_NS}">{body}</div>"""
    return etree.fromstring(src, parser)


def _markers(div) -> list:
    section, _, _, _ = _section_from_div(div)
    return section.markers


# ---------- shape 1: xml:id capture ----------------------------------------


def test_div_xml_id_lands_on_div_start():
    """A nested ``<div xml:id=X>`` emits ``tls:div-start`` with ``id=X``,
    preferring the div's own id over the head id fallback."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="OUT">外</seg></head>
        <div xml:id="DIV2">
          <head><seg xml:id="H2">內</seg></head>
          <p><seg xml:id="S">x</seg></p>
        </div>
    """))
    starts = [m for m in _markers(div) if m.type == "tls:div-start"]
    assert [m.id for m in starts] == ["DIV2"]


def test_p_xml_id_lands_on_paragraph_break():
    """``<p xml:id=P>`` becomes ``paragraph-break`` markers carrying the
    id (open) and ``id_end`` (close), with explicit ``role`` extras."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="H">頭</seg></head>
        <p xml:id="P1"><seg xml:id="S1">內文</seg></p>
    """))
    breaks = [m for m in _markers(div) if m.type == "paragraph-break"]
    assert len(breaks) == 2
    assert breaks[0].id == "P1"
    assert breaks[0].extras.get("role") == "open"
    assert breaks[1].id == "P1_end"
    assert breaks[1].extras.get("role") == "close"


def test_dup_xml_id_on_p_routes_through_dedup():
    """Repeated ``<p xml:id>`` values get ``_dup{n}`` suffixes and an
    ``_xml_error`` flag so the YAML records the source defect."""
    div = _div_xml_recover(textwrap.dedent("""\
        <head><seg xml:id="H">頭</seg></head>
        <p xml:id="P"><seg xml:id="S1">a</seg></p>
        <p xml:id="P"><seg xml:id="S2">b</seg></p>
    """))
    breaks = [m for m in _markers(div) if m.type == "paragraph-break"]
    # First p: open=P, close=P_end. Second p: open=P_dup{n} with _xml_error.
    open_breaks = [m for m in breaks if m.extras.get("role") == "open"]
    assert open_breaks[0].id == "P"
    assert open_breaks[1].id.startswith("P_dup")
    assert open_breaks[1].extras.get("_xml_error") == "duplicate-id"


# ---------- shape 3: inline-note brackets -----------------------------------


def test_inline_note_emits_bracket_markers():
    """An inline ``<note>`` inside a seg emits matching ``tls:note-start``
    (content ``(``) and ``tls:note-end`` (content ``)``) markers."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="H">頭</seg></head>
        <p><seg xml:id="S">前<note place="inline" xml:id="N1">注</note>後</seg></p>
    """))
    markers = _markers(div)
    starts = [m for m in markers if m.type == "tls:note-start"]
    ends = [m for m in markers if m.type == "tls:note-end"]
    assert len(starts) == 1 and len(ends) == 1
    assert starts[0].content == "("
    assert ends[0].content == ")"
    assert starts[0].id == "N1"
    assert ends[0].id == "N1_end"
    assert ends[0].extras.get("note_ref") == "N1"


def test_inline_note_in_head_emits_brackets_but_strips_label():
    """Inline note inside a ``<head>`` still emits brackets, but the note's
    own text does NOT bleed into the section's TOC label."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="H">序<note place="inline" xml:id="N">解</note>言</seg></head>
        <p><seg xml:id="S">x</seg></p>
    """))
    section, _, _, _ = _section_from_div(div)
    # Bracket markers were emitted into the marker stream.
    types = [m.type for m in section.markers]
    assert "tls:note-start" in types
    assert "tls:note-end" in types
    # But the head label captured for navigation excludes the note text.
    assert section.head_text == "序言"


def test_inline_note_paren_around_date_does_not_leak_to_body_text():
    """Source-XML ASCII parens in ``<note>X(<date>Y</date>)Z</note>`` become
    ``punctuation`` markers, not bytes in body text."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="H">頭</seg></head>
        <p><seg xml:id="S">起<note place="inline" xml:id="N">攝(<date>戊寅</date>)前</note>後</seg></p>
    """))
    section, _, _, _ = _section_from_div(div)
    # Body text contains only CJK characters.
    for ch in section.text:
        assert is_allowed_body_char(ch), (
            f"non-CJK leak: {ch!r} (U+{ord(ch):04X}) in {section.text!r}"
        )
    # The literal '(' and ')' from inside the note land as punctuation
    # markers (in addition to the note-start/end brackets).
    punct_chars = [m.content for m in section.markers
                   if m.type == "punctuation"]
    assert "(" in punct_chars
    assert ")" in punct_chars


# ---------- shape 5: body-text CJK invariant --------------------------------


def test_ideographic_space_becomes_indent_markers():
    """Ideographic spaces (U+3000) used as visual indent become
    ``indent`` markers, leaving body text CJK-only."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="H">頭</seg></head>
        <p><seg xml:id="S">\u3000\u3000\u3000內文</seg></p>
    """))
    section, _, _, _ = _section_from_div(div)
    # No ideographic spaces in body text — they moved to indent markers.
    assert "\u3000" not in section.text
    assert section.text == "頭內文"  # head + body, no indents
    indents = [m for m in section.markers if m.type == "indent"]
    assert len(indents) == 3
    assert all(m.content == "\u3000" for m in indents)


def test_ascii_residue_in_seg_text_becomes_punctuation_marker():
    """Stray ASCII characters in seg text (e.g. an authoring-artifact
    period) become ``punctuation`` markers, not text bytes."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="H">頭</seg></head>
        <p><seg xml:id="S">前.後</seg></p>
    """))
    section, _, _, _ = _section_from_div(div)
    assert "." not in section.text
    assert section.text == "頭前後"
    punct = [m for m in section.markers if m.type == "punctuation"]
    assert any(m.content == "." for m in punct)


# ---------- shape 4: typed-seg run folding ----------------------------------


def _seg_run_pairs(markers) -> list[tuple[str, str]]:
    """Return ``[(start_id, end_id), ...]`` for every run in document order."""
    pairs: list[tuple[str, str]] = []
    open_start: str | None = None
    for m in markers:
        if m.type == "tls:seg-start":
            open_start = m.id
        elif m.type == "tls:seg-end":
            assert open_start is not None
            pairs.append((open_start, m.id))
            open_start = None
    assert open_start is None, "unbalanced run"
    return pairs


def test_typed_seg_run_folds_consecutive_same_type():
    """Three consecutive ``type="comm"`` segs fold into one run with one
    start/end pair; member_ids lists every seg's id."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="H">頭</seg></head>
        <p>
          <seg xml:id="A" type="comm">甲</seg>
          <seg xml:id="B" type="comm">乙</seg>
          <seg xml:id="C" type="comm">丙</seg>
        </p>
    """))
    markers = _markers(div)
    starts = [m for m in markers if m.type == "tls:seg-start"]
    ends = [m for m in markers if m.type == "tls:seg-end"]
    assert len(starts) == 1 and len(ends) == 1
    assert starts[0].id == "A"
    assert starts[0].extras["seg_type"] == "comm"
    assert starts[0].extras["member_ids"] == ["A", "B", "C"]
    assert ends[0].id == "C_end"
    assert ends[0].extras["seg_type"] == "comm"


def test_typed_seg_run_breaks_on_different_type():
    """``comm, comm, quote`` produces two runs."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="H">頭</seg></head>
        <p>
          <seg xml:id="A" type="comm">甲</seg>
          <seg xml:id="B" type="comm">乙</seg>
          <seg xml:id="C" type="quote">丙</seg>
        </p>
    """))
    pairs = _seg_run_pairs(_markers(div))
    assert pairs == [("A", "B_end"), ("C", "C_end")]


def test_typed_seg_run_breaks_on_untyped_seg():
    """An untyped seg between two ``comm`` segs splits the run into two."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="H">頭</seg></head>
        <p>
          <seg xml:id="A" type="comm">甲</seg>
          <seg xml:id="B">乙</seg>
          <seg xml:id="C" type="comm">丙</seg>
        </p>
    """))
    pairs = _seg_run_pairs(_markers(div))
    assert pairs == [("A", "A_end"), ("C", "C_end")]


def test_typed_seg_run_does_not_break_on_pb():
    """``<pb>`` between two ``comm`` segs keeps the run intact."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="H">頭</seg></head>
        <p>
          <seg xml:id="A" type="comm">甲</seg>
          <pb xml:id="PB"/>
          <seg xml:id="B" type="comm">乙</seg>
        </p>
    """))
    markers = _markers(div)
    pairs = _seg_run_pairs(markers)
    assert pairs == [("A", "B_end")]
    starts = [m for m in markers if m.type == "tls:seg-start"]
    assert starts[0].extras["member_ids"] == ["A", "B"]


def test_typed_seg_run_breaks_on_inline_note_at_p_level():
    """An inline ``<note>`` as a direct ``<p>`` child breaks the run."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="H">頭</seg></head>
        <p>
          <seg xml:id="A" type="comm">甲</seg>
          <note place="inline" xml:id="N">夾注</note>
          <seg xml:id="B" type="comm">乙</seg>
        </p>
    """))
    pairs = _seg_run_pairs(_markers(div))
    assert pairs == [("A", "A_end"), ("B", "B_end")]


def test_typed_seg_run_closes_at_paragraph_end():
    """A run that's still open at end of ``<p>`` closes before the
    closing ``paragraph-break`` marker."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="H">頭</seg></head>
        <p>
          <seg xml:id="A" type="comm">甲</seg>
          <seg xml:id="B" type="comm">乙</seg>
        </p>
        <p>
          <seg xml:id="C" type="comm">丙</seg>
        </p>
    """))
    types = [m.type for m in _markers(div)]
    # Two distinct runs (one per paragraph) — neither bleeds across.
    assert len([t for t in types if t == "tls:seg-start"]) == 2
    assert len([t for t in types if t == "tls:seg-end"]) == 2
    # Each tls:seg-end appears before the next paragraph-break(close).
    # Sequence sanity: never see two tls:seg-end without an intervening
    # tls:seg-start.
    pairs = _seg_run_pairs(_markers(div))
    assert pairs == [("A", "B_end"), ("C", "C_end")]


def test_typed_seg_run_preserves_per_seg_marker():
    """The per-seg ``tls:seg`` point markers are still emitted *inside*
    runs so annotation offset resolution keeps working. (The head's
    inner seg is emitted as a ``tls:head`` marker, not ``tls:seg``.)"""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="H">頭</seg></head>
        <p>
          <seg xml:id="A" type="comm">甲</seg>
          <seg xml:id="B" type="comm">乙</seg>
        </p>
    """))
    markers = _markers(div)
    seg_marker_ids = [m.id for m in markers if m.type == "tls:seg"]
    assert seg_marker_ids == ["A", "B"]


# ---------- shape 2: nested-div TOC carry-over ------------------------------


def test_nested_div_start_carries_level_and_head_text_extras():
    """``tls:div-start`` for nested divs carries ``level`` (depth) and
    ``head_text`` (CJK-only label) extras so the TOC builder can place
    them. Outermost div is ``Section.head_*`` and is not represented by a
    div-start marker."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="L0">外</seg></head>
        <div xml:id="L1DIV">
          <head><seg xml:id="L1">章一</seg></head>
          <div xml:id="L2DIV">
            <head><seg xml:id="L2">節甲</seg></head>
            <p><seg xml:id="P">x</seg></p>
          </div>
        </div>
    """))
    starts = [m for m in _markers(div) if m.type == "tls:div-start"]
    assert [m.id for m in starts] == ["L1DIV", "L2DIV"]
    assert starts[0].extras["level"] == 2
    assert starts[0].extras["head_text"] == "章一"
    assert starts[1].extras["level"] == 3
    assert starts[1].extras["head_text"] == "節甲"


# ---------- shape 6: <lb/> preservation -------------------------------------


def test_lb_at_div_level_emits_line_break():
    """A bare ``<lb ed=… n=…/>`` between div-level siblings emits a
    ``line-break`` marker with id synthesized as ``{ed}_{n}``."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="H">頭</seg></head>
        <lb ed="X" n="0014c04"/>
        <p xml:id="P1"><seg xml:id="S1">內</seg></p>
    """))
    lbs = [m for m in _markers(div) if m.type == "line-break"]
    assert [m.id for m in lbs] == ["X_0014c04"]


def test_lb_inline_in_seg_emits_line_break():
    """``<lb/>`` siblings inside a ``<seg>``'s mixed content emit
    ``line-break`` markers in document order, and the seg's CJK text
    spans across the lb position remain intact."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="H">頭</seg></head>
        <p xml:id="P">\
<seg xml:id="S">金智<lb ed="X" n="0014c08"/><lb ed="R110" n="0834a05"/>無畏</seg></p>
    """))
    section, _, _, _ = _section_from_div(div)
    lbs = [m for m in section.markers if m.type == "line-break"]
    assert [m.id for m in lbs] == ["X_0014c08", "R110_0834a05"]
    # The two lb markers sit at the same offset, between 金智 and 無畏.
    assert lbs[0].offset == lbs[1].offset
    assert "金智無畏" in section.text


def test_lb_does_not_break_seg_run():
    """``<lb/>`` between consecutive ``<seg type=T>`` siblings does not
    close the typed-seg run — same behaviour as ``<pb/>``."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="H">頭</seg></head>
        <p>
          <seg xml:id="A" type="comm">甲</seg>
          <lb ed="X" n="0014c10"/>
          <seg xml:id="B" type="comm">乙</seg>
        </p>
    """))
    markers = _markers(div)
    starts = [m for m in markers if m.type == "tls:seg-start"]
    ends = [m for m in markers if m.type == "tls:seg-end"]
    assert len(starts) == 1
    assert len(ends) == 1
    # The lb still landed as a marker.
    assert any(m.type == "line-break" and m.id == "X_0014c10" for m in markers)


def test_lb_without_attrs_still_emitted():
    """An attribute-less ``<lb/>`` still produces a ``line-break`` marker
    (with empty id) — preserving the source element's presence."""
    div = _div_xml(textwrap.dedent("""\
        <head><seg xml:id="H">頭</seg></head>
        <lb/>
        <p xml:id="P"><seg xml:id="S">x</seg></p>
    """))
    lbs = [m for m in _markers(div) if m.type == "line-break"]
    assert len(lbs) == 1
    assert lbs[0].id == ""


def test_lb_marker_exports_back_to_lb_element():
    """Exporter round-trip: a ``line-break`` marker with id ``{ed}_{n}``
    emits an ``<lb ed=… n=…/>`` element in the rebuilt XML."""
    from bkk.exporter.tls import _build_div
    from bkk.importer.ir import Marker, Section

    section = Section(
        head_text="頭",
        head_marker_id="H",
        text="金智無畏",
        markers=[
            Marker(type="tls:head", offset=0, content="", id="H"),
            Marker(type="paragraph-break", offset=1, content="", id="P",
                   extras={"role": "open"}),
            Marker(type="tls:seg", offset=1, content="", id="S"),
            Marker(type="line-break", offset=3, content="", id="X_0014c08"),
            Marker(type="line-break", offset=3, content="",
                   id="R110_0834a05"),
            Marker(type="paragraph-break", offset=4, content="", id="P_end",
                   extras={"role": "close"}),
        ],
    )
    div_el = _build_div(section,
                        {"H": {"head_attrs": {}, "head_inner_seg_attrs": {}}},
                        {})
    lbs = div_el.findall(f".//{{{TEI_NS}}}lb")
    assert [lb.get("ed") for lb in lbs] == ["X", "R110"]
    assert [lb.get("n") for lb in lbs] == ["0014c08", "0834a05"]
    # No xml:id should be written — ed/n carry all the lb's identity.
    for lb in lbs:
        assert lb.get(f"{{{XML_NS}}}id") is None


def test_split_lb_id_handles_dedup_suffix():
    """``_split_lb_id`` strips the ``_dup{n}`` collision suffix before
    splitting so duplicate-id round-trips still recover clean ``ed``/``n``."""
    from bkk.exporter.tls import _split_lb_id

    assert _split_lb_id("X_0014c08") == ("X", "0014c08")
    assert _split_lb_id("R110_0834a05") == ("R110", "0834a05")
    assert _split_lb_id("X_0014c08_dup1") == ("X", "0014c08")
    assert _split_lb_id("") == ("", "")
