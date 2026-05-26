"""Direct CBETA import path.

The CLI selects by CBETA ``old_id`` from the mapping CSV, reads the XML
directly from a CBETA-style collection directory, and writes the bundle under
the mapped KR id.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from bkk.marker_assets import hydrate_juan_markers, load_marker_asset
from bkk.importer.cli import _find_cbeta_text, run


REPO = Path(__file__).resolve().parents[1]
SOURCE_XML = (
    REPO / "input" / "tls" / "tls-texts" / "data" / "KR6" / "KR6q"
    / "X63n1222.xml"
)


def _write_mapping(
    path: Path,
    kr_id: str = "KR9x0001",
    old_id: str = "X63n1222",
) -> Path:
    path.write_text(
        "kr_id,kr_subsection,old_id,authorityID,json_key,title,category,alt\n"
        f"{kr_id},KR9x,{old_id},CA9999999,X999,Direct CBETA Title,,T9999\n",
        encoding="utf-8",
    )
    return path


def test_cbeta_filename_derives_from_old_id(tmp_path: Path):
    root = tmp_path / "CBETA_XML"
    target = root / "B" / "B10" / "B10n0049.xml"
    target.parent.mkdir(parents=True)
    target.write_text("<TEI/>", encoding="utf-8")

    assert _find_cbeta_text(root, "B10n0049") == target


def test_cli_imports_old_id_to_mapped_kr_id(tmp_path: Path):
    cbeta_root = tmp_path / "cbeta"
    target = cbeta_root / "X" / "X63" / SOURCE_XML.name
    target.parent.mkdir(parents=True)
    target.write_text(SOURCE_XML.read_text(encoding="utf-8"), encoding="utf-8")
    mapping = _write_mapping(tmp_path / "mapping.csv")
    out = tmp_path / "out"

    rc = run([
        "--format", "cbeta",
        "--in", str(cbeta_root),
        "--mapping", str(mapping),
        "--text-id", "X63n1222",
        "--out", str(out),
    ])

    assert rc == 0
    bundle_root = out / "KR9x0001"
    assert bundle_root.is_dir()
    assert (bundle_root / "KR9x0001.manifest.yaml").is_file()
    assert not (out / "X63n1222").exists()

    manifest = yaml.safe_load(
        (bundle_root / "KR9x0001.manifest.yaml").read_text(encoding="utf-8")
    )
    assert manifest["canonical_identifier"] == "bkk:krp/KR9x0001/v1"
    assert manifest["metadata"]["identifiers"]["krp"] == "KR9x0001"
    assert manifest["metadata"]["identifiers"]["cbeta"] == "X63n1222"

    source = yaml.safe_load(
        (bundle_root / "KR9x0001.source.yaml").read_text(encoding="utf-8")
    )
    assert source["format"] == "cbeta-direct"
    assert source["mapping"]["old_id"] == "X63n1222"


def test_cli_imports_native_cbeta_p5_shape(tmp_path: Path):
    source_xml = Path("/home/chris/src/xml-p5/B/B10/B10n0049.xml")
    if not source_xml.exists():
        import pytest

        pytest.skip(f"native CBETA fixture missing at {source_xml}")

    mapping = _write_mapping(
        tmp_path / "mapping.csv",
        kr_id="KR6v0348",
        old_id="B10n0049",
    )
    out = tmp_path / "out"

    rc = run([
        "--format", "cbeta",
        "--in", "/home/chris/src/xml-p5",
        "--mapping", str(mapping),
        "--text-id", "B10n0049",
        "--out", str(out),
    ])

    assert rc == 0
    bundle_root = out / "KR6v0348"
    assert (bundle_root / "KR6v0348.manifest.yaml").is_file()
    assert (bundle_root / "KR6v0348_001.yaml").is_file()

    manifest = yaml.safe_load(
        (bundle_root / "KR6v0348.manifest.yaml").read_text(encoding="utf-8")
    )
    assert manifest["metadata"]["identifiers"]["krp"] == "KR6v0348"
    assert manifest["metadata"]["identifiers"]["cbeta"] == "B10n0049"

    juan = yaml.safe_load(
        (bundle_root / "KR6v0348_001.yaml").read_text(encoding="utf-8")
    )
    hydrated = hydrate_juan_markers(
        juan, load_marker_asset(bundle_root, manifest, 1),
    )
    ids = {
        marker["id"]
        for marker in hydrated["body"]["markers"]
        if marker.get("type") in {"page-break", "line-break"}
    }
    assert "KR6v0348_B_001-0076a03" in ids

    front_juan = yaml.safe_load(
        (bundle_root / "KR6v0348_000.yaml").read_text(encoding="utf-8")
    )
    front_hydrated = hydrate_juan_markers(
        front_juan, load_marker_asset(bundle_root, manifest, 0),
    )
    front_ids = {
        marker["id"]
        for marker in front_hydrated["front"]["markers"]
        if marker.get("type") == "page-break"
    }
    assert "KR6v0348_B_000-0076a" in front_ids
