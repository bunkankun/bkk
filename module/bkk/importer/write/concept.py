"""Writer for concept YAML records.

Also hosts shared path/UUID helpers used by every core-record writer.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

from bkk.serialize.yaml_io import dump_record

from ..ir import ConceptBibliographyEntry, ConceptBundle, ConceptRelation, ConceptSection


_CJK_RE = re.compile(r"(?<!\[\[)([\u3400-\u9fff]+)(?!\]\])")
_REF_TOKEN_RE = re.compile(r"\{\{BKKREF:([^|}]+)\|([^}]*)\}\}")

# Map source TEI relation types to top-level YAML field names.
_RELATION_FIELD = {
    "antonymy":  "antonyms",
    "hypernymy": "hypernyms",
    "taxonymy":  "hyponyms",
    "see":       "see_also",
}


def knowledge_note_path(out_root: Path, note_type: str, uuid_value: str) -> Path:
    """Return ``<out>/<type>/<first-hex>/<uuid>.yml`` for a core record."""
    uuid_value = _normalize_uuid(uuid_value)
    if not uuid_value:
        raise ValueError(f"{note_type} UUID is empty")
    return out_root / note_type / uuid_value[0].lower() / f"{uuid_value}.yml"


def concept_note_path(out_root: Path, uuid_value: str) -> Path:
    """Return ``<core-out>/concepts/<first-hex>/<uuid>.yml``."""
    return knowledge_note_path(out_root, "concepts", uuid_value)


def write_concept(concept: ConceptBundle, out_root: Path) -> Path:
    """Write one ConceptBundle and return its path."""
    out_path = concept_note_path(out_root, concept.uuid)
    dump_record(out_path, _record(concept))
    return out_path


def _record(concept: ConceptBundle) -> dict:
    data: dict = {
        "uuid": concept.uuid,
        "type": "concept",
        "concept": concept.concept,
    }
    if concept.labels:
        data["alt_labels"] = list(concept.labels)
    for lang in ("zh", "och"):
        if lang in concept.translations:
            data[lang] = concept.translations[lang]
    if concept.definition:
        data["definition"] = _prose_paragraphs(concept.definition)
    if concept.notes:
        data["criteria"] = [_criterion(section) for section in concept.notes]

    for relation in _ordered_relations(concept.relations):
        field = _RELATION_FIELD.get(relation.type)
        if field is None:
            continue
        uuids = [_normalize_uuid(uid) for uid, _ in relation.refs if uid]
        if uuids:
            data[field] = uuids
    other = _other_relations(concept.relations)
    if other:
        data["other_relations"] = other

    if concept.bibliography:
        data["bibliography"] = [_bibliography_ref(b) for b in concept.bibliography]

    if concept.words:
        data["words_text"] = _prose_paragraphs(concept.words)

    if concept.source:
        data["source"] = dict(concept.source)

    return data


def _criterion(section: ConceptSection) -> dict:
    text = _prose_paragraphs(section.paragraphs)
    if section.type == "old-chinese-criteria":
        text = _wikilink_cjk_terms(text)
    return {"type": section.type, "text": text}


def _bibliography_ref(entry: ConceptBibliographyEntry) -> dict:
    data: dict = {}
    if entry.ref_uuid:
        data["bibliography_uuid"] = _normalize_uuid(entry.ref_uuid)
    if entry.scope:
        data["scope"] = entry.scope
    if entry.scope_unit:
        data["scope_unit"] = entry.scope_unit
    if entry.notes:
        data["notes"] = list(entry.notes)
    return data


def _ordered_relations(relations: list[ConceptRelation]) -> list[ConceptRelation]:
    order = list(_RELATION_FIELD.keys())
    by_type: dict[str, ConceptRelation] = {}
    extras: list[ConceptRelation] = []
    for relation in relations:
        if relation.type in order:
            by_type[relation.type] = relation
        else:
            extras.append(relation)
    return [by_type[t] for t in order if t in by_type] + extras


def _other_relations(relations: list[ConceptRelation]) -> list[dict]:
    out: list[dict] = []
    for relation in relations:
        if relation.type in _RELATION_FIELD:
            continue
        uuids = [_normalize_uuid(uid) for uid, _ in relation.refs if uid]
        if uuids:
            out.append({"type": relation.type, "uuids": uuids})
    return out


def _prose_paragraphs(paragraphs: list[str]) -> str:
    return _render_text("\n\n".join(p for p in paragraphs if p is not None))


def _render_text(text: str) -> str:
    """Convert inline TEI ref tokens to bare-UUID markdown links."""

    def repl(match: re.Match[str]) -> str:
        target_uuid = _normalize_uuid(match.group(1))
        label = unquote(match.group(2))
        return f"[{label}]({target_uuid})"

    return _REF_TOKEN_RE.sub(repl, text)


def _wikilink_cjk_terms(text: str) -> str:
    return _CJK_RE.sub(r"[[\1]]", text)


def _normalize_uuid(value: str) -> str:
    value = (value or "").strip().lstrip("#")
    if value.startswith("uuid-"):
        return value[len("uuid-"):]
    return value
