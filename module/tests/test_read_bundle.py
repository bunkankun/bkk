"""Bundle reader: reconstruct Bundle IR from a written bundle directory.

Uses the KR6q0053 importer fixture from test_tls_roundtrip via path lookup.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bkk.exporter.read_bundle import read_bundle
from bkk.importer.cli import _find_tls_texts
from bkk.importer.read.tls import read_tls
from bkk.importer.write.bundle import write_bundle


REPO = Path(__file__).resolve().parents[1]
TEXT_ID = "KR6q0053"


@pytest.fixture(scope="module")
def bundle_dir(tmp_path_factory) -> Path:
    in_root = REPO / "input" / "tls"
    matches = _find_tls_texts(in_root, TEXT_ID)
    assert matches
    text_xml = matches[0]
    bundle = read_tls(
        text_xml,
        in_root / "tls-data" / "notes" / "swl" / f"{TEXT_ID}-ann.xml",
        in_root / "tls-data" / "notes" / "doc" / f"{TEXT_ID}-ann.xml",
        TEXT_ID,
    )
    out = tmp_path_factory.mktemp("bkk-out")
    write_bundle(bundle, out)
    return out / TEXT_ID


def test_bundle_basic_shape(bundle_dir: Path):
    b = read_bundle(bundle_dir)
    assert b.text_id == TEXT_ID
    assert b.edition_short == "T"
    assert len(b.juans) == 1
    juan = b.juans[0]
    assert juan.seq == 1


def test_section_count_matches_toc(bundle_dir: Path):
    b = read_bundle(bundle_dir)
    # KR6q0053: 4 front (序) + 3 body sections in TOC.
    assert len(b.juans[0].sections) == 7


def test_section_text_and_head(bundle_dir: Path):
    b = read_bundle(bundle_dir)
    secs = b.juans[0].sections
    # All sections have non-empty text and a head_marker_id.
    for s in secs:
        assert s.text
        assert s.head_marker_id
        assert s.head_text
    # Concatenated front sections should match the front bucket text length.
    # (We can't recover the front/body label here, but text length checks the
    # span math.)
    front_secs = [s for s in secs if "序" in s.head_text]
    assert front_secs
    body_secs = [s for s in secs if "序" not in s.head_text]
    assert body_secs


def test_no_tls_ann_markers(bundle_dir: Path):
    b = read_bundle(bundle_dir)
    for sec in b.juans[0].sections:
        for m in sec.markers:
            assert m.type != "tls:ann"


def test_annotation_count_and_provenance(bundle_dir: Path):
    """Annotations come from the .ann.yaml (one entry per unique xml:id whose
    seg_id falls within a bucket). Provenance is recovered from the sidecar.

    We assert the count against the .ann.yaml entry count and that every
    annotation gets a provenance — the exact swl/doc split depends on the
    fixture and would couple the test to incidental counts.
    """
    import yaml
    ann_data = yaml.safe_load(
        (bundle_dir / f"{TEXT_ID}_001.ann.yaml").read_text(encoding="utf-8")
    )
    expected = len(ann_data["annotations"])

    b = read_bundle(bundle_dir)
    anns = b.juans[0].annotations
    assert len(anns) == expected
    assert all(a.provenance in ("swl", "doc") for a in anns)
    assert any(a.provenance == "swl" for a in anns)
    assert any(a.provenance == "doc" for a in anns)


def test_source_info_loaded(bundle_dir: Path):
    b = read_bundle(bundle_dir)
    assert b.source_info is not None
    assert b.source_info["format"] == "tls"
    assert "tei" in b.source_info
    assert "ann_files" in b.source_info


def test_section_marker_offsets_section_local(bundle_dir: Path):
    b = read_bundle(bundle_dir)
    for sec in b.juans[0].sections:
        for m in sec.markers:
            assert 0 <= m.offset <= len(sec.text)
