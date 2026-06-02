"""Writer for semantic-feature Markdown notes."""

from __future__ import annotations

from pathlib import Path

from ..ir import SemanticFeatureBundle, SemanticFeatureRelation
from .concept import knowledge_note_path, relative_knowledge_link
from .yaml_writer import dump


_RELATION_HEADINGS = {
    "source-references": "Source References",
    "taxonymy": "Taxonomy",
}


def semantic_feature_note_path(out_root: Path, uuid_value: str) -> Path:
    """Return ``<core-out>/semantic-features/<first-hex>/<uuid>.md``."""
    return knowledge_note_path(out_root, "semantic-features", uuid_value)


def write_semantic_feature(
    record: SemanticFeatureBundle,
    out_root: Path,
) -> Path:
    """Write one semantic-feature note and return the Markdown path."""
    out_path = semantic_feature_note_path(out_root, record.uuid)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_semantic_feature(record), encoding="utf-8")
    return out_path


def render_semantic_feature(record: SemanticFeatureBundle) -> str:
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
            for ref in relation.refs:
                lines.append(f"- {_link(record, relation, ref)}")

    return "\n".join(lines).rstrip() + "\n"


def _frontmatter(record: SemanticFeatureBundle) -> dict:
    data: dict = {
        "uuid": record.uuid,
        "type": "semantic-feature",
        "code": record.code,
    }
    if record.relations:
        data["relations"] = [
            {
                "type": relation.type,
                "target_type": relation.target_type,
                "refs": relation.refs,
            }
            for relation in record.relations
        ]
    if record.metadata:
        data["source"] = record.metadata
    return data


def _relation_heading(relation: SemanticFeatureRelation) -> str:
    return _RELATION_HEADINGS.get(
        relation.type,
        relation.type.replace("-", " ").replace("_", " ").title(),
    )


def _link(
    record: SemanticFeatureBundle,
    relation: SemanticFeatureRelation,
    ref: dict,
) -> str:
    href = relative_knowledge_link(
        source_type="semantic-features",
        source_uuid=record.uuid,
        target_type=relation.target_type,
        target_uuid=ref["uuid"],
    )
    text = f"[{ref['label']}]({href})"
    details = _ref_details(ref)
    if details:
        return f"{text} - {details}"
    return text


def _ref_details(ref: dict) -> str | None:
    parts: list[str] = []
    title = ref.get("title")
    if title:
        parts.append(title)
    scope = ref.get("scope")
    if scope:
        unit = ref.get("scope_unit") or "scope"
        parts.append(f"{unit} {scope}")
    if not parts:
        return None
    return "; ".join(parts)
