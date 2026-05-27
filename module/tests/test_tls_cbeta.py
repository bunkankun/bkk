"""Tests for the CBETA-flavor TLS importer.

CBETA-flavor TLS sources mark juan boundaries with explicit
``<juan fun="open" n="NNN">`` elements and carry div-level ``<mulu>``s
that surface in the manifest TOC. This module exercises the dedicated
parse / split path on the X63n1222 fixture (Kanripo id KR6q0116).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bkk.importer.cli import _find_tls_texts
from bkk.importer.ir import Marker, Section
from bkk.importer.read.tls import (
    _parse_text,
    _split_sections_into_cbeta_juans,
    read_tls,
)
from bkk.importer.write.bundle import write_bundle


REPO = Path(__file__).resolve().parents[1]
TEI_ID = "X63n1222"
KRP_ID = "KR6q0116"


# ---------- _split_sections_into_cbeta_juans -------------------------------


def _section(text: str, markers: list[Marker]) -> Section:
    return Section(head_text="", head_marker_id="", text=text, markers=markers)


def test_pre_juan_content_grouped_under_000():
    """Markers preceding the first juan-start land in the ``"000"`` group."""
    sec = _section(
        "preface_body",
        [
            Marker(type="cbeta:mulu", offset=0, content="序",
                   id="X_CBETA_000-mulu-1"),
            Marker(type="tls:seg", offset=0, content="",
                   id="X_CBETA_000-0014c0501.s1"),
            Marker(type="cbeta:juan-start", offset=8, content="",
                   id="X_CBETA_001-juan-start", extras={"juan_n": "001"}),
            Marker(type="tls:seg", offset=8, content="",
                   id="X_CBETA_001-0834a14.s1"),
        ],
    )

    groups = _split_sections_into_cbeta_juans([sec])

    assert [lbl for lbl, _ in groups] == ["000", "001"]
    front_secs = groups[0][1]
    back_secs = groups[1][1]
    assert front_secs[0].text == "preface_"
    assert back_secs[0].text == "body"
    front_marker_ids = [m.id for m in front_secs[0].markers]
    back_marker_ids = [m.id for m in back_secs[0].markers]
    assert front_marker_ids == ["X_CBETA_000-mulu-1", "X_CBETA_000-0014c0501.s1"]
    # juan-start *and* the seg that opens the new juan move to the back group.
    assert back_marker_ids == [
        "X_CBETA_001-juan-start",
        "X_CBETA_001-0834a14.s1",
    ]


def test_no_pre_juan_content_skips_000_group():
    """If the first marker is a juan-start, the ``"000"`` group is empty
    (and therefore omitted) — no synthetic preface section is emitted."""
    sec = _section(
        "body",
        [
            Marker(type="cbeta:juan-start", offset=0, content="",
                   id="X_CBETA_001-juan-start", extras={"juan_n": "001"}),
            Marker(type="tls:seg", offset=0, content="",
                   id="X_CBETA_001-0001a.s1"),
        ],
    )

    groups = _split_sections_into_cbeta_juans([sec])

    assert [lbl for lbl, _ in groups] == ["001"]
    assert groups[0][1][0].text == "body"


def test_classic_tls_emits_configured_xml_element_markers(tmp_path: Path):
    xml = tmp_path / "KR9x0001.xml"
    xml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:id="KR9x0001">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>Test</title></titleStmt>
      <publicationStmt><p/></publicationStmt>
      <sourceDesc><p><idno type="kanripo">KR9x0001</idno></p></sourceDesc>
    </fileDesc>
  </teiHeader>
  <text>
    <body>
      <div>
        <head><seg xml:id="KR9x0001_T_001-h">題</seg></head>
        <p xml:id="p1" rend="test"><seg xml:id="KR9x0001_T_001-1a.1">甲乙</seg></p>
      </div>
    </body>
  </text>
</TEI>
""",
        encoding="utf-8",
    )

    sections, *_ = _parse_text(xml, "KR9x0001", xml_elements=["p"])

    markers = [m for m in sections[0].markers if m.type == "xml-element"]
    assert [(m.offset, m.extras["name"], m.extras["role"]) for m in markers] == [
        (1, "p", "open"),
        (3, "p", "close"),
    ]
    assert all(m.type != "paragraph-break" for m in sections[0].markers)


# ---------- end-to-end CBETA importer + manifest ---------------------------


@pytest.fixture(scope="module")
def cbeta_out_root(tmp_path_factory) -> Path:
    """Run the CBETA importer once for the suite."""
    in_root = REPO / "input" / "tls"
    matches = _find_tls_texts(in_root, TEI_ID)
    assert matches, f"{TEI_ID}.xml not found under {in_root}"
    text_xml = matches[0]
    bundle = read_tls(
        text_xml,
        in_root / "tls-data" / "notes" / "swl" / f"{TEI_ID}-ann.xml",
        in_root / "tls-data" / "notes" / "doc" / f"{TEI_ID}-ann.xml",
        TEI_ID,
    )
    out_dir = tmp_path_factory.mktemp("bkk-out-cbeta")
    write_bundle(bundle, out_dir)
    # Bundle is keyed by Kanripo id (KR6q0116), not the TEI id.
    return out_dir / KRP_ID


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_hydrated(bundle_dir: Path, seq: int) -> dict:
    from bkk.marker_assets import hydrate_juan_markers, load_marker_asset

    manifest = _load(bundle_dir / f"{KRP_ID}.manifest.yaml")
    juan = _load(bundle_dir / f"{KRP_ID}_{seq:03d}.yaml")
    return hydrate_juan_markers(
        juan, load_marker_asset(bundle_dir, manifest, seq),
    )


def test_two_juans_emitted(cbeta_out_root: Path):
    """Pre-juan content → ``_000.yaml``, juan-1 body → ``_001.yaml``."""
    assert (cbeta_out_root / f"{KRP_ID}_000.yaml").exists()
    assert (cbeta_out_root / f"{KRP_ID}_001.yaml").exists()


def test_pre_juan_content_in_front_bucket(cbeta_out_root: Path):
    """The ``_000`` juan is forced entirely into the ``front`` bucket."""
    juan = _load(cbeta_out_root / f"{KRP_ID}_000.yaml")
    assert juan["front"]["text"]
    # Body stays empty for the synthetic preface juan.
    assert juan["body"]["text"] == ""


def test_byline_seg_lives_in_juan_001(cbeta_out_root: Path):
    """The first body seg of juan 1 (a ``<byline>`` child) must land in
    ``_001.yaml`` — verifies the permissive walker reaches into byline."""
    juan = _load_hydrated(cbeta_out_root, 1)
    body_marker_ids = {m["id"] for m in juan["body"]["markers"]}
    assert f"{TEI_ID}_CBETA_001-0834a14.s1" in body_marker_ids


def test_manifest_lists_both_parts(cbeta_out_root: Path):
    manifest = _load(cbeta_out_root / f"{KRP_ID}.manifest.yaml")
    seqs = [p["seq"] for p in manifest["assets"]["parts"]]
    assert seqs == [0, 1]


def test_manifest_toc_emits_juan_and_mulu_entries(cbeta_out_root: Path):
    """CBETA TOC drops ``type: section`` entries; emits one ``type: juan``
    per juan with a ``<jhead>``, plus one ``type: mulu`` per div-level
    ``<mulu>`` with text content."""
    manifest = _load(cbeta_out_root / f"{KRP_ID}.manifest.yaml")
    toc = manifest["table_of_contents"]
    types = [entry["type"] for entry in toc]

    assert "section" not in types
    assert types.count("juan") == 1
    assert types.count("mulu") == 2

    juan_entry = next(e for e in toc if e["type"] == "juan")
    assert juan_entry["label"] == "修禪要訣"
    assert juan_entry["ref"]["span"][0] == "body"

    mulu_labels = [e["label"] for e in toc if e["type"] == "mulu"]
    assert "No. 1222-A 刻修禪要訣序" in mulu_labels
    assert "修禪要訣" in mulu_labels


def test_manifest_format_marks_cbeta_flavor(cbeta_out_root: Path):
    """The bundle's ``source.format`` distinguishes CBETA-flavor from classic."""
    source = _load(cbeta_out_root / f"{KRP_ID}.source.yaml")
    assert source["format"] == "tls-cbeta"
