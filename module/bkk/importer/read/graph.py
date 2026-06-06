"""Reader for Guangyun graph records."""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from ..ir import GraphBundle
from ._provenance import lift_source
from .concept import normalize_uuid


TLS_NS = "http://exist-db.org/tls"
HXWD_NS = "http://hxwd.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def _q(local: str) -> str:
    return f"{{{TLS_NS}}}{local}"


def read_graph(xml_path: Path) -> GraphBundle:
    """Parse one Guangyun graph XML record."""
    if not xml_path.exists():
        raise FileNotFoundError(xml_path)

    parser = etree.XMLParser(recover=True, remove_blank_text=False)
    tree = etree.parse(str(xml_path), parser)
    root = tree.getroot()
    if etree.QName(root.tag).localname != "guangyun-entry":
        raise ValueError(
            f"{xml_path.name}: expected <guangyun-entry> root"
        )

    uuid = normalize_uuid(root.get(_q("id")) or root.get(f"{{{XML_NS}}}id") or xml_path.stem)
    return GraphBundle(
        uuid=uuid,
        graphs=_parse_graphs(root),
        gloss=_child_text(root, "gloss"),
        xiaoyun=_parse_xiaoyun(root),
        fanqie=_parse_fanqie(root),
        ids=_parse_simple_children(root.find(_q("ids"))),
        locations=_parse_simple_children(root.find(_q("locations"))),
        notes=_parse_notes(root),
        pronunciation=_parse_pronunciation(root),
        source=lift_source(root.find(f"{{{HXWD_NS}}}metadata")),
    )


def _text(el) -> str | None:
    if el is None:
        return None
    text = " ".join("".join(el.itertext()).split())
    return text or None


def _child_text(parent, local: str) -> str | None:
    if parent is None:
        return None
    return _text(parent.find(_q(local)))


def _graph_text(parent, path: str) -> str | None:
    if parent is None:
        return None
    el = parent.find(path)
    return _text(el)


def _parse_graphs(root) -> dict:
    graphs = root.find(_q("graphs"))
    attested = graphs.find(_q("attested-graph")) if graphs is not None else None
    return {
        "attested": _graph_text(attested, _q("graph")),
        "unemended": _graph_text(
            attested, f"{_q('unemended-graph')}/{_q('graph')}",
        ),
        "emended": _graph_text(
            attested, f"{_q('emended-graph')}/{_q('graph')}",
        ),
        "standardised": _graph_text(
            graphs, f"{_q('standardised-graph')}/{_q('graph')}",
        ),
    }


def _parse_xiaoyun(root) -> dict:
    xiaoyun = root.find(_q("xiaoyun"))
    out = _parse_simple_children(xiaoyun)
    if "graph_count" in out and isinstance(out["graph_count"], str):
        try:
            out["graph_count"] = int(out["graph_count"])
        except ValueError:
            pass
    return out


def _parse_fanqie(root) -> dict:
    fanqie = root.find(_q("fanqie"))
    return {
        "shangzi": {
            "attested": _graph_text(
                fanqie,
                f"{_q('fanqie-shangzi')}/{_q('fanqie-shangzi-attested')}/{_q('graph')}",
            ),
            "standard": _graph_text(
                fanqie,
                f"{_q('fanqie-shangzi')}/{_q('fanqie-shangzi-standard')}/{_q('graph')}",
            ),
        },
        "xiazi": {
            "attested": _graph_text(
                fanqie,
                f"{_q('fanqie-xiazi')}/{_q('fanqie-xiazi-attested')}/{_q('graph')}",
            ),
            "standard": _graph_text(
                fanqie,
                f"{_q('fanqie-xiazi')}/{_q('fanqie-xiazi-standard')}/{_q('graph')}",
            ),
        },
    }


def _parse_notes(root) -> dict:
    return {
        "pan_wuyun_note_on_guangyun": _child_text(
            root, "pan-wuyun-note-on-guangyun",
        ),
        "pan_wuyun_note": _child_text(root, "pan-wuyun-note"),
    }


def _parse_pronunciation(root) -> dict:
    pron = root.find(_q("pronunciation"))
    mandarin = pron.find(_q("mandarin")) if pron is not None else None
    mc = pron.find(_q("middle-chinese")) if pron is not None else None
    oc = pron.find(_q("old-chinese")) if pron is not None else None
    return {
        "mandarin": _parse_simple_children(mandarin),
        "middle_chinese": {
            "categories": _parse_simple_children(
                mc.find(_q("categories")) if mc is not None else None,
            ),
            "yundianwang_reconstructions": _parse_simple_children(
                mc.find(_q("yundianwang-reconstructions"))
                if mc is not None else None,
            ),
            "authorial_reconstructions": _parse_simple_children(
                mc.find(_q("authorial-reconstructions"))
                if mc is not None else None,
            ),
        },
        "old_chinese": {
            "pan_wuyun": _parse_simple_children(
                oc.find(_q("pan-wuyun")) if oc is not None else None,
            ),
            "zhengzhang_shangfang": _parse_simple_children(
                oc.find(_q("zhengzhang-shangfang"))
                if oc is not None else None,
            ),
        },
    }


def _parse_simple_children(parent) -> dict:
    if parent is None:
        return {}
    out: dict = {}
    for child in parent:
        key = etree.QName(child.tag).localname.replace("-", "_")
        out[key] = _text(child)
    return out
