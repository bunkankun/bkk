"""Writer for graph Markdown notes."""

from __future__ import annotations

from pathlib import Path

from ..ir import GraphBundle
from .concept import knowledge_note_path
from .yaml_writer import dump


def graph_note_path(out_root: Path, uuid_value: str) -> Path:
    """Return ``<core-out>/graphs/<first-hex>/<uuid>.md``."""
    return knowledge_note_path(out_root, "graphs", uuid_value)


def write_graph(graph: GraphBundle, out_root: Path) -> Path:
    """Write one graph note and return the Markdown path."""
    out_path = graph_note_path(out_root, graph.uuid)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_graph(graph), encoding="utf-8")
    return out_path


def render_graph(graph: GraphBundle) -> str:
    lines = ["---"]
    lines.extend(dump(_frontmatter(graph)).rstrip().splitlines())
    lines.append("---")
    lines.append("")

    lines.append(f"# {_display_graph(graph)}")

    fanqie = _fanqie_text(graph)
    if fanqie:
        lines.append("")
        lines.append("## Fanqie")
        lines.append(fanqie)

    mandarin_jin = (
        graph.pronunciation.get("mandarin", {}).get("jin")
        if graph.pronunciation else None
    )
    if mandarin_jin:
        lines.append("")
        lines.append("## Mandarin")
        lines.append(mandarin_jin)

    return "\n".join(lines).rstrip() + "\n"


def _frontmatter(graph: GraphBundle) -> dict:
    data: dict = {
        "uuid": graph.uuid,
        "type": "graph",
    }
    for key in [
        "graphs",
        "gloss",
        "xiaoyun",
        "fanqie",
        "ids",
        "locations",
        "notes",
        "pronunciation",
    ]:
        value = getattr(graph, key)
        if value not in (None, {}, []):
            data[key] = value
    return data


def _fanqie_text(graph: GraphBundle) -> str | None:
    shangzi = graph.fanqie.get("shangzi", {})
    xiazi = graph.fanqie.get("xiazi", {})
    shang = shangzi.get("attested") or shangzi.get("standard")
    xia = xiazi.get("attested") or xiazi.get("standard")
    if shang or xia:
        return "".join(part for part in [shang, xia] if part)
    return None


def _display_graph(graph: GraphBundle) -> str:
    attested = graph.graphs.get("attested")
    if attested:
        return attested
    standardised = graph.graphs.get("standardised")
    if standardised:
        return f"{standardised} (standardized)"
    return graph.uuid
