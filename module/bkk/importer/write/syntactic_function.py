"""Writer for syntactic-function Markdown notes."""

from __future__ import annotations

from pathlib import Path

from ..ir import SyntacticFunctionBundle, SyntacticFunctionRelation
from .concept import knowledge_note_path, relative_knowledge_link
from .yaml_writer import dump


_RELATION_HEADINGS = {
    "taxonymy": "Taxonomy",
}


def syntactic_function_note_path(out_root: Path, uuid_value: str) -> Path:
    """Return ``<core-out>/syntactic-functions/<first-hex>/<uuid>.md``."""
    return knowledge_note_path(out_root, "syntactic-functions", uuid_value)


def write_syntactic_function(
    record: SyntacticFunctionBundle,
    out_root: Path,
) -> Path:
    """Write one syntactic-function note and return the Markdown path."""
    out_path = syntactic_function_note_path(out_root, record.uuid)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_syntactic_function(record), encoding="utf-8")
    return out_path


def render_syntactic_function(record: SyntacticFunctionBundle) -> str:
    lines = ["---"]
    lines.extend(dump(_frontmatter(record)).rstrip().splitlines())
    lines.append("---")
    lines.append("")
    lines.append(f"# {record.code}")

    if record.descriptions:
        lines.append("")
        lines.append("## Description")
        lines.extend(record.descriptions)

    if record.notes:
        lines.append("")
        lines.append("## Notes")
        lines.extend(record.notes)

    if record.relations:
        lines.append("")
        lines.append("## Links")
        for relation in record.relations:
            lines.append(f"### {_relation_heading(relation)}")
            for target_uuid, label in relation.refs:
                lines.append(f"- {_link(record.uuid, target_uuid, label)}")

    return "\n".join(lines).rstrip() + "\n"


def _frontmatter(record: SyntacticFunctionBundle) -> dict:
    data: dict = {
        "uuid": record.uuid,
        "type": "syntactic-function",
        "code": record.code,
    }
    if record.relations:
        data["relations"] = [
            {
                "type": relation.type,
                "refs": [
                    {"uuid": target_uuid, "label": label}
                    for target_uuid, label in relation.refs
                ],
            }
            for relation in record.relations
        ]
    if record.metadata:
        data["source"] = record.metadata
    return data


def _relation_heading(relation: SyntacticFunctionRelation) -> str:
    return _RELATION_HEADINGS.get(
        relation.type,
        relation.type.replace("-", " ").replace("_", " ").title(),
    )


def _link(source_uuid: str, target_uuid: str, label: str) -> str:
    href = relative_knowledge_link(
        source_type="syntactic-functions",
        source_uuid=source_uuid,
        target_type="syntactic-functions",
        target_uuid=target_uuid,
    )
    return f"[{label}]({href})"
