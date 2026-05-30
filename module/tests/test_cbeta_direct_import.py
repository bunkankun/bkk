"""Direct CBETA import path.

The CLI selects by CBETA ``old_id`` from the mapping CSV, reads the XML
directly from a CBETA-style collection directory, and writes the bundle under
the mapped KR id.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from bkk.marker_assets import hydrate_juan_markers, load_marker_asset
from bkk.importer.cli import _find_cbeta_text, _find_cbeta_texts, run
from bkk.importer.read.cbeta import read_cbeta


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


def test_direct_reader_adds_apparatus_variants_from_back(tmp_path: Path):
    xml = tmp_path / "T01n0001.xml"
    xml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0"
     xmlns:cb="http://www.cbeta.org/ns/1.0"
     xml:id="T01n0001">
  <teiHeader>
    <fileDesc>
      <titleStmt><title xml:lang="zh-Hant">測試經</title></titleStmt>
      <publicationStmt><p/></publicationStmt>
      <sourceDesc><p/></sourceDesc>
    </fileDesc>
    <encodingDesc>
      <tagsDecl>
        <namespace name="http://www.tei-c.org/ns/1.0">
          <tagUsage gi="rdg">
            <listWit>
              <witness xml:id="wit.cbeta">【CB】</witness>
              <witness xml:id="wit.orig">【底本】</witness>
            </listWit>
          </tagUsage>
        </namespace>
      </tagsDecl>
    </encodingDesc>
  </teiHeader>
  <text>
    <body>
      <cb:juan fun="open" n="1"/>
      <p>甲<anchor xml:id="beg0001"/>乙<anchor xml:id="end0001"/>丙</p>
    </body>
    <back>
      <cb:div type="apparatus">
        <app from="#beg0001" to="#end0001">
          <lem wit="#wit.cbeta">乙</lem>
          <rdg wit="#wit.orig">二</rdg>
        </app>
      </cb:div>
    </back>
  </text>
</TEI>
""",
        encoding="utf-8",
    )

    bundle = read_cbeta(
        xml,
        {
            "kr_id": "KR9x0001",
            "old_id": "T01n0001",
            "title": "",
        },
    )

    markers = bundle.juans[0].sections[0].markers
    variant = next(marker for marker in markers if marker.type == "variant")
    assert variant.offset == 1
    assert variant.content == "乙"
    assert variant.extras == {"length": 1, "底本": "二"}
    assert bundle.metadata["editions"] == [
        {"short": "底本", "label": "底本", "source_xml_id": "wit.orig"}
    ]
    assert bundle.witnesses == ["底本"]


def test_direct_reader_emits_configured_xml_element_markers(tmp_path: Path):
    xml = tmp_path / "T01n0001.xml"
    xml.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0"
     xmlns:cb="http://www.cbeta.org/ns/1.0"
     xml:id="T01n0001">
  <teiHeader>
    <fileDesc>
      <titleStmt><title xml:lang="zh-Hant">測試經</title></titleStmt>
      <publicationStmt><p/></publicationStmt>
      <sourceDesc><p/></sourceDesc>
    </fileDesc>
  </teiHeader>
  <text>
    <body>
      <cb:juan fun="open" n="1"/>
      <p xml:id="p1" rend="test">甲乙</p>
    </body>
  </text>
</TEI>
""",
        encoding="utf-8",
    )

    bundle = read_cbeta(
        xml,
        {"kr_id": "KR9x0001", "old_id": "T01n0001", "title": ""},
        xml_elements=["p"],
    )

    markers = [
        marker for marker in bundle.juans[0].sections[0].markers
        if marker.type == "xml-element"
    ]
    assert [(m.offset, m.extras["name"], m.extras["role"]) for m in markers] == [
        (0, "p", "open"),
        (2, "p", "close"),
    ]
    assert markers[0].id == "p1"
    assert markers[0].extras["attrs"] == {"xml:id": "p1", "rend": "test"}


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


# ── _find_cbeta_texts ────────────────────────────────────────────────────────


def _make_cbeta_file(root: Path, collection: str, volume: str, stem: str) -> Path:
    p = root / collection / volume / f"{stem}.xml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("<TEI/>", encoding="utf-8")
    return p


def test_find_cbeta_texts_single(tmp_path: Path):
    root = tmp_path / "cbeta"
    target = _make_cbeta_file(root, "T", "T01", "T01n0001")
    result = _find_cbeta_texts(root, "T01n0001")
    assert result == [target]


def test_find_cbeta_texts_letter_suffix_no_glob(tmp_path: Path):
    """old_id with a letter suffix triggers exact match, not glob."""
    root = tmp_path / "cbeta"
    target = _make_cbeta_file(root, "T", "T08", "T08n0236a")
    # Distractor that the glob would wrongly pick up:
    _make_cbeta_file(root, "T", "T08", "T08n0236b")
    result = _find_cbeta_texts(root, "T08n0236a")
    assert result == [target]


def test_find_cbeta_texts_multivolume_letter_suffix(tmp_path: Path):
    """T05n0220-style: no exact file, letter-suffixed parts across volumes."""
    root = tmp_path / "cbeta"
    fa = _make_cbeta_file(root, "T", "T05", "T05n0220a")
    fb = _make_cbeta_file(root, "T", "T06", "T06n0220b")
    fc = _make_cbeta_file(root, "T", "T07", "T07n0220c")
    result = _find_cbeta_texts(root, "T05n0220")
    assert [p.stem for p in result] == ["T05n0220a", "T06n0220b", "T07n0220c"]
    assert result == [fa, fb, fc]


def test_find_cbeta_texts_multivolume_no_letter_suffix(tmp_path: Path):
    """X81n1571-style: primary file exists, companion volume also present."""
    root = tmp_path / "cbeta"
    f81 = _make_cbeta_file(root, "X", "X81", "X81n1571")
    f82 = _make_cbeta_file(root, "X", "X82", "X82n1571")
    result = _find_cbeta_texts(root, "X81n1571")
    assert [p.stem for p in result] == ["X81n1571", "X82n1571"]
    assert result == [f81, f82]


def _minimal_cbeta_xml(xml_id: str, juan_nums: list[int]) -> str:
    juans = "\n".join(
        f'<cb:juan fun="open" n="{n}"/><p>文{n}</p>'
        for n in juan_nums
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0"
     xmlns:cb="http://www.cbeta.org/ns/1.0"
     xml:id="{xml_id}">
  <teiHeader>
    <fileDesc>
      <titleStmt><title xml:lang="zh-Hant">測試</title></titleStmt>
      <publicationStmt><p/></publicationStmt>
      <sourceDesc><p/></sourceDesc>
    </fileDesc>
  </teiHeader>
  <text><body>{juans}</body></text>
</TEI>"""


def _write_mapping_multi(path: Path, kr_id: str, old_id: str) -> Path:
    path.write_text(
        "kr_id,kr_subsection,old_id,authorityID,json_key,title,category,alt\n"
        f"{kr_id},KR9x,{old_id},CA9999999,X999,Test,,\n",
        encoding="utf-8",
    )
    return path


def test_run_cbeta_multivolume_manifest_rebuilt(tmp_path: Path):
    """End-to-end: two-file multi-volume text; manifest covers all juans."""
    root = tmp_path / "cbeta"
    fa = root / "X" / "X81" / "X81n1571.xml"
    fb = root / "X" / "X82" / "X82n1571.xml"
    fa.parent.mkdir(parents=True)
    fb.parent.mkdir(parents=True)
    fa.write_text(_minimal_cbeta_xml("X81n1571", [1, 2]), encoding="utf-8")
    fb.write_text(_minimal_cbeta_xml("X82n1571", [3, 4]), encoding="utf-8")

    mapping = _write_mapping_multi(tmp_path / "mapping.csv", "KR9x0099", "X81n1571")
    out = tmp_path / "out"

    rc = run([
        "--format", "cbeta",
        "--in", str(root),
        "--mapping", str(mapping),
        "--text-id", "KR9x0099",
        "--out", str(out),
        "--yes",
    ])

    assert rc == 0
    bundle_root = out / "KR9x0099"
    manifest = yaml.safe_load(
        (bundle_root / "KR9x0099.manifest.yaml").read_text(encoding="utf-8")
    )
    parts = manifest["assets"]["parts"]
    juan_seqs = [p["seq"] for p in parts]
    assert juan_seqs == [1, 2, 3, 4]
