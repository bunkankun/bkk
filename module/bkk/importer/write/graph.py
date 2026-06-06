"""Writer for graph YAML records."""

from __future__ import annotations

from pathlib import Path

from bkk.serialize.yaml_io import dump_record

from ..ir import GraphBundle
from .concept import knowledge_note_path


def graph_note_path(out_root: Path, uuid_value: str) -> Path:
    """Return ``<core-out>/graphs/<first-hex>/<uuid>.yml``."""
    return knowledge_note_path(out_root, "graphs", uuid_value)


def write_graph(graph: GraphBundle, out_root: Path) -> Path:
    """Write one graph record and return its path."""
    out_path = graph_note_path(out_root, graph.uuid)
    dump_record(out_path, _record(graph))
    return out_path


def _record(graph: GraphBundle) -> dict:
    data: dict = {
        "uuid": graph.uuid,
        "type": "graph",
    }
    for key in (
        "graphs",
        "gloss",
        "xiaoyun",
        "fanqie",
        "ids",
        "locations",
        "notes",
        "pronunciation",
    ):
        value = getattr(graph, key)
        if value not in (None, {}, []):
            data[key] = value
    if graph.source:
        data["source"] = dict(graph.source)
    return data
