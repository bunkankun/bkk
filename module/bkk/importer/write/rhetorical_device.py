"""Writer for rhetorical-device YAML records."""

from __future__ import annotations

from pathlib import Path

from bkk.serialize.yaml_io import dump_record

from ..ir import RhetoricalDeviceBundle
from .concept import knowledge_note_path


# Map source TEI relation types to top-level YAML field names.
_RELATION_FIELD = {
    "antonymy":  "antonyms",
    "hypernymy": "hypernyms",
    "taxonymy":  "hyponyms",
}


def rhetorical_device_note_path(out_root: Path, uuid_value: str) -> Path:
    """Return ``<core-out>/rhetorical-devices/<first-hex>/<uuid>.yml``."""
    return knowledge_note_path(out_root, "rhetorical-devices", uuid_value)


def write_rhetorical_device(
    record: RhetoricalDeviceBundle,
    out_root: Path,
) -> Path:
    """Write one rhetorical-device record and return its path."""
    out_path = rhetorical_device_note_path(out_root, record.uuid)
    dump_record(out_path, _record(record))
    return out_path


def _record(record: RhetoricalDeviceBundle) -> dict:
    data: dict = {
        "uuid": record.uuid,
        "type": "rhetorical-device",
        "code": record.code,
    }
    if record.translations:
        data["translations"] = dict(record.translations)
    if record.descriptions:
        data["description"] = "\n\n".join(record.descriptions)
    if record.notes:
        data["notes"] = "\n\n".join(record.notes)
    if record.location:
        data["location"] = record.location

    source_references: list[dict] = []
    relation_uuids: dict[str, list[str]] = {}
    for relation in record.relations:
        if relation.type == "source-references":
            for ref in relation.refs:
                entry = {"bibliography_uuid": ref.get("uuid")}
                if ref.get("scope"):
                    entry["scope"] = ref.get("scope")
                if ref.get("scope_unit"):
                    entry["scope_unit"] = ref.get("scope_unit")
                source_references.append(entry)
            continue
        field = _RELATION_FIELD.get(relation.type)
        if field is None:
            continue
        bucket = relation_uuids.setdefault(field, [])
        for ref in relation.refs:
            target = ref.get("uuid")
            if target and target not in bucket:
                bucket.append(target)

    for field in ("hypernyms", "hyponyms", "antonyms"):
        if relation_uuids.get(field):
            data[field] = relation_uuids[field]
    if source_references:
        data["source_references"] = source_references
    if record.metadata:
        data["source"] = record.metadata
    return data
