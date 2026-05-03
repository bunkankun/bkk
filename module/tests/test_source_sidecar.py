"""Source sidecar tests.

The sidecar (``<text-id>.source.yaml``) is an auxiliary file the importer
writes alongside the bundle. It captures source-format-specific information
(full ``<teiHeader>`` tree, div/head/seg/pb attrs, annotation provenance and
trees) needed by a future XML exporter to round-trip a bundle back to TEI.

These tests confirm the sidecar is emitted, well-formed, and complete enough
that the round-trip target is achievable. They do *not* exercise the export
itself — that's a follow-up task.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from lxml import etree

from bkk.importer.cli import _find_tls_text
from bkk.importer.read.tls import TLS_NS, _q, read_tls
from bkk.importer.write.bundle import write_bundle


REPO = Path(__file__).resolve().parents[1]
TEXT_ID = "KR6q0053"


@pytest.fixture(scope="module")
def out_root(tmp_path_factory) -> Path:
    in_root = REPO / "input" / "tls"
    text_xml = _find_tls_text(in_root, TEXT_ID)
    assert text_xml is not None
    bundle = read_tls(
        text_xml,
        in_root / "tls-data" / "notes" / "swl" / f"{TEXT_ID}-ann.xml",
        in_root / "tls-data" / "notes" / "doc" / f"{TEXT_ID}-ann.xml",
        TEXT_ID,
    )
    out_dir = tmp_path_factory.mktemp("bkk-out")
    write_bundle(bundle, out_dir)
    return out_dir / TEXT_ID


@pytest.fixture(scope="module")
def sidecar(out_root: Path) -> dict:
    path = out_root / f"{TEXT_ID}.source.yaml"
    assert path.exists(), f"sidecar not emitted at {path}"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_sidecar_emitted(out_root: Path):
    assert (out_root / f"{TEXT_ID}.source.yaml").exists()


def test_format_tag(sidecar: dict):
    assert sidecar["text_id"] == TEXT_ID
    assert sidecar["format"] == "tls"
    assert sidecar["format_version"] == 1


def test_source_files_listed(sidecar: dict):
    files = sidecar["source_files"]
    assert files["text"].endswith(f"{TEXT_ID}.xml")
    assert files["swl_ann"].endswith(f"{TEXT_ID}-ann.xml")
    assert files["doc_ann"].endswith(f"{TEXT_ID}-ann.xml")


def test_tei_header_round_trips(sidecar: dict):
    """The captured tree should rebuild into an XML element whose key
    identifying paths match the original (title text, fileDesc presence).
    """
    header = sidecar["tei"]["header"]
    assert header["tag"] == "teiHeader"

    # The sidecar tree is generic; re-emit and re-parse to confirm shape.
    elem = _tree_to_element(header)
    assert etree.QName(elem).localname == "teiHeader"
    title = elem.find(f".//{_q('title')}")
    assert title is not None
    assert title.text == "臨濟錄"

    # catRefs from textClass should round-trip with their attrs.
    catrefs = elem.findall(f".//{_q('catRef')}")
    assert len(catrefs) >= 1
    assert all(c.get("scheme") and c.get("target") for c in catrefs)


def test_div_coverage(sidecar: dict, out_root: Path):
    """Every section's head_marker_id should have a divs entry — the exporter
    needs the head/seg attrs to rebuild each <div>."""
    juan = yaml.safe_load((out_root / f"{TEXT_ID}_001.yaml").read_text(encoding="utf-8"))
    head_ids: list[str] = []
    for bucket in ("front", "body", "back"):
        bdict = juan.get(bucket) or {}
        for m in bdict.get("markers", []):
            if m["type"] == "tls:head":
                head_ids.append(m["id"])
    assert head_ids, "no head markers found in juan"
    for hid in head_ids:
        assert hid in sidecar["divs"], f"missing div info for {hid}"


def test_pb_attrs_captured(sidecar: dict):
    """Page-break source attrs (@ed, @n) must be captured for round-trip."""
    pb_entries = [v for v in sidecar["markers"].values() if v["type"] == "page-break"]
    assert pb_entries, "no page-break entries in sidecar"
    sample = pb_entries[0]
    assert "ed" in sample["attrs"]
    assert "n" in sample["attrs"]


def test_annotation_provenance_counts(sidecar: dict):
    """Every annotation should carry a swl/doc provenance, and totals should
    match the source file element counts."""
    swl_count = _count_anns(REPO / "input" / "tls" / "tls-data" / "notes" / "swl"
                            / f"{TEXT_ID}-ann.xml")
    doc_count = _count_anns(REPO / "input" / "tls" / "tls-data" / "notes" / "doc"
                            / f"{TEXT_ID}-ann.xml")

    anns = sidecar["annotations"]
    assert anns, "no annotations in sidecar"
    by_prov: dict[str, int] = {}
    for entry in anns.values():
        prov = entry["provenance"]
        assert prov in ("swl", "doc")
        by_prov[prov] = by_prov.get(prov, 0) + 1
    assert by_prov.get("swl", 0) == swl_count
    assert by_prov.get("doc", 0) == doc_count


def test_annotation_tree_preserves_dropped_fields(sidecar: dict):
    """The full <tls:ann> tree should preserve fields the bundle drops:
    link@target, form@corresp, gramGrp inner @corresp, respStmt subtree,
    tls:metadata @resp/@created."""
    sample = next(iter(sidecar["annotations"].values()))
    tree = sample["tree"]
    assert tree["tag"] == "tls:ann"

    flat = list(_walk_tree(tree))
    # link@target
    assert any(n["tag"] == "link" and "target" in n.get("attrs", {})
               for n in flat)
    # respStmt subtree
    assert any(n["tag"] == "respStmt" for n in flat)
    # tls:metadata with resp/created
    md = [n for n in flat if n["tag"] == "tls:metadata"]
    assert md
    assert "resp" in md[0].get("attrs", {})


def test_ann_file_envelopes_captured(sidecar: dict):
    """Each annotation file's envelope must be captured: tei root attrs,
    teiHeader tree, body div head text, p wrapper attrs, and per-seg <line>
    content. The exporter rebuilds both ann files from these."""
    ann_files = sidecar["ann_files"]
    assert "swl" in ann_files and "doc" in ann_files
    swl = ann_files["swl"]
    assert swl["tei_root_attrs"]["xml:id"] == f"{TEXT_ID}-ann"
    assert swl["tei_header"]["tag"] == "teiHeader"
    assert swl["body_div_head"] == "Annotations"
    assert swl["p_attrs"]["xml:id"].startswith(TEXT_ID)
    assert swl["seg_lines"], "no seg_lines captured"
    # Lines should look like real source text — non-empty Han characters.
    sample_line = next(iter(swl["seg_lines"].values()))
    assert sample_line and any("\u4e00" <= c <= "\u9fff" for c in sample_line)


def test_manifest_does_not_reference_sidecar(out_root: Path):
    """The sidecar must not appear in any manifest's assets — it's auxiliary,
    not part of the bundle hash chain."""
    for mf_name in (f"{TEXT_ID}.manifest.yaml",):
        manifest = yaml.safe_load(
            (out_root / mf_name).read_text(encoding="utf-8")
        )
        assets_str = yaml.safe_dump(manifest.get("assets", {}))
        assert ".source.yaml" not in assets_str


# ---------- helpers --------------------------------------------------------


def _count_anns(path: Path) -> int:
    if not path.exists():
        return 0
    tree = etree.parse(str(path))
    return sum(1 for _ in tree.iter(f"{{{TLS_NS}}}ann"))


def _tree_to_element(node: dict):
    """Minimal inverse of read.tls._to_tree, sufficient for these assertions.
    Re-introduces the TEI default namespace so XPath finds work."""
    tag = node["tag"]
    if ":" in tag:
        # tls:foo etc — keep prefix-style for our local needs
        prefix, local = tag.split(":", 1)
        ns_map = {"tei": "http://www.tei-c.org/ns/1.0",
                  "tls": "http://hxwd.org/ns/1.0",
                  "xml": "http://www.w3.org/XML/1998/namespace"}
        clark = f"{{{ns_map[prefix]}}}{local}"
    else:
        clark = f"{{http://www.tei-c.org/ns/1.0}}{tag}"
    el = etree.Element(clark)
    for k, v in node.get("attrs", {}).items():
        if ":" in k:
            prefix, local = k.split(":", 1)
            ns_map = {"xml": "http://www.w3.org/XML/1998/namespace",
                      "tls": "http://hxwd.org/ns/1.0"}
            el.set(f"{{{ns_map[prefix]}}}{local}", v)
        else:
            el.set(k, v)
    if "text" in node:
        el.text = node["text"]
    for child in node.get("children", []):
        el.append(_tree_to_element(child))
    if "tail" in node:
        el.tail = node["tail"]
    return el


def _walk_tree(node: dict):
    yield node
    for child in node.get("children", []):
        yield from _walk_tree(child)
