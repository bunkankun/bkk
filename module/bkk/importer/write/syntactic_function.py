"""Writer for syntactic-function YAML records."""

from __future__ import annotations

from pathlib import Path

from bkk.serialize.yaml_io import dump_record

from ..ir import SyntacticFunctionBundle
from .concept import knowledge_note_path


def syntactic_function_note_path(out_root: Path, uuid_value: str) -> Path:
    """Return ``<core-out>/syntactic-functions/<first-hex>/<uuid>.yml``."""
    return knowledge_note_path(out_root, "syntactic-functions", uuid_value)


def write_syntactic_function(
    record: SyntacticFunctionBundle,
    out_root: Path,
) -> Path:
    """Write one syntactic-function record and return its path."""
    out_path = syntactic_function_note_path(out_root, record.uuid)
    dump_record(out_path, _record(record))
    return out_path


def _record(record: SyntacticFunctionBundle) -> dict:
    data: dict = {
        "uuid": record.uuid,
        "type": "syntactic-function",
        "code": record.code,
    }
    if record.descriptions:
        data["description"] = "\n\n".join(record.descriptions)
    if record.notes:
        data["notes"] = "\n\n".join(record.notes)
    taxonomy_parents = _taxonomy_parents(record)
    if taxonomy_parents:
        data["taxonomy_parents"] = taxonomy_parents
    if record.metadata:
        data["source"] = record.metadata
    return data


def _taxonomy_parents(record: SyntacticFunctionBundle) -> list[str]:
    parents: list[str] = []
    for relation in record.relations:
        if relation.type != "taxonymy":
            continue
        for target_uuid, _label in relation.refs:
            if target_uuid and target_uuid not in parents:
                parents.append(target_uuid)
    return parents
