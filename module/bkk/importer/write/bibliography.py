"""Writer for bibliography YAML records."""

from __future__ import annotations

from pathlib import Path

from bkk.serialize.yaml_io import dump_record

from ..ir import BibliographyBundle
from .concept import knowledge_note_path


def bibliography_note_path(out_root: Path, uuid_value: str) -> Path:
    """Return ``<core-out>/bibliography/<first-hex>/<uuid>.yml``."""
    return knowledge_note_path(out_root, "bibliography", uuid_value)


def write_bibliography(entry: BibliographyBundle, out_root: Path) -> Path:
    """Write one bibliography record and return its path."""
    out_path = bibliography_note_path(out_root, entry.uuid)
    dump_record(out_path, _record(entry))
    return out_path


def _record(entry: BibliographyBundle) -> dict:
    data: dict = {
        "uuid": entry.uuid,
        "type": "bibliography",
    }
    if entry.citation_label:
        data["citation_label"] = entry.citation_label
    if entry.ref_usage:
        data["ref_usage"] = entry.ref_usage
    if entry.resource_type:
        data["resource_type"] = entry.resource_type
    if entry.genres:
        data["genres"] = [
            _drop_none({"value": g.value, "authority": g.authority})
            for g in entry.genres
        ]
    if entry.titles:
        data["titles"] = [
            _drop_none({
                "title": t.title,
                "subtitle": t.subtitle,
                "type": t.type,
                "lang": t.lang,
                "script": t.script,
                "transliteration": t.transliteration,
            })
            for t in entry.titles
        ]
    if entry.contributors:
        data["contributors"] = [
            _drop_none({
                "type": c.type,
                "roles": c.roles or None,
                "given": c.given,
                "family": c.family,
                "lang": c.lang,
                "script": c.script,
                "names": c.names or None,
            })
            for c in entry.contributors
        ]
    if entry.origin:
        data["origin"] = entry.origin
    if entry.notes:
        data["notes"] = [
            _drop_none({"type": n.type, "text": n.text})
            for n in entry.notes
        ]
    if entry.source:
        data["source"] = _drop_none(entry.source)
    return data


def _drop_none(data: dict) -> dict:
    return {k: v for k, v in data.items() if v is not None}
