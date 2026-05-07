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

from bkk.importer.cli import _find_tls_text
from bkk.importer.ir import Marker, Section
from bkk.importer.read.tls import (
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


# ---------- end-to-end CBETA importer + manifest ---------------------------


@pytest.fixture(scope="module")
def cbeta_out_root(tmp_path_factory) -> Path:
    """Run the CBETA importer once for the suite."""
    in_root = REPO / "input" / "tls"
    text_xml = _find_tls_text(in_root, TEI_ID)
    assert text_xml is not None, f"{TEI_ID}.xml not found under {in_root}"
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
    juan = _load(cbeta_out_root / f"{KRP_ID}_001.yaml")
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
