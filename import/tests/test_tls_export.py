"""TLS exporter: text XML emission shape tests.

Builds the text XML bytes from the KR6q0053 bundle and verifies a few
structural invariants. Round-trip equality is the goal; this test catches
gross structural failures before that final test runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from bkk.exporter.read_bundle import read_bundle
from bkk.exporter.tls import build_ann_xml, build_text_xml
from bkk.importer.cli import _find_tls_text
from bkk.importer.read.tls import read_tls
from bkk.importer.write.bundle import write_bundle


REPO = Path(__file__).resolve().parents[1]
TEXT_ID = "KR6q0053"
TEI_NS = "http://www.tei-c.org/ns/1.0"
TLS_NS = "http://hxwd.org/ns/1.0"


@pytest.fixture(scope="module")
def rebuilt_bundle(tmp_path_factory):
    in_root = REPO / "input" / "tls"
    text_xml = _find_tls_text(in_root, TEXT_ID)
    bundle = read_tls(
        text_xml,
        in_root / "tls-data" / "notes" / "swl" / f"{TEXT_ID}-ann.xml",
        in_root / "tls-data" / "notes" / "doc" / f"{TEXT_ID}-ann.xml",
        TEXT_ID,
    )
    out_root = tmp_path_factory.mktemp("bkk-out")
    write_bundle(bundle, out_root)
    return read_bundle(out_root / TEXT_ID)


@pytest.fixture(scope="module")
def text_xml_bytes(rebuilt_bundle) -> bytes:
    return build_text_xml(rebuilt_bundle)


def _parse(xml: bytes) -> etree._Element:
    return etree.fromstring(xml)


def test_root_is_tei(text_xml_bytes: bytes):
    root = _parse(text_xml_bytes)
    assert etree.QName(root).namespace == TEI_NS
    assert etree.QName(root).localname == "TEI"
    assert root.get(f"{{http://www.w3.org/XML/1998/namespace}}id") == TEXT_ID


def test_teiheader_present(text_xml_bytes: bytes):
    root = _parse(text_xml_bytes)
    header = root.find(f"{{{TEI_NS}}}teiHeader")
    assert header is not None
    title = header.find(f".//{{{TEI_NS}}}title")
    assert title is not None
    assert title.text == "臨濟錄"


def test_div_count_matches_toc(text_xml_bytes: bytes):
    root = _parse(text_xml_bytes)
    divs = root.findall(f".//{{{TEI_NS}}}body/{{{TEI_NS}}}div")
    # KR6q0053: 4 front + 3 body sections.
    assert len(divs) == 7


def test_each_div_has_head(text_xml_bytes: bytes):
    root = _parse(text_xml_bytes)
    for div in root.findall(f".//{{{TEI_NS}}}body/{{{TEI_NS}}}div"):
        head = div.find(f"{{{TEI_NS}}}head")
        assert head is not None
        seg = head.find(f"{{{TEI_NS}}}seg")
        assert seg is not None
        assert seg.text


def test_seg_xmlid_present(text_xml_bytes: bytes):
    root = _parse(text_xml_bytes)
    segs = root.findall(f".//{{{TEI_NS}}}body//{{{TEI_NS}}}p/{{{TEI_NS}}}seg")
    assert segs, "expected <seg> children inside <p> blocks"
    for seg in segs[:50]:
        assert seg.get(f"{{http://www.w3.org/XML/1998/namespace}}id")


def test_pb_attrs_round_trip(text_xml_bytes: bytes):
    root = _parse(text_xml_bytes)
    pbs = root.findall(f".//{{{TEI_NS}}}body//{{{TEI_NS}}}pb")
    assert pbs
    # The first pb is KR6q0053_T_001-0495a, which carries ed=T47 / n=001-0495a.
    first = pbs[0]
    assert first.get("ed") == "T47"
    assert first.get("n") == "001-0495a"


def test_punctuation_emitted_as_c(text_xml_bytes: bytes):
    root = _parse(text_xml_bytes)
    cs = root.findall(f".//{{{TEI_NS}}}c")
    assert cs, "expected <c/> punctuation elements"
    # n attribute should carry the punctuation char.
    sample = cs[0]
    assert sample.get("n")


def test_xml_serializes_to_bytes(text_xml_bytes: bytes):
    # Confirm it's well-formed and has the XML declaration.
    assert text_xml_bytes.startswith(b"<?xml")
    assert b"<TEI" in text_xml_bytes


def test_swl_ann_xml_emits(rebuilt_bundle):
    xml = build_ann_xml(rebuilt_bundle, "swl")
    assert xml is not None
    root = etree.fromstring(xml)
    assert root.get(f"{{http://www.w3.org/XML/1998/namespace}}id") == f"{TEXT_ID}-ann"
    head = root.find(f".//{{{TEI_NS}}}body/{{{TEI_NS}}}div/{{{TEI_NS}}}head")
    assert head is not None and head.text == "Annotations"
    # tls:ann count matches sidecar swl provenance count.
    anns_in_xml = root.findall(f".//{{{TLS_NS}}}ann")
    info = rebuilt_bundle.source_info["annotations"]
    swl_count = sum(1 for a in info.values() if a.get("provenance") == "swl")
    assert len(anns_in_xml) == swl_count


def test_doc_ann_xml_emits(rebuilt_bundle):
    xml = build_ann_xml(rebuilt_bundle, "doc")
    assert xml is not None
    root = etree.fromstring(xml)
    anns_in_xml = root.findall(f".//{{{TLS_NS}}}ann")
    info = rebuilt_bundle.source_info["annotations"]
    doc_count = sum(1 for a in info.values() if a.get("provenance") == "doc")
    assert len(anns_in_xml) == doc_count
