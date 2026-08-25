"""DTS endpoints backed by catalog rows and CTF navigation."""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from lxml import etree

from bkk.index import build_catalog_index
from bkk.serve import create_app
from bkk.serve.config import ServeConfig

from .conftest import write_bundle
from .test_catalog import _write_frontmatter

TEI_NS = "http://www.tei-c.org/ns/1.0"
DTS_NS = "https://w3id.org/api/dts#"
BKK_NS = "http://bunkankun.org/ns/1.0"
NS = {"tei": TEI_NS, "dts": DTS_NS, "bkk": BKK_NS}


def _dts_client(tmp_path: Path) -> TestClient:
    bundle = write_bundle(
        tmp_path,
        "KR1h0001",
        "甲乙丙丁戊己庚辛",
        title="Manifest Title",
        identifiers={"krp": "KR1h0001"},
    )
    juan_path = bundle / "KR1h0001_001.yaml"
    juan = yaml.safe_load(juan_path.read_text(encoding="utf-8"))
    juan["body"]["markers"] = [
        {"type": "punctuation", "offset": 2, "content": "，"},
    ]
    juan_path.write_text(yaml.safe_dump(juan, allow_unicode=True), encoding="utf-8")
    write_bundle(
        tmp_path,
        "KR1h0002",
        "abcdefghij",
        title="Second Manifest Title",
        identifiers={"krp": "KR1h0002"},
    )
    csv_path = _write_frontmatter(
        tmp_path / "frontmatter.csv",
        [
            {
                "id": "KR1h",
                "title": "四書類",
                "titlePinyin": "Sishu lei",
                "titleEnglish": "Four Books",
            },
            {
                "id": "KR1h0001",
                "title": "目錄題一",
                "titlePinyin": "Mulu ti yi",
                "titleEnglish": "Catalog Title One",
                "notBefore": "1",
                "notAfter": "2",
            },
            {
                "id": "KR1h0002",
                "title": "目錄題二",
                "titlePinyin": "Mulu ti er",
                "titleEnglish": "Catalog Title Two",
                "notBefore": "3",
                "notAfter": "4",
            },
        ],
    )
    catalog_path = build_catalog_index(tmp_path, csv_path, tmp_path / "_catalog.bkkc")

    ctf_root = tmp_path / "ctf"
    section = ctf_root / "KR1h"
    section.mkdir(parents=True)
    (section / "KR1h0001.ctf.tsv").write_text(
        "\n".join([
            "id\tparent_id\tlabel\tend",
            "KR1h0001\tKR1h\t目錄題一\t",
            "KR1h0001/1\tKR1h0001\t卷一\t",
            "KR1h0001/1/1/@0+2\tKR1h0001/1\t甲乙\t4",
            "KR1h0001/1/2/@4+2\tKR1h0001/1\t戊己\t8",
            "",
        ]),
        encoding="utf-8",
    )
    (section / "KR1h0002_001.ctf.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "bkk.ctf/v1",
                "textid": "KR1h0002",
                "seq": 1,
                "bucket": "body",
                "nodes": [
                    {
                        "id": "KR1h0002/1",
                        "parent_id": "KR1h0002",
                        "label": "卷一",
                        "level": 0,
                    },
                    {
                        "id": "KR1h0002/1/1/@2+2",
                        "parent_id": "KR1h0002/1",
                        "label": "cd",
                        "level": 1,
                        "span_ref": "KR1h0002/1/@2+4",
                    },
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    app = create_app(ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
        catalog_path=catalog_path,
        ctf_root=ctf_root,
    ))
    return TestClient(app)


def test_dts_entrypoint_and_collection_hierarchy(tmp_path: Path) -> None:
    client = _dts_client(tmp_path)

    entry = client.get("/dts").json()
    assert entry["@type"] == "EntryPoint"
    assert entry["collection"].startswith("http://testserver/api/dts/collection")

    root = client.get("/dts/collection").json()
    kr1 = next(member for member in root["member"] if member["@id"] == "KR1")
    assert kr1["title"] == "Classics"
    assert kr1["extensions"]["bkk:bundleCount"] == 2

    section = client.get("/dts/collection", params={"id": "KR1h"}).json()
    assert section["title"] == "Four Books"
    assert [member["@id"] for member in section["member"]] == [
        "KR1h0001",
        "KR1h0002",
    ]
    assert section["member"][0]["title"] == "目錄題一"
    assert section["member"][0]["extensions"]["bkk:titlePinyin"] == "Mulu ti yi"

    parents = client.get(
        "/dts/collection",
        params={"id": "KR1h0001", "nav": "parents"},
    ).json()
    assert parents["@type"] == "Resource"
    assert parents["member"][0]["@id"] == "KR1h"


def test_dts_navigation_from_tsv_ctf(tmp_path: Path) -> None:
    client = _dts_client(tmp_path)

    top = client.get("/dts/navigation", params={"resource": "KR1h0001"}).json()
    assert [member["identifier"] for member in top["member"]] == ["KR1h0001/1"]

    all_nodes = client.get(
        "/dts/navigation",
        params={"resource": "KR1h0001", "down": -1},
    ).json()
    assert [member["identifier"] for member in all_nodes["member"]] == [
        "KR1h0001/1",
        "KR1h0001/1/1/@0+2",
        "KR1h0001/1/2/@4+2",
    ]
    assert all_nodes["member"][1]["extensions"]["bkk:end"] == 4

    ref = client.get(
        "/dts/navigation",
        params={"resource": "KR1h0001", "ref": "KR1h0001/1/1/@0+2"},
    ).json()
    assert ref["ref"]["identifier"] == "KR1h0001/1/1/@0+2"
    assert ref["member"][0]["identifier"] == "KR1h0001/1/1/@0+2"


def test_dts_document_fragments_and_media_types(tmp_path: Path) -> None:
    client = _dts_client(tmp_path)

    tei = client.get(
        "/dts/document",
        params={"resource": "KR1h0001", "ref": "KR1h0001/1/1/@0+2"},
    )
    assert tei.status_code == 200
    assert tei.headers["content-type"].startswith("application/tei+xml")
    assert '<dts:wrapper xmlns:dts="https://w3id.org/api/dts#"' in tei.text
    root = etree.fromstring(tei.content)
    wrapper = root.xpath(".//dts:wrapper", namespaces=NS)[0]
    assert wrapper.get("ref") == "KR1h0001/1/1/@0+2"
    div = wrapper.xpath("./tei:div", namespaces=NS)[0]
    assert div.get(f"{{{BKK_NS}}}ref") == "KR1h0001/1/1/@0+2"
    assert "".join(div.xpath(".//tei:seg/text()", namespaces=NS)) == "甲乙丙丁"
    assert div.xpath(".//tei:c/@n", namespaces=NS) == ["，"]

    plain = client.get(
        "/dts/document",
        params={
            "resource": "KR1h0001",
            "ref": "KR1h0001/1/2/@4+2",
            "mediaType": "text/plain",
        },
    )
    assert plain.text == "戊己庚辛"

    whole = client.get(
        "/dts/document",
        params={"resource": "KR1h0001", "mediaType": "text/plain"},
    )
    assert whole.text == "甲乙丙丁戊己庚辛"

    whole_tei = client.get(
        "/dts/document",
        params={"resource": "KR1h0001", "ref": "KR1h0001"},
    )
    root = etree.fromstring(whole_tei.content)
    wrapper = root.xpath(".//dts:wrapper", namespaces=NS)[0]
    assert wrapper.get("ref") == "KR1h0001"
    div = wrapper.xpath("./tei:div", namespaces=NS)[0]
    assert div.get(f"{{{BKK_NS}}}ref") == "KR1h0001/1"
    assert "".join(div.xpath(".//tei:seg/text()", namespaces=NS)) == "甲乙丙丁戊己庚辛"
    assert div.xpath(".//tei:c/@n", namespaces=NS) == ["，"]


def test_dts_reads_per_juan_yaml_ctf(tmp_path: Path) -> None:
    client = _dts_client(tmp_path)

    nav = client.get(
        "/dts/navigation",
        params={"resource": "KR1h0002", "down": -1},
    ).json()
    assert [member["identifier"] for member in nav["member"]] == [
        "KR1h0002/1",
        "KR1h0002/1/1/@2+2",
    ]

    doc = client.get(
        "/dts/document",
        params={
            "resource": "KR1h0002",
            "ref": "KR1h0002/1/1/@2+2",
            "mediaType": "text/plain",
        },
    )
    assert doc.text == "cdef"


def test_dts_rejects_unsupported_ranges_and_media_types(tmp_path: Path) -> None:
    client = _dts_client(tmp_path)

    nav = client.get(
        "/dts/navigation",
        params={"resource": "KR1h0001", "start": "a", "end": "b"},
    )
    assert nav.status_code == 400
    assert nav.json()["error"] == "dts_range_navigation_unsupported"

    doc = client.get(
        "/dts/document",
        params={"resource": "KR1h0001", "mediaType": "application/json"},
    )
    assert doc.status_code == 404
    assert doc.json()["error"] == "dts_media_type_not_available"
