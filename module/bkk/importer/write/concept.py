"""Writer for concept Markdown notes."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

from ..ir import ConceptBibliographyEntry, ConceptBundle, ConceptRelation
from .yaml_writer import dump


_RELATION_HEADINGS = {
    "antonymy": "Antonym",
    "hypernymy": "Hypernym",
    "taxonymy": "Hyponym",
    "see": "See also",
}
_RELATION_ORDER = ("antonymy", "hypernymy", "taxonymy", "see")
_CJK_RE = re.compile(r"(?<!\[\[)([\u3400-\u9fff]+)(?!\]\])")
_REF_TOKEN_RE = re.compile(r"\{\{BKKREF:([^|}]+)\|([^}]*)\}\}")


def knowledge_note_path(out_root: Path, note_type: str, uuid_value: str) -> Path:
    """Return ``<out>/<type>/<first-hex>/<uuid>.md`` for a knowledge note."""
    uuid_value = _normalize_uuid(uuid_value)
    if not uuid_value:
        raise ValueError(f"{note_type} UUID is empty")
    return out_root / note_type / uuid_value[0].lower() / f"{uuid_value}.md"


def relative_knowledge_link(
    *,
    source_type: str,
    source_uuid: str,
    target_type: str,
    target_uuid: str,
) -> str:
    """Return a Markdown href from one sharded knowledge note to another."""
    source_uuid = _normalize_uuid(source_uuid)
    target_uuid = _normalize_uuid(target_uuid)
    source_shard = source_uuid[0].lower()
    target_shard = target_uuid[0].lower()
    if source_type == target_type:
        if source_shard == target_shard:
            return f"{target_uuid}.md"
        return f"../{target_shard}/{target_uuid}.md"
    return f"../../{target_type}/{target_shard}/{target_uuid}.md"


def concept_note_path(out_root: Path, uuid_value: str) -> Path:
    """Return ``<core-out>/concepts/<first-hex>/<uuid>.md``."""
    return knowledge_note_path(out_root, "concepts", uuid_value)


def write_concept(concept: ConceptBundle, out_root: Path) -> Path:
    """Write one ConceptBundle and return the Markdown path."""
    out_path = concept_note_path(out_root, concept.uuid)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_concept(concept), encoding="utf-8")
    return out_path


def render_concept(concept: ConceptBundle) -> str:
    header = {
        "uuid": concept.uuid,
        "type": "concept",
        "concept": concept.concept,
    }
    if concept.labels:
        header["labels"] = concept.labels
    for lang in ("zh", "och"):
        if lang in concept.translations:
            header[lang] = concept.translations[lang]

    lines: list[str] = ["---"]
    lines.extend(dump(header).rstrip().splitlines())
    lines.append("---")
    lines.append(f"# Concept: {concept.concept}")

    if concept.definition:
        lines.append("# Definition")
        lines.extend(_render_text(p, concept.uuid) for p in concept.definition)

    if concept.notes:
        lines.append("# Criteria and general notes")
        for section in concept.notes:
            lines.append(f"## {_section_heading(section.type)}")
            for p in section.paragraphs:
                if section.type == "old-chinese-criteria":
                    lines.append(_render_text(
                        _wikilink_cjk_terms(p), concept.uuid,
                    ))
                else:
                    lines.append(_render_text(p, concept.uuid))

    if concept.relations:
        lines.append("# Ontology")
        lines.append("")
        for relation in _ordered_relations(concept.relations):
            heading = _RELATION_HEADINGS.get(
                relation.type, _section_heading(relation.type),
            )
            lines.append(f"## {heading}")
            for target_uuid, label in relation.refs:
                lines.append(_uuid_link(
                    label, target_uuid, concept.uuid, target_type="concepts",
                ))

    if concept.bibliography:
        lines.append("# Bibliography")
        for entry in concept.bibliography:
            lines.append(_render_bibl_entry(entry, concept.uuid))
            lines.append("")
        while lines and lines[-1] == "":
            lines.pop()

    lines.append("# Words")
    lines.extend(_render_text(p, concept.uuid) for p in concept.words)
    return "\n".join(lines).rstrip() + "\n"


def _normalize_uuid(value: str) -> str:
    value = (value or "").strip().lstrip("#")
    if value.startswith("uuid-"):
        return value[len("uuid-"):]
    return value


def _section_heading(section_type: str) -> str:
    words = [w for w in section_type.replace("_", "-").split("-") if w]
    return " ".join(w.capitalize() for w in words)


def _ordered_relations(relations: list[ConceptRelation]) -> list[ConceptRelation]:
    by_type: dict[str, ConceptRelation] = {}
    extras: list[ConceptRelation] = []
    for relation in relations:
        if relation.type in _RELATION_ORDER:
            by_type[relation.type] = relation
        else:
            extras.append(relation)
    ordered = [by_type[t] for t in _RELATION_ORDER if t in by_type]
    ordered.extend(extras)
    return ordered


def _uuid_link(
    label: str,
    target_uuid: str,
    source_uuid: str,
    *,
    target_type: str,
) -> str:
    href = relative_knowledge_link(
        source_type="concepts",
        source_uuid=source_uuid,
        target_type=target_type,
        target_uuid=target_uuid,
    )
    return f"[{label}]({href})"


def _render_bibl_entry(
    entry: ConceptBibliographyEntry, source_uuid: str,
) -> str:
    lines: list[str] = []
    if entry.ref_uuid and entry.ref_label:
        link = _uuid_link(
            entry.ref_label,
            entry.ref_uuid,
            source_uuid,
            target_type="bibliography",
        )
        lines.append(f"- {link}")
    elif entry.ref_label:
        lines.append(f"- {entry.ref_label}")
    else:
        lines.append("-")

    if entry.title:
        title_line = f"**{entry.title}**"
        if entry.scope:
            unit = entry.scope_unit or "scope"
            title_line += f" {unit} {entry.scope}"
        lines.append(title_line)
    elif entry.scope:
        unit = entry.scope_unit or "scope"
        lines.append(f"{unit} {entry.scope}")

    lines.extend(entry.notes)
    lines = [
        _render_text(line, source_uuid, target_type="bibliography")
        for line in lines
    ]
    return "\n".join(lines)


def _wikilink_cjk_terms(text: str) -> str:
    return _CJK_RE.sub(r"[[\1]]", text)


def _render_text(
    text: str,
    source_uuid: str,
    *,
    target_type: str = "concepts",
) -> str:
    def repl(match: re.Match[str]) -> str:
        target_uuid = match.group(1)
        label = unquote(match.group(2))
        return _uuid_link(
            label, target_uuid, source_uuid, target_type=target_type,
        )

    return _REF_TOKEN_RE.sub(repl, text)
