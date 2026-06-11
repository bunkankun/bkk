"""Reader for TLS rhetorical-device records."""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from ..ir import RhetoricalDeviceBundle, RhetoricalDeviceRelation
from ._provenance import TLS_NS, lift_source
from .concept import normalize_uuid


TEI_NS = "http://www.tei-c.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def _q(local: str, ns: str = TEI_NS) -> str:
    return f"{{{ns}}}{local}"


def read_rhetorical_devices(xml_path: Path) -> list[RhetoricalDeviceBundle]:
    """Parse every ``<div type="rhet-dev">`` in a TEI source file."""
    if not xml_path.exists():
        raise FileNotFoundError(xml_path)

    parser = etree.XMLParser(recover=True, remove_blank_text=False)
    tree = etree.parse(str(xml_path), parser)
    root = tree.getroot()

    records: list[RhetoricalDeviceBundle] = []
    for div in root.findall(f".//{_q('div')}[@type='rhet-dev']"):
        records.append(_parse_rhet_dev(div, xml_path))
    return records


def _parse_rhet_dev(div, xml_path: Path) -> RhetoricalDeviceBundle:
    uuid = normalize_uuid(div.get(f"{{{XML_NS}}}id") or "")
    if not uuid:
        raise ValueError("rhetorical device is missing xml:id")

    return RhetoricalDeviceBundle(
        uuid=uuid,
        code=_child_text(div, "head") or uuid,
        descriptions=_parse_definition(div),
        notes=_parse_notes(div),
        location=_parse_location(div),
        translations=_parse_translations(div),
        relations=[
            *_parse_pointer_relations(div),
            *_parse_source_references(div),
        ],
        metadata=_metadata(div, xml_path),
    )


def _parse_definition(div) -> list[str]:
    definition = div.find(f"{_q('div')}[@type='definition']")
    if definition is None:
        return []
    paragraphs = [
        _text(p) for p in definition.findall(_q("p"))
    ]
    return [p for p in paragraphs if p]


def _parse_notes(div) -> list[str]:
    """Collect prose from both ``<note>`` siblings and ``<div type='notes'>``.

    Boilerplate paragraphs reading ``undefined`` are dropped.
    """
    notes: list[str] = []
    for note in div.findall(_q("note")):
        for p in note.findall(_q("p")):
            text = _text(p)
            if text and text.lower() != "undefined":
                notes.append(text)
    notes_div = div.find(f"{_q('div')}[@type='notes']")
    if notes_div is not None:
        for p in notes_div.findall(f".//{_q('p')}"):
            text = _text(p)
            if text and text.lower() != "undefined":
                notes.append(text)
    return notes


def _parse_location(div) -> str | None:
    loc = div.find(f"{_q('div')}[@type='rhet-dev-loc']")
    if loc is None:
        return None
    parts = [_text(p) for p in loc.findall(_q("p"))]
    text = "\n\n".join(p for p in parts if p)
    return text or None


def _parse_translations(div) -> dict[str, str]:
    """Top-level ``<list type='translations'>`` → ``{xml:lang: text}`` dict."""
    translations: dict[str, str] = {}
    for lst in div.findall(_q("list")):
        if (lst.get("type") or "").strip() != "translations":
            continue
        for item in lst.findall(_q("item")):
            lang = (item.get(f"{{{XML_NS}}}lang") or "").strip()
            text = _text(item)
            if lang and text:
                translations[lang] = text
    return translations


def _metadata(div, xml_path: Path) -> dict:
    """Lift provenance from the div and its ``<tls:metadata>`` child."""
    tls_meta = div.find(f"{{{TLS_NS}}}metadata")
    definition = div.find(f"{_q('div')}[@type='definition']")
    data: dict = {"source_file": xml_path.name}
    data.update(lift_source(div, tls_meta, definition))
    return data


def _parse_pointer_relations(div) -> list[RhetoricalDeviceRelation]:
    pointers = div.find(f"{_q('div')}[@type='pointers']")
    if pointers is None:
        return []

    relations: list[RhetoricalDeviceRelation] = []
    for lst in pointers.findall(_q("list")):
        rel_type = (lst.get("type") or "").strip()
        refs: list[dict] = []
        for ref in lst.findall(f".//{_q('ref')}"):
            target = normalize_uuid(ref.get("target") or "")
            label = _text(ref)
            if target and label:
                refs.append({"uuid": target, "label": label})
        if rel_type and refs:
            relations.append(RhetoricalDeviceRelation(
                type=rel_type,
                target_type="rhetorical-devices",
                refs=refs,
            ))
    return relations


def _parse_source_references(div) -> list[RhetoricalDeviceRelation]:
    source_refs = div.find(f"{_q('div')}[@type='source-references']")
    if source_refs is None:
        return []

    refs: list[dict] = []
    for bibl in source_refs.findall(f".//{_q('bibl')}"):
        ref = bibl.find(_q("ref"))
        target = normalize_uuid(ref.get("target") or "") if ref is not None else ""
        label = _text(ref) if ref is not None else None
        if not target or not label:
            continue
        item: dict = {"uuid": target, "label": label}
        title = _child_text(bibl, "title")
        if title:
            item["title"] = title
        scope = bibl.find(_q("biblScope"))
        scope_text = _text(scope)
        if scope_text:
            item["scope"] = scope_text
            unit = (scope.get("unit") or "").strip() if scope is not None else ""
            if unit:
                item["scope_unit"] = unit
        refs.append(item)
    if not refs:
        return []
    return [RhetoricalDeviceRelation(
        type="source-references",
        target_type="bibliography",
        refs=refs,
    )]


def _child_text(parent, local: str) -> str | None:
    child = parent.find(_q(local))
    return _text(child)


def _text(el) -> str | None:
    if el is None:
        return None
    text = " ".join("".join(el.itertext()).split())
    return text or None
