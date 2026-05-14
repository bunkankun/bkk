"""Bundle endpoints across non-flat corpus layouts.

Regression for the bug where `/bundles` (which uses ``discover_bundles``)
listed nested bundles but the per-textid endpoints assumed the flat layout
``<corpus>/<textid>/`` and 404'd on them.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from bkk.serve import create_app
from bkk.serve.config import ServeConfig

from .conftest import write_bundle


def _client(corpus_root: Path) -> TestClient:
    config = ServeConfig(
        corpus_root=corpus_root,
        index_path=corpus_root / "_corpus.bkkx",
    )
    return TestClient(create_app(config))


def test_three_level_layout_bundles_endpoints(tmp_path: Path):
    write_bundle(
        tmp_path,
        "KR1a0001",
        "甲乙丙丁戊",
        title="Nested",
        identifiers={"krp": "KR1a0001"},
        references=[{"filename": "src.txt", "role": "source"}],
        extra_files={
            "src.txt": "raw source",
            "KR1a0001_001.ann.yaml": yaml.safe_dump(
                {
                    "annotations": [
                        {"offset": 0, "length": 1, "concept": "甲"},
                    ]
                },
                allow_unicode=True,
            ),
        },
        subdir=Path("krp") / "KR1a",
    )
    client = _client(tmp_path)

    r = client.get("/bundles")
    assert r.status_code == 200
    assert {b["textid"] for b in r.json()["bundles"]} == {"KR1a0001"}

    r = client.get("/bundles/KR1a0001")
    assert r.status_code == 200
    assert r.json()["textid"] == "KR1a0001"

    r = client.get("/bundles/KR1a0001/manifest")
    assert r.status_code == 200
    assert r.json()["assets"]["parts"][0]["seq"] == 1

    r = client.get("/bundles/KR1a0001/juan")
    assert r.status_code == 200
    assert r.json()[0]["filename"] == "KR1a0001_001.yaml"

    r = client.get("/bundles/KR1a0001/juan/1")
    assert r.status_code == 200
    assert r.json()["body"]["text"] == "甲乙丙丁戊"

    r = client.get("/bundles/KR1a0001/juan/1/body/text")
    assert r.status_code == 200
    assert r.text == "甲乙丙丁戊"

    r = client.get("/bundles/KR1a0001/assets")
    assert r.status_code == 200
    assets = r.json()["assets"]
    assert len(assets) == 1 and assets[0]["name"] == "src.txt"

    r = client.get("/bundles/KR1a0001/assets/src.txt")
    assert r.status_code == 200
    assert r.content == b"raw source"


def test_three_level_layout_slice_endpoint(tmp_path: Path):
    write_bundle(
        tmp_path,
        "KR1a0001",
        "甲乙丙丁戊",
        subdir=Path("krp") / "KR1a",
    )
    client = _client(tmp_path)

    r = client.get("/bundles/KR1a0001/juan/1/slice?bucket=body&offset=1&length=3")
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "乙丙丁"
    assert body["span"] == [1, 4]


def test_three_level_layout_annotations_endpoint(tmp_path: Path):
    write_bundle(
        tmp_path,
        "KR1a0001",
        "甲乙丙丁戊",
        extra_files={
            "KR1a0001_001.ann.yaml": yaml.safe_dump(
                {
                    "annotations": [
                        {"offset": 0, "length": 1, "concept": "甲"},
                    ]
                },
                allow_unicode=True,
            ),
        },
        subdir=Path("krp") / "KR1a",
    )
    client = _client(tmp_path)

    r = client.get("/bundles/KR1a0001/juan/1/annotations")
    assert r.status_code == 200


def test_mixed_flat_and_three_level_layouts(tmp_path: Path):
    """A flat bundle and a nested bundle coexisting both resolve."""
    write_bundle(tmp_path, "FLAT0001", "ABCDE")
    write_bundle(
        tmp_path,
        "KR1a0001",
        "甲乙丙",
        subdir=Path("krp") / "KR1a",
    )
    client = _client(tmp_path)

    r = client.get("/bundles")
    assert {b["textid"] for b in r.json()["bundles"]} == {"FLAT0001", "KR1a0001"}

    assert client.get("/bundles/FLAT0001").status_code == 200
    assert client.get("/bundles/KR1a0001").status_code == 200
