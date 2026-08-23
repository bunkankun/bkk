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


def test_direct_reader_emits_xml_head_voice_markers_with_paths(tmp_path: Path):
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
      <cb:div>
        <cb:mulu level="1" type="其他">大章</cb:mulu><head>大章</head><p>甲</p>
        <cb:div>
          <cb:mulu level="2" type="其他">小節</cb:mulu><head>小節</head><p>乙</p>
        </cb:div>
        <cb:mulu level="1" type="其他">次章</cb:mulu><head>次章</head><p>丙</p>
      </cb:div>
      <cb:juan fun="close" n="1"/>
      <cb:juan fun="open" n="2"/>
      <cb:div>
        <cb:mulu level="1" type="其他">後章</cb:mulu><head>後章</head><p>丁</p>
      </cb:div>
    </body>
  </text>
</TEI>
""",
        encoding="utf-8",
    )

    bundle = read_cbeta(
        xml,
        {"kr_id": "KR9x0001", "old_id": "T01n0001", "title": ""},
    )

    first_heads = [
        marker for marker in bundle.juans[0].sections[0].markers
        if marker.type == "voice" and marker.extras.get("name") == "head"
    ]
    assert [
        (m.offset, m.extras["length"], m.id, m.extras["source"], m.extras["path"])
        for m in first_heads
    ] == [
        (0, 2, "h1", "xml", [1]),
        (3, 2, "h2", "xml", [1, 1]),
        (6, 2, "h3", "xml", [2]),
    ]
    assert [
        marker.extras.get("mulu_type")
        for marker in bundle.juans[0].sections[0].markers
        if marker.type == "cbeta:mulu"
    ] == ["其他", "其他", "其他"]

    second_heads = [
        marker for marker in bundle.juans[1].sections[0].markers
        if marker.type == "voice" and marker.extras.get("name") == "head"
    ]
    assert [(m.id, m.extras["path"]) for m in second_heads] == [("h1", [1])]


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


def test_cli_writes_cbeta_xml_heads_to_marker_asset_not_manifest(tmp_path: Path):
    cbeta_root = tmp_path / "cbeta"
    target = cbeta_root / "T" / "T01" / "T01n0001.xml"
    target.parent.mkdir(parents=True)
    target.write_text(
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
      <cb:div>
        <cb:mulu level="1" type="其他">大章</cb:mulu><head>大章</head><p>甲</p>
      </cb:div>
    </body>
  </text>
</TEI>
""",
        encoding="utf-8",
    )
    mapping = _write_mapping(
        tmp_path / "mapping.csv",
        kr_id="KR9x0001",
        old_id="T01n0001",
    )
    out = tmp_path / "out"

    rc = run([
        "--format", "cbeta",
        "--in", str(cbeta_root),
        "--mapping", str(mapping),
        "--text-id", "T01n0001",
        "--out", str(out),
    ])

    assert rc == 0
    bundle_root = out / "KR9x0001"
    manifest = yaml.safe_load(
        (bundle_root / "KR9x0001.manifest.yaml").read_text(encoding="utf-8")
    )
    assert all(entry["type"] != "head" for entry in manifest["table_of_contents"])
    assert [entry["type"] for entry in manifest["table_of_contents"]] == [
        "juan",
        "mulu",
    ]

    juan = yaml.safe_load(
        (bundle_root / "KR9x0001_001.yaml").read_text(encoding="utf-8")
    )
    assert [
        marker for marker in juan["body"]["markers"]
        if marker.get("type") == "voice"
    ] == []

    asset = load_marker_asset(bundle_root, manifest, 1)
    heads = [
        marker for marker in asset["markers"]["body"]
        if marker.get("type") == "voice" and marker.get("name") == "head"
    ]
    assert heads == [
        {
            "type": "voice",
            "offset": 0,
            "id": "h1",
            "length": 2,
            "name": "head",
            "source": "xml",
            "path": [1],
        }
    ]
    assert [
        marker for marker in juan["body"]["markers"]
        if marker.get("id") == "KR9x0001_T_001-mulu-1"
    ] == [
        {
            "type": "cbeta:mulu",
            "offset": 0,
            "content": "大章",
            "id": "KR9x0001_T_001-mulu-1",
            "level": "1",
            "mulu_type": "其他",
        }
    ]


def test_on_exists_overwrite_replaces_unknown_cbeta_bundle(tmp_path: Path):
    cbeta_root = tmp_path / "cbeta"
    target = cbeta_root / "T" / "T01" / "T01n0001.xml"
    target.parent.mkdir(parents=True)
    target.write_text(_minimal_cbeta_xml("T01n0001", [1]), encoding="utf-8")
    mapping = _write_mapping(
        tmp_path / "mapping.csv",
        kr_id="KR9x0001",
        old_id="T01n0001",
    )
    out = tmp_path / "out"
    unknown = out / "KR9x0001"
    unknown.mkdir(parents=True)
    (unknown / "KR9x0001.manifest.yaml").write_text(
        "canonical_identifier: bkk:krp/KR9x0001/v1\nmetadata: {}\n",
        encoding="utf-8",
    )

    rc = run([
        "--format", "cbeta",
        "--in", str(cbeta_root),
        "--mapping", str(mapping),
        "--text-id", "T01n0001",
        "--out", str(out),
        "--on-exists", "overwrite",
    ])

    assert rc == 0
    manifest = yaml.safe_load(
        (unknown / "KR9x0001.manifest.yaml").read_text(encoding="utf-8")
    )
    assert manifest["metadata"]["identifiers"]["cbeta"] == "T01n0001"
    assert (unknown / "KR9x0001.source.yaml").is_file()


def test_on_exists_wipe_before_overwrite_removes_stale_cbeta_files(
    tmp_path: Path,
):
    cbeta_root = tmp_path / "cbeta"
    target = cbeta_root / "T" / "T01" / "T01n0001.xml"
    target.parent.mkdir(parents=True)
    target.write_text(_minimal_cbeta_xml("T01n0001", [1]), encoding="utf-8")
    mapping = _write_mapping(
        tmp_path / "mapping.csv",
        kr_id="KR9x0001",
        old_id="T01n0001",
    )
    out = tmp_path / "out"
    args = [
        "--format", "cbeta",
        "--in", str(cbeta_root),
        "--mapping", str(mapping),
        "--text-id", "T01n0001",
        "--out", str(out),
    ]
    assert run(args) == 0

    bundle_root = out / "KR9x0001"
    git_dir = bundle_root / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/master\n", encoding="utf-8")
    gitignore = bundle_root / ".gitignore"
    gitignore.write_text("ignored\n", encoding="utf-8")
    stale = bundle_root / "KR9x0001_999.yaml"
    stale.write_text("stale", encoding="utf-8")
    stale_asset = bundle_root / "assets" / "KR9x0001_999.markers.yaml"
    stale_asset.parent.mkdir(exist_ok=True)
    stale_asset.write_text("stale", encoding="utf-8")

    rc = run(args + ["--on-exists", "wipe-before-overwrite"])

    assert rc == 0
    assert git_dir.is_dir()
    assert (git_dir / "HEAD").read_text(encoding="utf-8") == (
        "ref: refs/heads/master\n"
    )
    assert gitignore.read_text(encoding="utf-8") == "ignored\n"
    assert not stale.exists()
    assert not stale_asset.exists()
    assert (bundle_root / "KR9x0001_001.yaml").is_file()


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
    # Primary volume's identifier must survive companion-volume import.
    identifiers = manifest["metadata"]["identifiers"]
    assert identifiers["cbeta"] == "X81n1571"
    assert identifiers["cbeta_old_id"] == "X81n1571"


def _minimal_cbeta_xml_milestone(xml_id: str, juan_nums: list[int]) -> str:
    """Build a minimal CBETA XML file that uses <milestone unit="juan">
    instead of <cb:juan> for juan boundaries (LC/TX/GA collections)."""
    juans = "\n".join(
        f'<milestone unit="juan" n="{n}"/><p>文{n}</p>'
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


def test_milestone_juan_splits_into_separate_juans(tmp_path: Path):
    """Files using <milestone unit="juan"> (LC/TX/GA style) must produce one
    BKK juan per milestone, not one giant juan_000."""
    xml_path = tmp_path / "LC03n0003.xml"
    xml_path.write_text(
        _minimal_cbeta_xml_milestone("LC03n0003", [1, 2, 3]),
        encoding="utf-8",
    )
    row = {
        "kr_id": "KR6v0553", "old_id": "LC03n0003",
        "kr_subsection": "KR6v", "authorityID": "", "json_key": "",
        "title": "", "category": "", "alt": "",
    }
    bundle = read_cbeta(xml_path, row)
    assert len(bundle.juans) == 3
    assert [j.seq for j in bundle.juans] == [1, 2, 3]
