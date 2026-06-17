"""Writer for tax-char YAML records."""

from __future__ import annotations

from pathlib import Path

from bkk.serialize.yaml_io import dump_record

from ..ir import TaxCharBundle, TaxCharPronunciation, TaxCharSense
from .concept import knowledge_note_path


def tax_char_note_path(out_root: Path, uuid_value: str) -> Path:
    """Return ``<core-out>/tax-chars/<first-hex>/<uuid>.yml``."""
    return knowledge_note_path(out_root, "tax-chars", uuid_value)


def write_tax_char(record: TaxCharBundle, out_root: Path) -> Path:
    """Write one tax-char record and return its path."""
    out_path = tax_char_note_path(out_root, record.uuid)
    dump_record(out_path, _record(record))
    return out_path


def _record(record: TaxCharBundle) -> dict:
    data: dict = {
        "uuid": record.uuid,
        "type": "tax-char",
        "heads": list(record.heads),
    }
    if record.pronunciations:
        data["pronunciations"] = [_pronunciation(p) for p in record.pronunciations]
    if record.unattributed_senses:
        data["unattributed_senses"] = [_sense(s) for s in record.unattributed_senses]
    if record.metadata:
        data["source"] = dict(record.metadata)
    return data


def _pronunciation(pron: TaxCharPronunciation) -> dict:
    data: dict = {}
    for key in ("reading", "old_chinese", "middle_chinese", "fanqie", "tone", "guangyun"):
        value = getattr(pron, key)
        if value:
            data[key] = value
    if pron.raw:
        data["raw"] = pron.raw
    if pron.senses:
        data["senses"] = [_sense(s) for s in pron.senses]
    return data


def _sense(sense: TaxCharSense) -> dict:
    data: dict = {}
    if sense.gloss:
        data["gloss"] = sense.gloss
    if sense.concept_uuid:
        data["concept_uuid"] = sense.concept_uuid
    if sense.concept_label:
        data["concept_label"] = sense.concept_label
    if sense.children:
        data["children"] = [_sense(c) for c in sense.children]
    return data
