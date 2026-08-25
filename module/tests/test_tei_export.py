"""TEI fragment export driven by CTF refs."""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import yaml
from lxml import etree

from bkk.exporter import cli as exporter_cli

TEI = "http://www.tei-c.org/ns/1.0"
BKK = "http://bunkankun.org/ns/1.0"
XML = "http://www.w3.org/XML/1998/namespace"
NS = {"tei": TEI, "bkk": BKK}


def _capture(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = exporter_cli.run(argv)
    return rc, out.getvalue(), err.getvalue()


def _write_bundle(
    corpus: Path,
    textid: str,
    text: str,
    markers: list[dict] | None = None,
) -> Path:
    bundle = corpus / textid
    bundle.mkdir(parents=True)
    (bundle / f"{textid}_001.yaml").write_text(
        yaml.safe_dump(
            {
                "canonical_identifier": f"bkk:test/{textid}/v1/juan/1",
                "seq": 1,
                "body": {
                    "text": text,
                    "hash": "sha256:body",
                    "markers": markers or [],
                },
                "hash": "sha256:juan",
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (bundle / f"{textid}.manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "canonical_identifier": f"bkk:test/{textid}/v1",
                "hash": "sha256:manifest",
                "metadata": {"title": textid, "edition": {"short": "bkk"}},
                "assets": {
                    "parts": [
                        {
                            "seq": 1,
                            "filename": f"{textid}_001.yaml",
                            "hash": "sha256:juan",
                        }
                    ]
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return bundle


def _parse(path: Path) -> etree._Element:
    return etree.fromstring(path.read_bytes())


def test_tei_export_resolves_ctf_yaml_span_and_renders_offsets_punctuation_and_notes(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr("bkk.config.load_rc", lambda: {})
    corpus = tmp_path / "corpus"
    ctf_root = tmp_path / "ctf"
    section = ctf_root / "KR1h"
    section.mkdir(parents=True)
    _write_bundle(
        corpus,
        "KR1h0001",
        "甲乙注丙",
        [
            {
                "type": "voice",
                "offset": 0,
                "length": 4,
                "name": "root",
                "id": "r1",
            },
            {
                "type": "voice",
                "offset": 2,
                "length": 1,
                "name": "commentary",
                "id": "c1",
                "responds-to": "r1",
            },
            {"type": "punctuation", "offset": 1, "content": "，"},
            {"type": "punctuation", "offset": 4, "content": "。"},
        ],
    )
    (section / "KR1h0001_001.ctf.yaml").write_text(
        yaml.safe_dump(
            {
                "kind": "bkk.ctf/v1",
                "textid": "KR1h0001",
                "seq": 1,
                "bucket": "body",
                "nodes": [
                    {
                        "id": "KR1h0001/1/1/@0+1",
                        "parent_id": "KR1h0001/1",
                        "label": "node",
                        "level": 1,
                        "span_ref": "KR1h0001/1/@0+4",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out"
    rc, _out, err = _capture([
        "--format", "tei",
        "--corpus", str(corpus),
        "--ctf-root", str(ctf_root),
        "--ctf", "KR1h0001/1/1/@0+1",
        "--output-dir", str(out_dir),
    ])

    assert rc == 0, err
    root = _parse(out_dir / "KR1h0001.tei.xml")
    assert root.tag == f"{{{TEI}}}div"
    assert root.get(f"{{{BKK}}}ref") == "KR1h0001/1/1/@0+1"

    segs = root.xpath(".//tei:seg", namespaces=NS)
    assert all(seg.get(f"{{{XML}}}id") for seg in segs)
    assert all(seg.get(f"{{{BKK}}}offset") is not None for seg in segs)
    assert len({seg.get(f"{{{XML}}}id") for seg in segs}) == len(segs)

    normal_text = "".join(
        seg.text or ""
        for seg in segs
        if not seg.xpath("ancestor::tei:note", namespaces=NS)
    )
    assert normal_text == "甲乙丙"

    punct = root.xpath(".//tei:c", namespaces=NS)
    assert [(c.get("n"), c.get(f"{{{BKK}}}offset")) for c in punct] == [
        ("，", "1"),
        ("。", "4"),
    ]

    note = root.xpath(".//tei:note", namespaces=NS)[0]
    assert note.get("type") == "commentary"
    assert note.get("corresp", "").startswith("#")
    target = note.get("corresp")[1:]
    assert target in {
        seg.get(f"{{{XML}}}id")
        for seg in segs
        if not seg.xpath("ancestor::tei:note", namespaces=NS)
    }
    assert "".join(note.xpath(".//tei:seg/text()", namespaces=NS)) == "注"


def test_tei_export_allows_repeated_ctf_refs_from_different_bundles(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr("bkk.config.load_rc", lambda: {})
    corpus = tmp_path / "corpus"
    _write_bundle(corpus, "KR1h0001", "甲乙")
    _write_bundle(corpus, "KR1h0002", "丙丁")

    out_dir = tmp_path / "out"
    rc, _out, err = _capture([
        "--format", "tei",
        "--corpus", str(corpus),
        "--ctf", "KR1h0001/1/@0+1",
        "--ctf", "KR1h0002/1/@1+1",
        "--output-dir", str(out_dir),
    ])

    assert rc == 0, err
    root = _parse(out_dir / "fragments.tei.xml")
    children = root.xpath("./tei:div", namespaces=NS)
    assert [child.get(f"{{{BKK}}}ref") for child in children] == [
        "KR1h0001/1/@0+1",
        "KR1h0002/1/@1+1",
    ]
    assert ["".join(child.xpath(".//tei:seg/text()", namespaces=NS)) for child in children] == [
        "甲",
        "丁",
    ]


def test_tei_export_accepts_recipe_ctf(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr("bkk.config.load_rc", lambda: {})
    corpus = tmp_path / "corpus"
    _write_bundle(corpus, "KR1h0001", "甲乙")
    out_dir = tmp_path / "out"
    recipe = tmp_path / "tei.yaml"
    recipe.write_text(
        yaml.safe_dump(
            {
                "format": "tei",
                "output_dir": str(out_dir),
                "ctf": ["KR1h0001/1/@0+2"],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    rc, _out, err = _capture([
        "--recipe", str(recipe),
        "--corpus", str(corpus),
    ])

    assert rc == 0, err
    root = _parse(out_dir / "KR1h0001.tei.xml")
    assert "".join(root.xpath(".//tei:seg/text()", namespaces=NS)) == "甲乙"


def test_tei_export_rejects_bundle_and_requires_ctf(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr("bkk.config.load_rc", lambda: {})
    corpus = tmp_path / "corpus"
    bundle = _write_bundle(corpus, "KR1h0001", "甲乙")

    rc, _out, err = _capture([
        "--format", "tei",
        "--corpus", str(corpus),
        "--bundle", str(bundle),
        "--ctf", "KR1h0001/1/@0+1",
        "--output-dir", str(tmp_path / "out"),
    ])
    assert rc == 2
    assert "does not use --bundle" in err

    rc, _out, err = _capture([
        "--format", "tei",
        "--corpus", str(corpus),
        "--output-dir", str(tmp_path / "out"),
    ])
    assert rc == 2
    assert "requires at least one --ctf" in err
