"""xml_tree round-trip: importer's _to_tree ↔ exporter's tree_to_element."""

from __future__ import annotations

from lxml import etree

from bkk.exporter.xml_tree import TEI_NS, TLS_NS, tree_to_element
from bkk.importer.read.tls import _to_tree


def _round_trip(xml: str) -> etree._Element:
    root = etree.fromstring(xml)
    node = _to_tree(root)
    return tree_to_element(node, nsmap={None: TEI_NS, "tls": TLS_NS})


def test_simple_element():
    el = _round_trip(b'<title xmlns="http://www.tei-c.org/ns/1.0">hello</title>')
    assert etree.QName(el).localname == "title"
    assert el.text == "hello"


def test_attrs_and_namespaces():
    src = (b'<TEI xmlns="http://www.tei-c.org/ns/1.0" '
           b'xmlns:tls="http://hxwd.org/ns/1.0" '
           b'xml:id="K1">'
           b'<tls:srcline pos="3">x</tls:srcline></TEI>')
    el = _round_trip(src)
    xml_id = el.get("{http://www.w3.org/XML/1998/namespace}id")
    assert xml_id == "K1"
    inner = el[0]
    assert etree.QName(inner).namespace == TLS_NS
    assert etree.QName(inner).localname == "srcline"
    assert inner.get("pos") == "3"
    assert inner.text == "x"


def test_mixed_content_with_tail():
    src = (b'<p xmlns="http://www.tei-c.org/ns/1.0">a'
           b'<c n="-"/>b<c n="-"/>c</p>')
    el = _round_trip(src)
    # Text + 2 children with tails. Whitespace-only text/tail are stripped at
    # capture time, but real text/tail like "a", "b", "c" round-trip.
    assert el.text == "a"
    assert len(el) == 2
    assert el[0].get("n") == "-"
    assert el[0].tail == "b"
    assert el[1].tail == "c"


def test_nested_tree_round_trips_to_equivalent_xml():
    src = (b'<teiHeader xmlns="http://www.tei-c.org/ns/1.0">'
           b'<fileDesc><titleStmt><title>T</title></titleStmt></fileDesc>'
           b'</teiHeader>')
    el = _round_trip(src)
    title = el.find(".//{http://www.tei-c.org/ns/1.0}title")
    assert title is not None
    assert title.text == "T"
