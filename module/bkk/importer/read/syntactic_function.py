"""Reader for TLS syntactic-function records."""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from ..ir import SyntacticFunctionBundle, SyntacticFunctionRelation
from ._provenance import lift_source
from .concept import normalize_uuid


TEI_NS = "http://www.tei-c.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def _q(local: str, ns: str = TEI_NS) -> str:
    return f"{{{ns}}}{local}"


def read_syntactic_functions(xml_path: Path) -> list[SyntacticFunctionBundle]:
    """Parse every ``<div type="syn-func">`` in a TEI source file."""
    if not xml_path.exists():
        raise FileNotFoundError(xml_path)

    parser = etree.XMLParser(recover=True, remove_blank_text=False)
    tree = etree.parse(str(xml_path), parser)
    root = tree.getroot()

    records: list[SyntacticFunctionBundle] = []
    for div in root.findall(f".//{_q('div')}[@type='syn-func']"):
        records.append(_parse_syn_func(div, xml_path))
    return records


def _parse_syn_func(div, xml_path: Path) -> SyntacticFunctionBundle:
    uuid = normalize_uuid(div.get(f"{{{XML_NS}}}id") or "")
    if not uuid:
        raise ValueError("syntactic function is missing xml:id")

    head = _child_text(div, "head") or uuid
    descriptions = [
        _text(p)
        for p in div.findall(_q("p"))
        if _text(p)
    ]
    notes = [
        _text(p)
        for p in div.findall(f"{_q('note')}/{_q('p')}")
        if _text(p)
    ]
    metadata = _metadata(div, xml_path)

    return SyntacticFunctionBundle(
        uuid=uuid,
        code=head,
        descriptions=descriptions,
        notes=notes,
        relations=_parse_relations(div),
        metadata=metadata,
    )


def _metadata(div, xml_path: Path) -> dict:
    first_p = div.find(_q("p"))
    data: dict = {"source_file": xml_path.name}
    data.update(lift_source(div, first_p))
    return data


def _parse_relations(div) -> list[SyntacticFunctionRelation]:
    pointers = div.find(f"{_q('div')}[@type='pointers']")
    if pointers is None:
        return []

    relations: list[SyntacticFunctionRelation] = []
    for lst in pointers.findall(_q("list")):
        rel_type = (lst.get("type") or "").strip()
        refs: list[tuple[str, str]] = []
        for ref in lst.findall(f".//{_q('ref')}"):
            target = normalize_uuid(ref.get("target") or "")
            label = _text(ref)
            if target and label:
                refs.append((target, label))
        if rel_type and refs:
            relations.append(SyntacticFunctionRelation(type=rel_type, refs=refs))
    return relations


def _child_text(parent, local: str) -> str | None:
    child = parent.find(_q(local))
    return _text(child)


def _text(el) -> str | None:
    if el is None:
        return None
    text = " ".join("".join(el.itertext()).split())
    return text or None
