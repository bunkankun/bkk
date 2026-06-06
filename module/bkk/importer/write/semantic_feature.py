"""Writer for semantic-feature YAML records."""

from __future__ import annotations

from pathlib import Path

from bkk.serialize.yaml_io import dump_record

from ..ir import SemanticFeatureBundle
from .concept import knowledge_note_path


def semantic_feature_note_path(out_root: Path, uuid_value: str) -> Path:
    """Return ``<core-out>/semantic-features/<first-hex>/<uuid>.yml``."""
    return knowledge_note_path(out_root, "semantic-features", uuid_value)


def write_semantic_feature(
    record: SemanticFeatureBundle,
    out_root: Path,
) -> Path:
    """Write one semantic-feature record and return its path."""
    out_path = semantic_feature_note_path(out_root, record.uuid)
    dump_record(out_path, _record(record))
    return out_path


def _record(record: SemanticFeatureBundle) -> dict:
    data: dict = {
        "uuid": record.uuid,
        "type": "semantic-feature",
        "code": record.code,
    }
    if record.descriptions:
        data["description"] = "\n\n".join(record.descriptions)
    if record.notes:
        data["notes"] = "\n\n".join(record.notes)

    taxonomy_parents: list[str] = []
    source_references: list[dict] = []
    for relation in record.relations:
        if relation.type == "taxonymy":
            for ref in relation.refs:
                target = ref.get("uuid")
                if target and target not in taxonomy_parents:
                    taxonomy_parents.append(target)
        elif relation.type == "source-references":
            for ref in relation.refs:
                entry = {"bibliography_uuid": ref.get("uuid")}
                if ref.get("scope"):
                    entry["scope"] = ref.get("scope")
                if ref.get("scope_unit"):
                    entry["scope_unit"] = ref.get("scope_unit")
                source_references.append(entry)

    if taxonomy_parents:
        data["taxonomy_parents"] = taxonomy_parents
    if source_references:
        data["source_references"] = source_references
    if record.metadata:
        data["source"] = record.metadata
    return data
