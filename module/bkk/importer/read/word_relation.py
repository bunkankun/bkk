"""Reader for TLS word-relations.xml.

Walks ``.//div[@type='word-rel-type']`` → ``word-rels`` → ``word-rel`` →
``word-rel-ref``. Each ``<word-rel-ref>`` is one record; ``rel_type``,
``rel_label``, ``group_uuid``, and bibliographic source references are
inherited from ancestors.
"""

from __future__ import annotations

import hashlib
import sys
import uuid as _uuid
from pathlib import Path

from lxml import etree

from ..ir import (
    WordRelationAttestation,
    WordRelationBundle,
    WordRelationItem,
    WordRelationSourceRef,
)
from ._provenance import lift_source
from .concept import normalize_uuid


TEI_NS = "http://www.tei-c.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def _q(local: str, ns: str = TEI_NS) -> str:
    return f"{{{ns}}}{local}"


def read_word_relations(xml_path: Path) -> list[WordRelationBundle]:
    """Parse every ``<word-rel-ref>`` in a TLS word-relations TEI file."""
    if not xml_path.exists():
        raise FileNotFoundError(xml_path)

    parser = etree.XMLParser(recover=True, remove_blank_text=False)
    tree = etree.parse(str(xml_path), parser)
    root = tree.getroot()

    records: list[WordRelationBundle] = []
    seen: set[str] = set()

    for type_div in root.findall(f".//{_q('div')}[@type='word-rel-type']"):
        rel_type_uuid = normalize_uuid(type_div.get(f"{{{XML_NS}}}id") or "")
        head_el = type_div.find(_q("head"))
        rel_type = _text(head_el) or rel_type_uuid

        for rels_div in type_div.findall(f"{_q('div')}[@type='word-rels']"):
            label_el = rels_div.find(_q("p"))
            rel_label = _text(label_el)

            for rel_div in rels_div.findall(f"{_q('div')}[@type='word-rel']"):
                group_uuid = _group_uuid(rel_div, rel_type_uuid)
                source_refs = _parse_source_references(rel_div)

                ref_divs = rel_div.findall(f"{_q('div')}[@type='word-rel-ref']")
                for ref_div in ref_divs:
                    record = _parse_ref(
                        ref_div,
                        group_uuid=group_uuid,
                        rel_type=rel_type,
                        rel_type_uuid=rel_type_uuid,
                        rel_label=rel_label,
                        source_refs=source_refs,
                        xml_path=xml_path,
                    )
                    if record is None:
                        continue
                    if record.uuid in seen:
                        # TLS source occasionally repeats the same xml:id on
                        # parallel <word-rel-ref> divs. Keep the first
                        # occurrence and warn rather than abort.
                        print(
                            f"warning: duplicate word-rel-ref xml:id "
                            f"{record.uuid!r} in {xml_path.name}; "
                            f"keeping first occurrence",
                            file=sys.stderr,
                        )
                        continue
                    seen.add(record.uuid)
                    records.append(record)

    return records


def _parse_ref(
    ref_div,
    *,
    group_uuid: str,
    rel_type: str,
    rel_type_uuid: str,
    rel_label: str | None,
    source_refs: list[WordRelationSourceRef],
    xml_path: Path,
) -> WordRelationBundle | None:
    uuid = normalize_uuid(ref_div.get(f"{{{XML_NS}}}id") or "")
    if not uuid:
        return None

    items = ref_div.findall(f".//{_q('item')}")
    if len(items) < 2:
        return None

    left = right = None
    for item in items:
        parsed = _parse_item(item)
        if parsed is None:
            continue
        if parsed.position == "left-word" and left is None:
            left = parsed
        elif parsed.position == "right-word" and right is None:
            right = parsed

    if left is None or right is None:
        return None

    return WordRelationBundle(
        uuid=uuid,
        group_uuid=group_uuid,
        rel_type=rel_type,
        rel_type_uuid=rel_type_uuid,
        rel_label=rel_label,
        left=left,
        right=right,
        source_references=list(source_refs),
        metadata=_metadata(ref_div, xml_path),
    )


def _parse_item(item) -> WordRelationItem | None:
    position = (item.get("p") or "").strip()
    if position not in {"left-word", "right-word"}:
        return None
    word_uuid = normalize_uuid(item.get("corresp") or "") or None
    concept_uuid = normalize_uuid(item.get("concept-id") or "") or None
    concept = (item.get("concept") or "").strip() or None
    text = _text(item)

    attestation = _parse_attestation(item)

    if word_uuid is None and text is None:
        return None

    return WordRelationItem(
        position=position,
        word_uuid=word_uuid,
        text=text,
        concept=concept,
        concept_uuid=concept_uuid,
        attestation=attestation,
    )


def _parse_attestation(item) -> WordRelationAttestation | None:
    text_title = (item.get("txt") or "").strip() or None
    line_uuid = normalize_uuid(item.get("lineref") or "") or None
    line_id = (item.get("line-id") or "").strip() or None
    textline = (item.get("textline") or "").strip() or None
    offset = _int_attr(item.get("offset"))
    range_ = _int_attr(item.get("range"))

    if not any((text_title, line_uuid, line_id, textline,
                offset is not None, range_ is not None)):
        return None

    return WordRelationAttestation(
        text_title=text_title,
        line_uuid=line_uuid,
        line_id=line_id,
        textline=textline,
        offset=offset,
        range=range_,
    )


def _parse_source_references(rel_div) -> list[WordRelationSourceRef]:
    source_refs_div = rel_div.find(f"{_q('div')}[@type='source-references']")
    if source_refs_div is None:
        return []

    refs: list[WordRelationSourceRef] = []
    for bibl in source_refs_div.findall(f".//{_q('bibl')}"):
        ref_el = bibl.find(_q("ref"))
        target = normalize_uuid(ref_el.get("target") or "") if ref_el is not None else ""
        title_el = bibl.find(_q("title"))
        title = _text(title_el)
        scope_el = bibl.find(_q("biblScope"))
        scope = _text(scope_el)
        scope_unit = (scope_el.get("unit") or "").strip() if scope_el is not None else ""
        if not target and not title:
            continue
        refs.append(WordRelationSourceRef(
            bibliography_uuid=target or None,
            title=title,
            scope=scope,
            scope_unit=scope_unit or None,
        ))
    return refs


def _group_uuid(rel_div, rel_type_uuid: str) -> str:
    """Derive a stable per-<word-rel> group UUID.

    1. Use ``xml:id`` on the <word-rel> when present.
    2. Else hash the sorted (A, B) UUIDs from the optional <link target>
       together with the rel_type_uuid via UUIDv5 in the OID namespace.
    3. Else fall back to the xml:id of the first <word-rel-ref> child.
    """
    explicit = normalize_uuid(rel_div.get(f"{{{XML_NS}}}id") or "")
    if explicit:
        return explicit

    link = rel_div.find(_q("link"))
    if link is not None:
        target = (link.get("target") or "").strip()
        tokens = sorted(normalize_uuid(t) for t in target.split() if t)
        if tokens:
            seed = "|".join([rel_type_uuid, *tokens])
            digest = hashlib.sha1(seed.encode("utf-8")).digest()
            return str(_uuid.UUID(bytes=digest[:16], version=5))

    first_ref = rel_div.find(f"{_q('div')}[@type='word-rel-ref']")
    if first_ref is not None:
        return normalize_uuid(first_ref.get(f"{{{XML_NS}}}id") or "")

    return ""


def _metadata(ref_div, xml_path: Path) -> dict:
    data: dict = {"source_file": xml_path.name}
    data.update(lift_source(ref_div))
    return data


def _int_attr(value) -> int | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _text(el) -> str | None:
    if el is None:
        return None
    text = " ".join("".join(el.itertext()).split())
    return text or None
