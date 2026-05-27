"""Sub-juan endpoints (bucket, text, markers) and bundle assets."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bkk.serve import create_app
from bkk.serve.config import ServeConfig

from .conftest import write_bundle


@pytest.fixture
def assets_corpus(tmp_path: Path) -> Path:
    write_bundle(
        tmp_path,
        "AST0001",
        "甲乙丙丁",
        title="With Assets",
        references=[
            {"filename": "PUA-map.yaml", "role": "pua-map", "hash": "sha256:1"},
            {"filename": "notes.md", "role": "notes", "hash": "sha256:2"},
        ],
        extra_files={
            "PUA-map.yaml": "x: 1\n",
            "notes.md": "# Notes\n",
        },
    )
    return tmp_path


@pytest.fixture
def assets_client(assets_corpus: Path) -> TestClient:
    config = ServeConfig(
        corpus_root=assets_corpus, index_path=assets_corpus / "_corpus.bkkx"
    )
    return TestClient(create_app(config))


def test_juan_bucket_body(client: TestClient):
    r = client.get("/bundles/TEST0001/juan/1/body")
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "甲乙丙丁戊己庚辛壬癸"


def test_juan_bucket_invalid(client: TestClient):
    r = client.get("/bundles/TEST0001/juan/1/sideways")
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "bad_bucket"
    assert body["bucket"] == "sideways"


def test_juan_bucket_text_is_plaintext(client: TestClient):
    r = client.get("/bundles/TEST0001/juan/1/body/text")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text == "甲乙丙丁戊己庚辛壬癸"


def test_juan_markers_filtering(tmp_path: Path):
    # Build a synthetic bundle with several typed markers + master_offsets.
    write_bundle(
        tmp_path,
        "MKR0001",
        "abcdefghij",
        variants=[
            {"master_offset": 0, "length": 1, "content": "A", "witness": "X", "witness_form": "A"},
            {"master_offset": 5, "length": 1, "content": "F", "witness": "X", "witness_form": "F"},
        ],
    )
    config = ServeConfig(corpus_root=tmp_path, index_path=tmp_path / "_corpus.bkkx")
    client = TestClient(create_app(config))

    r = client.get("/bundles/MKR0001/juan/1/body/markers")
    assert r.status_code == 200
    assert len(r.json()) == 2

    r = client.get("/bundles/MKR0001/juan/1/body/markers", params={"type": "variant"})
    assert len(r.json()) == 2

    r = client.get(
        "/bundles/MKR0001/juan/1/body/markers", params={"from": 1, "to": 9}
    )
    assert [m["master_offset"] for m in r.json()] == [5]


def test_list_assets(assets_client: TestClient):
    r = assets_client.get("/bundles/AST0001/assets")
    assert r.status_code == 200
    body = r.json()
    assert body["textid"] == "AST0001"
    assert {a["name"] for a in body["assets"]} == {"PUA-map.yaml", "notes.md"}
    by_name = {a["name"]: a for a in body["assets"]}
    assert by_name["PUA-map.yaml"]["role"] == "pua-map"
    assert by_name["PUA-map.yaml"]["size"] is not None


def test_get_asset(assets_client: TestClient):
    r = assets_client.get("/bundles/AST0001/assets/PUA-map.yaml")
    assert r.status_code == 200
    assert r.text == "x: 1\n"


def test_get_asset_not_declared(assets_client: TestClient):
    r = assets_client.get("/bundles/AST0001/assets/secrets.txt")
    assert r.status_code == 400
    assert r.json()["error"] == "asset_not_declared"


def test_get_local_file_backed_image(tmp_path: Path):
    image_root = tmp_path / "images"
    image_path = image_root / "WYG0015" / "WYG0015-0754c.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    write_bundle(
        tmp_path,
        "IMG0001",
        "甲乙丙",
        extra_metadata={"image_base_urls": {"WYG": image_root.as_uri() + "/"}},
    )
    client = TestClient(
        create_app(
            ServeConfig(
                corpus_root=tmp_path,
                index_path=tmp_path / "_corpus.bkkx",
            )
        )
    )

    r = client.get("/bundles/IMG0001/images/WYG/WYG0015/WYG0015-0754c.png")
    assert r.status_code == 200
    assert r.content == b"\x89PNG\r\n\x1a\n"


def test_get_local_file_backed_image_rejects_traversal(tmp_path: Path):
    image_root = tmp_path / "images"
    image_root.mkdir()
    write_bundle(
        tmp_path,
        "IMG0002",
        "甲乙丙",
        extra_metadata={"image_base_urls": {"WYG": image_root.as_uri() + "/"}},
    )
    client = TestClient(
        create_app(
            ServeConfig(
                corpus_root=tmp_path,
                index_path=tmp_path / "_corpus.bkkx",
            )
        )
    )

    r = client.get("/bundles/IMG0002/images/WYG/%2E%2E/secret.png")
    assert r.status_code == 400
    assert r.json()["error"] == "bad_image_path"
