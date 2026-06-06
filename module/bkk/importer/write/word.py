"""Writers for super-entry, word, and sense YAML records.

A ``WordBundle`` produces three kinds of files:

* one ``super-entries/<hex>/<uuid>.yml`` per word family,
* one ``words/<hex>/<uuid>.yml`` per word entry,
* one ``senses/<hex>/<uuid>.yml`` per sense inside an entry.

Senses are top-level records in their own collection so that they can be
addressed by UUID, edited individually, and ordered explicitly via the
parent entry's ``sense_uuids`` list.
"""

from __future__ import annotations

from pathlib import Path

from bkk.serialize.yaml_io import dump_record

from ..ir import (
    WordBibliographyRef,
    WordBundle,
    WordEntry,
    WordForm,
    WordGrammarLink,
    WordPronunciation,
    WordSense,
    WordUsage,
)
from .concept import knowledge_note_path


def word_super_entry_note_path(out_root: Path, uuid_value: str) -> Path:
    """Return ``<core-out>/super-entries/<first-hex>/<uuid>.yml``."""
    return knowledge_note_path(out_root, "super-entries", uuid_value)


def word_entry_note_path(out_root: Path, uuid_value: str) -> Path:
    """Return ``<core-out>/words/<first-hex>/<uuid>.yml``."""
    return knowledge_note_path(out_root, "words", uuid_value)


def sense_note_path(out_root: Path, uuid_value: str) -> Path:
    """Return ``<core-out>/senses/<first-hex>/<uuid>.yml``."""
    return knowledge_note_path(out_root, "senses", uuid_value)


def write_word(
    word: WordBundle,
    out_root: Path,
    *,
    entries: list[WordEntry] | None = None,
    skip_existing: bool = False,
) -> list[Path]:
    """Write the super-entry, selected word entries, and their senses.

    Returns every file that was written. Existing files are left untouched
    when ``skip_existing`` is true.
    """
    selected_entries = list(word.entries if entries is None else entries)
    written: list[Path] = []

    super_path = word_super_entry_note_path(out_root, word.uuid)
    if not (skip_existing and super_path.exists()):
        dump_record(super_path, _super_entry_record(word, selected_entries))
        written.append(super_path)

    for entry in selected_entries:
        entry_path = word_entry_note_path(out_root, entry.uuid)
        if not (skip_existing and entry_path.exists()):
            dump_record(entry_path, _entry_record(word, entry))
            written.append(entry_path)
        for sense in entry.senses:
            sense_path = sense_note_path(out_root, sense.uuid)
            if skip_existing and sense_path.exists():
                continue
            dump_record(sense_path, _sense_record(entry, sense))
            written.append(sense_path)

    return written


def _super_entry_record(word: WordBundle, entries: list[WordEntry]) -> dict:
    data: dict = {
        "uuid": word.uuid,
        "type": "super-entry",
    }
    if word.orth:
        data["orth"] = word.orth
    if word.n:
        data["n"] = word.n
    if word.forms:
        data["forms"] = [_form_record(form) for form in word.forms]
    if entries:
        data["word_uuids"] = [entry.uuid for entry in _ordered_entries(entries)]
    if word.metadata:
        data["source"] = word.metadata
    return data


def _entry_record(word: WordBundle, entry: WordEntry) -> dict:
    data: dict = {
        "uuid": entry.uuid,
        "type": "word",
        "super_entry_uuid": word.uuid,
    }
    if entry.concept_uuid:
        data["concept_uuid"] = entry.concept_uuid
    if entry.n:
        data["n"] = entry.n
    if entry.form:
        form_data = _form_record(entry.form)
        if form_data:
            data["form"] = form_data
    if entry.definition:
        data["definition"] = entry.definition
    if entry.bibliography:
        data["bibliography"] = [_bibliography_ref(ref) for ref in entry.bibliography]
    if entry.senses:
        data["sense_uuids"] = [sense.uuid for sense in entry.senses]
    source = dict(word.metadata) if word.metadata else {}
    source.update(entry.source)
    if source:
        data["source"] = source
    return data


def _sense_record(entry: WordEntry, sense: WordSense) -> dict:
    data: dict = {
        "uuid": sense.uuid,
        "type": "sense",
        "word_uuid": entry.uuid,
    }
    if sense.n:
        data["n"] = sense.n
    if sense.pos:
        data["pos"] = sense.pos
    syn_uuids = _grammar_uuids(sense.syntactic_functions)
    if syn_uuids:
        data["syntactic_function_uuids"] = syn_uuids
    sem_uuids = _grammar_uuids(sense.semantic_features)
    if sem_uuids:
        data["semantic_feature_uuids"] = sem_uuids
    if sense.definition:
        data["definition"] = sense.definition
    if sense.usages:
        data["usages"] = [_usage_record(usage) for usage in sense.usages]
    if sense.source:
        data["source"] = dict(sense.source)
    return data


def _form_record(form: WordForm) -> dict:
    data: dict = {}
    if form.orth:
        data["orth"] = form.orth
    graph_uuids = _parse_graph_uuids(form.graph_uuid)
    if graph_uuids:
        data["graph_uuids"] = graph_uuids
    if form.pronunciations:
        data["pronunciations"] = [
            _pronunciation_record(pron) for pron in form.pronunciations
        ]
    return data


def _pronunciation_record(pron: WordPronunciation) -> dict:
    data: dict = {"lang": pron.lang, "value": pron.value}
    if pron.resp:
        data["resp"] = pron.resp
    return data


def _bibliography_ref(ref: WordBibliographyRef) -> dict:
    data: dict = {}
    if ref.uuid:
        data["bibliography_uuid"] = ref.uuid
    if ref.scope:
        data["scope"] = ref.scope
    if ref.scope_unit:
        data["scope_unit"] = ref.scope_unit
    if ref.notes:
        data["notes"] = list(ref.notes)
    return data


def _grammar_uuids(links: list[WordGrammarLink]) -> list[str]:
    out: list[str] = []
    for link in links:
        if link.uuid and link.uuid not in out:
            out.append(link.uuid)
    return out


def _usage_record(usage: WordUsage) -> dict:
    data: dict = {"value": usage.value}
    if usage.type:
        data["type"] = usage.type
    return data


def _ordered_entries(entries: list[WordEntry]) -> list[WordEntry]:
    return sorted(
        entries,
        key=lambda entry: (
            (entry.concept or "").casefold(),
            entry.n or "",
            entry.uuid,
        ),
    )


def _parse_graph_uuids(value: str | None) -> list[str]:
    """Split a possibly multi-UUID corresp string into normalized UUIDs.

    The source ``corresp`` for multi-character orths arrives as
    ``"<uuid1> #uuid-<uuid2>"`` (only the leading ``#uuid-`` is stripped by
    the reader). Split on whitespace and normalize each piece.
    """
    out: list[str] = []
    for part in (value or "").split():
        p = part.strip().lstrip("#")
        if p.startswith("uuid-"):
            p = p[len("uuid-"):]
        if p:
            out.append(p)
    return out
