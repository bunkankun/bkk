"""Reader for TLS semantic-feature records."""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from ..ir import SemanticFeatureBundle, SemanticFeatureRelation
from .concept import normalize_uuid


TEI_NS = "http://www.tei-c.org/ns/1.0"
TLS_NS = "http://hxwd.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def _q(local: str, ns: str = TEI_NS) -> str:
    return f"{{{ns}}}{local}"


def read_semantic_features(xml_path: Path) -> list[SemanticFeatureBundle]:
    """Parse every ``<div type="sem-feat">`` in a TEI source file."""
    if not xml_path.exists():
        raise FileNotFoundError(xml_path)

    parser = etree.XMLParser(recover=True, remove_blank_text=False)
    tree = etree.parse(str(xml_path), parser)
    root = tree.getroot()

    records: list[SemanticFeatureBundle] = []
    for div in root.findall(f".//{_q('div')}[@type='sem-feat']"):
        records.append(_parse_sem_feat(div, xml_path))
    return records


def _parse_sem_feat(div, xml_path: Path) -> SemanticFeatureBundle:
    uuid = normalize_uuid(div.get(f"{{{XML_NS}}}id") or "")
    if not uuid:
        raise ValueError("semantic feature is missing xml:id")

    return SemanticFeatureBundle(
        uuid=uuid,
        code=_child_text(div, "head") or uuid,
        descriptions=[
            _text(p)
            for p in div.findall(_q("p"))
            if _text(p)
        ],
        notes=[
            _text(p)
            for p in div.findall(f"{_q('note')}/{_q('p')}")
            if _text(p)
        ],
        relations=[
            *_parse_pointer_relations(div),
            *_parse_source_references(div),
        ],
        metadata=_metadata(div, xml_path),
    )


def _metadata(div, xml_path: Path) -> dict:
    data: dict = {"source_file": xml_path.name}
    for attr_name, key in [
        ("resp", "resp"),
        (f"{{{TLS_NS}}}created", "created"),
        ("created", "created"),
    ]:
        value = div.get(attr_name)
        if value:
            data[key] = value
    return data


def _parse_pointer_relations(div) -> list[SemanticFeatureRelation]:
    pointers = div.find(f"{_q('div')}[@type='pointers']")
    if pointers is None:
        return []

    relations: list[SemanticFeatureRelation] = []
    for lst in pointers.findall(_q("list")):
        rel_type = (lst.get("type") or "").strip()
        refs: list[dict] = []
        for ref in lst.findall(f".//{_q('ref')}"):
            target = normalize_uuid(ref.get("target") or "")
            label = _text(ref)
            if target and label:
                refs.append({"uuid": target, "label": label})
        if rel_type and refs:
            relations.append(SemanticFeatureRelation(
                type=rel_type,
                target_type="semantic-features",
                refs=refs,
            ))
    return relations


def _parse_source_references(div) -> list[SemanticFeatureRelation]:
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
    return [SemanticFeatureRelation(
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
