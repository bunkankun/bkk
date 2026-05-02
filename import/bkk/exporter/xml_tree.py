"""Convert a sidecar tree node back into an ``lxml`` element.

Inverse of ``bkk.importer.read.tls._to_tree``. The sidecar represents an XML
element as::

    {tag: <prefix:local>, attrs: {...}, text: "...", tail: "...",
     children: [...]}

This module turns that back into a real element. Used by the exporter to
splice the captured ``<teiHeader>`` and per-annotation ``<tls:ann>`` trees
into the rebuilt source XML.
"""

from __future__ import annotations

from lxml import etree


TEI_NS = "http://www.tei-c.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"
TLS_NS = "http://hxwd.org/ns/1.0"

_PREFIX_TO_NS = {
    "tls": TLS_NS,
    "xml": XML_NS,
}


def _expand_tag(name: str) -> str:
    """Element-name expansion: bare names get the TEI default namespace."""
    if name.startswith("{"):
        return name
    if ":" in name:
        prefix, local = name.split(":", 1)
        ns = _PREFIX_TO_NS.get(prefix)
        if ns is None:
            raise ValueError(f"unknown namespace prefix in tag {name!r}")
        return f"{{{ns}}}{local}"
    return f"{{{TEI_NS}}}{name}"


def _expand_attr(name: str) -> str:
    """Attribute-name expansion: bare names stay bare (XML's default namespace
    does not apply to unqualified attributes); prefixed names expand to Clark
    form."""
    if name.startswith("{"):
        return name
    if ":" in name:
        prefix, local = name.split(":", 1)
        ns = _PREFIX_TO_NS.get(prefix)
        if ns is None:
            raise ValueError(f"unknown namespace prefix in attr {name!r}")
        return f"{{{ns}}}{local}"
    return name


def tree_to_element(node: dict, nsmap: dict | None = None) -> etree._Element:
    """Build an lxml element from a sidecar tree node.

    The element is created with the supplied ``nsmap`` (only honored on the
    root element); pass ``nsmap={None: TEI_NS, "tls": TLS_NS}`` for the
    document root so prefixes serialize cleanly.
    """
    tag = _expand_tag(node["tag"])
    if nsmap is not None:
        el = etree.Element(tag, nsmap=nsmap)
    else:
        el = etree.Element(tag)
    for k, v in node.get("attrs", {}).items():
        el.set(_expand_attr(k), v)
    if "text" in node:
        el.text = node["text"]
    for child in node.get("children", []):
        el.append(tree_to_element(child))
    if "tail" in node:
        el.tail = node["tail"]
    return el
