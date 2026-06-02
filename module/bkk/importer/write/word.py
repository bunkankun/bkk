"""Writer for word super-entry and entry Markdown notes."""

from __future__ import annotations

import os
from pathlib import Path

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
from .yaml_writer import dump


def word_super_entry_note_path(out_root: Path, uuid_value: str) -> Path:
    """Return ``<core-out>/super-entries/<first-hex>/<uuid>.md``."""
    return knowledge_note_path(out_root, "super-entries", uuid_value)


def word_entry_note_path(out_root: Path, uuid_value: str) -> Path:
    """Return ``<core-out>/words/<first-hex>/<uuid>.md``."""
    return knowledge_note_path(out_root, "words", uuid_value)


def knowledge_path(out_root: Path, note_type: str, uuid_value: str) -> Path:
    """Return a core knowledge note path for use by relative links."""
    return knowledge_note_path(out_root, note_type, uuid_value)


def relative_note_link(source_path: Path, target_path: Path) -> str:
    """Return a POSIX Markdown href from one note path to another."""
    rel = os.path.relpath(target_path, start=source_path.parent)
    return Path(rel).as_posix()


def write_word(
    word: WordBundle,
    out_root: Path,
    *,
    entries: list[WordEntry] | None = None,
    skip_existing: bool = False,
) -> list[Path]:
    """Write the super-entry plus selected entry notes.

    Returns the files that were written. Existing files are left untouched when
    ``skip_existing`` is true.
    """
    selected_entries = list(word.entries if entries is None else entries)
    written: list[Path] = []

    super_path = word_super_entry_note_path(out_root, word.uuid)
    if not (skip_existing and super_path.exists()):
        super_path.parent.mkdir(parents=True, exist_ok=True)
        super_path.write_text(
            render_word_super_entry(word, selected_entries, out_root),
            encoding="utf-8",
        )
        written.append(super_path)

    for entry in selected_entries:
        entry_path = word_entry_note_path(out_root, entry.uuid)
        if skip_existing and entry_path.exists():
            continue
        entry_path.parent.mkdir(parents=True, exist_ok=True)
        entry_path.write_text(
            render_word_entry(word, entry, out_root),
            encoding="utf-8",
        )
        written.append(entry_path)

    return written


def render_word_super_entry(
    word: WordBundle,
    entries: list[WordEntry],
    out_root: Path,
) -> str:
    source_path = word_super_entry_note_path(out_root, word.uuid)
    ordered_entries = _ordered_entries(entries)
    lines = ["---"]
    lines.extend(dump(_super_entry_frontmatter(word, ordered_entries)).rstrip().splitlines())
    lines.append("---")
    lines.append("")
    lines.append(f"# Super-entry: {_md_text(word.orth or word.uuid)}")

    if word.forms:
        lines.append("")
        lines.append("## Forms")
        for form in word.forms:
            lines.extend(_form_bullets(
                form, source_path=source_path, out_root=out_root,
            ))

    if ordered_entries:
        lines.append("")
        lines.append("## Words")
        for entry in ordered_entries:
            target = word_entry_note_path(out_root, entry.uuid)
            link = _markdown_link(
                entry.concept or entry.uuid,
                relative_note_link(source_path, target),
            )
            details = _entry_index_details(entry)
            lines.append(f"- {link}{details}")

    return "\n".join(lines).rstrip() + "\n"


def render_word_entry(
    word: WordBundle,
    entry: WordEntry,
    out_root: Path,
) -> str:
    source_path = word_entry_note_path(out_root, entry.uuid)
    parent_path = word_super_entry_note_path(out_root, word.uuid)
    lines = ["---"]
    lines.extend(dump(_entry_frontmatter(word, entry)).rstrip().splitlines())
    lines.append("---")
    lines.append("")
    title = word.orth or entry.form.orth if entry.form else word.orth
    lines.append(f"# {_md_text(title or word.uuid)}: {_md_text(entry.concept or entry.uuid)}")
    lines.append("")
    lines.append(
        f"- Super-entry: {_markdown_link(word.orth or word.uuid, relative_note_link(source_path, parent_path))}"
    )
    if entry.concept_uuid:
        concept_path = knowledge_path(out_root, "concepts", entry.concept_uuid)
        lines.append(
            f"- Concept: {_markdown_link(entry.concept or entry.concept_uuid, relative_note_link(source_path, concept_path))}"
        )

    if entry.form:
        lines.append("")
        lines.append("## Form")
        lines.extend(_form_bullets(
            entry.form, source_path=source_path, out_root=out_root,
        ))

    if entry.definition:
        lines.append("")
        lines.append("## Definition")
        lines.append(entry.definition)

    if entry.bibliography:
        lines.append("")
        lines.append("## Bibliography")
        for ref in entry.bibliography:
            lines.append(f"- {_bibliography_line(ref, source_path, out_root)}")

    if entry.senses:
        lines.append("")
        lines.append("## Senses")
        for number, sense in enumerate(entry.senses, start=1):
            lines.extend(_sense_bullets(number, sense, source_path, out_root))

    return "\n".join(lines).rstrip() + "\n"


def _super_entry_frontmatter(
    word: WordBundle,
    entries: list[WordEntry],
) -> dict:
    data: dict = {
        "uuid": word.uuid,
        "type": "super-entry",
    }
    if word.orth:
        data["orth"] = word.orth
    if word.n:
        data["n"] = word.n
    if word.forms:
        data["forms"] = [_form_metadata(form) for form in word.forms]
    if entries:
        data["entries"] = [_entry_index_metadata(entry) for entry in entries]
    if word.metadata:
        data["source"] = word.metadata
    return data


def _entry_frontmatter(word: WordBundle, entry: WordEntry) -> dict:
    data: dict = {
        "uuid": entry.uuid,
        "type": "word",
        "super_entry_uuid": word.uuid,
    }
    if word.orth:
        data["super_entry_orth"] = word.orth
    if entry.concept:
        data["concept"] = entry.concept
    if entry.concept_uuid:
        data["concept_uuid"] = entry.concept_uuid
    if entry.n:
        data["n"] = entry.n
    if entry.form:
        form_data = _form_metadata(entry.form)
        if form_data:
            data["form"] = form_data
    if entry.bibliography:
        data["bibliography"] = [
            _bibliography_metadata(ref)
            for ref in entry.bibliography
        ]
    if entry.senses:
        data["senses"] = [
            _sense_metadata(sense, body_number=i)
            for i, sense in enumerate(entry.senses, start=1)
        ]
    if entry.provenance:
        data["provenance"] = entry.provenance
    if word.metadata:
        data["source"] = word.metadata
    return data


def _form_metadata(form: WordForm) -> dict:
    data: dict = {}
    if form.orth:
        data["orth"] = form.orth
    if form.graph_uuid:
        data["graph_uuid"] = form.graph_uuid
    if form.pronunciations:
        data["pronunciations"] = [
            _pronunciation_metadata(pron)
            for pron in form.pronunciations
        ]
    return data


def _pronunciation_metadata(pron: WordPronunciation) -> dict:
    data = {"lang": pron.lang, "value": pron.value}
    if pron.resp:
        data["resp"] = pron.resp
    return data


def _entry_index_metadata(entry: WordEntry) -> dict:
    data: dict = {
        "uuid": entry.uuid,
        "sense_count": len(entry.senses),
    }
    if entry.concept:
        data["concept"] = entry.concept
    if entry.concept_uuid:
        data["concept_uuid"] = entry.concept_uuid
    if entry.n:
        data["n"] = entry.n
    return data


def _bibliography_metadata(ref: WordBibliographyRef) -> dict:
    data: dict = {}
    if ref.uuid:
        data["uuid"] = ref.uuid
    if ref.label:
        data["label"] = ref.label
    if ref.title:
        data["title"] = ref.title
    if ref.scope:
        data["scope"] = ref.scope
    if ref.scope_unit:
        data["scope_unit"] = ref.scope_unit
    if ref.notes:
        data["notes"] = ref.notes
    return data


def _sense_metadata(sense: WordSense, *, body_number: int) -> dict:
    data: dict = {
        "uuid": sense.uuid,
        "body_number": body_number,
    }
    if sense.n:
        data["n"] = sense.n
    if sense.pos:
        data["pos"] = sense.pos
    if sense.syntactic_functions:
        data["syntactic_functions"] = [
            _grammar_link_metadata(link)
            for link in sense.syntactic_functions
        ]
    if sense.semantic_features:
        data["semantic_features"] = [
            _grammar_link_metadata(link)
            for link in sense.semantic_features
        ]
    if sense.usages:
        data["usages"] = [_usage_metadata(usage) for usage in sense.usages]
    if sense.provenance:
        data["provenance"] = sense.provenance
    return data


def _grammar_link_metadata(link: WordGrammarLink) -> dict:
    data = {"label": link.label}
    if link.uuid:
        data["uuid"] = link.uuid
    return data


def _usage_metadata(usage: WordUsage) -> dict:
    data = {"value": usage.value}
    if usage.type:
        data["type"] = usage.type
    return data


def _form_bullets(
    form: WordForm,
    *,
    source_path: Path,
    out_root: Path,
) -> list[str]:
    lines: list[str] = []
    orth = form.orth or "(no orthograph)"
    if form.graph_uuid:
        graph_path = knowledge_path(out_root, "graphs", form.graph_uuid)
        orth = _markdown_link(orth, relative_note_link(source_path, graph_path))
    lines.append(f"- Orth: {orth}")
    for label, values in _pronunciations_by_role(form.pronunciations).items():
        if values:
            lines.append(f"  - {label}: {', '.join(values)}")
    return lines


def _sense_bullets(
    number: int,
    sense: WordSense,
    source_path: Path,
    out_root: Path,
) -> list[str]:
    parts = []
    syntax = _grammar_links(
        sense.syntactic_functions,
        target_type="syntactic-functions",
        source_path=source_path,
        out_root=out_root,
    )
    if syntax:
        parts.append(f"**{syntax}**")
    semantic_features = _grammar_links(
        sense.semantic_features,
        target_type="semantic-features",
        source_path=source_path,
        out_root=out_root,
    )
    if semantic_features:
        parts.append(f"*{semantic_features}*")
    parts.append(sense.definition or "(no definition)")
    if sense.n:
        parts.append(f"**{sense.n} Attributions**")
    lines = [f"{number}. {' '.join(parts)}"]
    if sense.usages:
        lines.append(
            "   - Usage: "
            + ", ".join(_usage_label(usage) for usage in sense.usages)
        )
    return lines


def _bibliography_line(
    ref: WordBibliographyRef,
    source_path: Path,
    out_root: Path,
) -> str:
    if ref.uuid and ref.label:
        target_path = knowledge_path(out_root, "bibliography", ref.uuid)
        text = _markdown_link(ref.label, relative_note_link(source_path, target_path))
    else:
        text = ref.label or ref.uuid or ""
    details: list[str] = []
    if ref.title:
        details.append(ref.title)
    if ref.scope:
        unit = ref.scope_unit or "scope"
        details.append(f"{unit} {ref.scope}")
    details.extend(ref.notes)
    if details:
        return f"{text} - {'; '.join(details)}"
    return text


def _grammar_links(
    links: list[WordGrammarLink],
    *,
    target_type: str,
    source_path: Path,
    out_root: Path,
) -> str:
    rendered: list[str] = []
    for link in links:
        if link.uuid:
            target_path = knowledge_path(out_root, target_type, link.uuid)
            rendered.append(_markdown_link(
                link.label,
                relative_note_link(source_path, target_path),
            ))
        else:
            rendered.append(_md_text(link.label))
    return ", ".join(rendered)


def _entry_index_details(entry: WordEntry) -> str:
    details = [f"{len(entry.senses)} sense{'s' if len(entry.senses) != 1 else ''}"]
    if entry.n:
        details.append(f"n={entry.n}")
    return f" ({', '.join(details)})"


def _ordered_entries(entries: list[WordEntry]) -> list[WordEntry]:
    return sorted(
        entries,
        key=lambda entry: (
            (entry.concept or "").casefold(),
            entry.n or "",
            entry.uuid,
        ),
    )


def _pronunciations_by_role(prons: list[WordPronunciation]) -> dict[str, list[str]]:
    data = {
        "Pinyin": [],
        "Old Chinese": [],
        "Middle Chinese": [],
        "Provenance": [],
    }
    for pron in prons:
        if pron.lang == "zh-Latn-x-pinyin":
            data["Pinyin"].append(pron.value)
        elif pron.lang == "zh-x-oc":
            data["Old Chinese"].append(pron.value)
        elif pron.lang == "zh-x-mc":
            data["Middle Chinese"].append(pron.value)
        else:
            data["Provenance"].append(f"{pron.lang}: {pron.value}")
        if pron.resp:
            data["Provenance"].append(f"{pron.lang} resp={pron.resp}")
    return data


def _usage_label(usage: WordUsage) -> str:
    if usage.type:
        return f"{usage.type}: {usage.value}"
    return usage.value


def _markdown_link(label: str, href: str) -> str:
    return f"[{_link_label_escape(label)}]({href})"


def _md_text(value: str) -> str:
    return str(value).replace("\n", " ").strip()


def _link_label_escape(value: str) -> str:
    return _md_text(value).replace("[", "\\[").replace("]", "\\]")


def _normalize_uuid(value: str) -> str:
    value = (value or "").strip().lstrip("#")
    if value.startswith("uuid-"):
        return value[len("uuid-"):]
    return value
