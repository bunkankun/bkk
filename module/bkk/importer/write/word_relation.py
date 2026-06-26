"""Writer for word-relation YAML records and word YAML back-references."""

from __future__ import annotations

from pathlib import Path

from bkk.serialize.yaml_io import dump_record, load_record

from ..ir import (
    WordRelationAttestation,
    WordRelationBundle,
    WordRelationItem,
    WordRelationSourceRef,
)
from .concept import knowledge_note_path
from .word import word_entry_note_path


def word_relation_note_path(out_root: Path, uuid_value: str) -> Path:
    """Return ``<core-out>/word-relations/<first-hex>/<uuid>.yml``."""
    return knowledge_note_path(out_root, "word-relations", uuid_value)


def write_word_relation(record: WordRelationBundle, out_root: Path) -> Path:
    """Write one word-relation record and return its path."""
    out_path = word_relation_note_path(out_root, record.uuid)
    dump_record(out_path, _record(record))
    return out_path


def patch_word_backrefs(
    out_root: Path,
    by_word_uuid: dict[str, list[str]],
) -> int:
    """Set ``word_relations:`` on each touched word YAML.

    ``by_word_uuid`` maps word UUID → list of word-relation UUIDs. The list
    *replaces* any existing ``word_relations`` field on that word YAML
    (deduped + sorted). Word UUIDs mapping to an empty list have the field
    cleared. Word YAMLs not on disk are silently skipped (the relation may
    reference a word that wasn't imported).

    Returns the number of word YAMLs that were rewritten.
    """
    touched = 0
    for word_uuid, relation_uuids in by_word_uuid.items():
        path = word_entry_note_path(out_root, word_uuid)
        if not path.exists():
            continue
        data = load_record(path)
        new_list = sorted(set(relation_uuids))
        current = data.get("word_relations") or []
        if new_list == current:
            continue
        if new_list:
            data["word_relations"] = new_list
        elif "word_relations" in data:
            del data["word_relations"]
        dump_record(path, data)
        touched += 1
    return touched


def discover_stale_word_backrefs(
    out_root: Path,
    fresh_word_uuids: set[str],
) -> list[str]:
    """Return word UUIDs that currently carry ``word_relations`` on disk but
    are not in ``fresh_word_uuids``.

    These need to be patched with an empty list so the back-ref reflects the
    current XML input.
    """
    words_root = out_root / "words"
    if not words_root.is_dir():
        return []
    stale: list[str] = []
    for yml in words_root.rglob("*.yml"):
        data = load_record(yml)
        word_uuid = data.get("uuid")
        if not word_uuid or word_uuid in fresh_word_uuids:
            continue
        if data.get("word_relations"):
            stale.append(word_uuid)
    return stale


def _record(record: WordRelationBundle) -> dict:
    data: dict = {
        "uuid": record.uuid,
        "type": "word-relation",
    }
    if record.group_uuid:
        data["group_uuid"] = record.group_uuid
    data["rel_type"] = record.rel_type
    if record.rel_type_uuid:
        data["rel_type_uuid"] = record.rel_type_uuid
    if record.rel_label:
        data["rel_label"] = record.rel_label
    if record.left is not None:
        data["left"] = _item(record.left)
    if record.right is not None:
        data["right"] = _item(record.right)
    if record.source_references:
        data["source_references"] = [
            _source_ref(ref) for ref in record.source_references
        ]
    if record.metadata:
        data["source"] = dict(record.metadata)
    return data


def _item(item: WordRelationItem) -> dict:
    data: dict = {}
    if item.word_uuid:
        data["word_uuid"] = item.word_uuid
    if item.text:
        data["text"] = item.text
    if item.concept:
        data["concept"] = item.concept
    if item.concept_uuid:
        data["concept_uuid"] = item.concept_uuid
    if item.attestation is not None:
        att = _attestation(item.attestation)
        if att:
            data["attestation"] = att
    return data


def _attestation(att: WordRelationAttestation) -> dict:
    data: dict = {}
    if att.text_title:
        data["text_title"] = att.text_title
    if att.line_uuid:
        data["line_uuid"] = att.line_uuid
    if att.line_id:
        data["line_id"] = att.line_id
    if att.textline:
        data["textline"] = att.textline
    if att.offset is not None:
        data["offset"] = att.offset
    if att.range is not None:
        data["range"] = att.range
    return data


def _source_ref(ref: WordRelationSourceRef) -> dict:
    data: dict = {}
    if ref.bibliography_uuid:
        data["bibliography_uuid"] = ref.bibliography_uuid
    if ref.title:
        data["title"] = ref.title
    if ref.scope:
        data["scope"] = ref.scope
    if ref.scope_unit:
        data["scope_unit"] = ref.scope_unit
    return data
