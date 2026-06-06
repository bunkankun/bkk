"""Reader for TLS-style concept XML files."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from lxml import etree

from ..ir import (
    ConceptBibliographyEntry,
    ConceptBundle,
    ConceptRelation,
    ConceptSection,
)
from ._provenance import lift_source


TEI_NS = "http://www.tei-c.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def _q(local: str, ns: str = TEI_NS) -> str:
    return f"{{{ns}}}{local}"


def normalize_uuid(value: str) -> str:
    value = (value or "").strip().lstrip("#")
    if value.startswith("uuid-"):
        return value[len("uuid-"):]
    return value


def ref_token(target_uuid: str, label: str) -> str:
    """Encode an inline UUID ref for the writer to make path-relative."""
    return f"{{{{BKKREF:{target_uuid}|{quote(label, safe='')}}}}}"


def read_concept(xml_path: Path) -> ConceptBundle:
    """Parse one ``<div type="concept">`` XML file into a ConceptBundle."""
    if not xml_path.exists():
        raise FileNotFoundError(xml_path)

    parser = etree.XMLParser(recover=True, remove_blank_text=False)
    tree = etree.parse(str(xml_path), parser)
    root = tree.getroot()

    type_attr = (root.get("type") or "").strip()
    if type_attr != "concept":
        raise ValueError(
            f"{xml_path.name}: root @type is {type_attr!r}, expected 'concept'"
        )

    uuid = normalize_uuid(root.get(_q("id", XML_NS)) or root.get("xml:id") or "")
    if not uuid:
        raise ValueError(f"{xml_path.name}: concept is missing xml:id")

    head = root.find(_q("head"))
    concept = _text_content(head) or xml_path.stem

    labels = _list_items(root, "altnames")
    translations = {
        (item.get(_q("lang", XML_NS)) or "").strip(): _text_content(item)
        for item in root.findall(f"{_q('list')}[@type='translations']/{_q('item')}")
        if (item.get(_q("lang", XML_NS)) or "").strip() and _text_content(item)
    }

    definition = _paragraphs_in_first_div(root, "definition")
    notes = _parse_notes(root)
    relations = _parse_relations(root)
    bibliography = _parse_bibliography(root)
    words = _paragraphs_in_first_div(root, "words")
    source = _source(root)

    return ConceptBundle(
        uuid=uuid,
        concept=concept,
        labels=labels,
        translations=translations,
        definition=definition,
        notes=notes,
        relations=relations,
        bibliography=bibliography,
        words=words,
        source=source,
    )


def _source(root) -> dict:
    """Lift resp/date from the root <div>, the <head>, the definition <div>, and its first <p>."""
    head = root.find(_q("head"))
    def_div = root.find(f"{_q('div')}[@type='definition']")
    first_p = def_div.find(f".//{_q('p')}") if def_div is not None else None
    return lift_source(root, head, def_div, first_p)


def _text_content(el) -> str:
    return _content(el, refs=False)


def _paragraph_content(el) -> str:
    return _content(el, refs=True)


def _content(el, *, refs: bool) -> str:
    if el is None:
        return ""
    parts: list[str] = []

    def walk(node) -> None:
        if node.text:
            parts.append(node.text)
        for child in node:
            tag = etree.QName(child.tag).localname
            target = normalize_uuid(child.get("target") or "")
            if refs and tag == "ref" and target:
                parts.append(ref_token(target, _content(child, refs=False)))
            else:
                walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(el)
    return " ".join("".join(parts).split())


def _list_items(root, list_type: str) -> list[str]:
    values: list[str] = []
    for item in root.findall(f"{_q('list')}[@type='{list_type}']/{_q('item')}"):
        text = _text_content(item)
        if text:
            values.append(text)
    return values


def _paragraphs_in_first_div(root, div_type: str) -> list[str]:
    div = root.find(f"{_q('div')}[@type='{div_type}']")
    if div is None:
        return []
    return [
        _paragraph_content(p)
        for p in div.findall(f".//{_q('p')}")
        if _paragraph_content(p)
    ]


def _parse_notes(root) -> list[ConceptSection]:
    notes_div = root.find(f"{_q('div')}[@type='notes']")
    if notes_div is None:
        return []

    sections: list[ConceptSection] = []
    for div in notes_div.findall(_q("div")):
        div_type = (div.get("type") or "").strip()
        paragraphs = [
            _paragraph_content(p)
            for p in div.findall(f".//{_q('p')}")
            if _paragraph_content(p)
        ]
        if div_type or paragraphs:
            sections.append(ConceptSection(type=div_type, paragraphs=paragraphs))
    return sections


def _parse_relations(root) -> list[ConceptRelation]:
    pointers = root.find(f"{_q('div')}[@type='pointers']")
    if pointers is None:
        return []

    relations: list[ConceptRelation] = []
    for lst in pointers.findall(_q("list")):
        rel_type = (lst.get("type") or "").strip()
        refs: list[tuple[str, str]] = []
        for ref in lst.findall(f".//{_q('ref')}"):
            target = normalize_uuid(ref.get("target") or "")
            label = _text_content(ref)
            if target and label:
                refs.append((target, label))
        if rel_type and refs:
            relations.append(ConceptRelation(type=rel_type, refs=refs))
    return relations


def _parse_bibliography(root) -> list[ConceptBibliographyEntry]:
    source_refs = root.find(f"{_q('div')}[@type='source-references']")
    if source_refs is None:
        return []

    entries: list[ConceptBibliographyEntry] = []
    for bibl in source_refs.findall(f".//{_q('bibl')}"):
        ref = bibl.find(_q("ref"))
        ref_uuid = normalize_uuid(ref.get("target") or "") if ref is not None else None
        ref_label = _text_content(ref) if ref is not None else None

        title_el = bibl.find(_q("title"))
        title = _text_content(title_el) or None

        scope_el = bibl.find(_q("biblScope"))
        scope = _text_content(scope_el) or None
        scope_unit = (
            (scope_el.get("unit") or "").strip()
            if scope_el is not None else None
        ) or None

        notes: list[str] = []
        for p in bibl.findall(f"{_q('note')}/{_q('p')}"):
            text = _paragraph_content(p)
            if text:
                notes.append(text)

        if ref_uuid or ref_label or title or scope or notes:
            entries.append(ConceptBibliographyEntry(
                ref_uuid=ref_uuid,
                ref_label=ref_label,
                title=title,
                scope_unit=scope_unit,
                scope=scope,
                notes=notes,
            ))
    return entries
