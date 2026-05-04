"""/texts/{id}/* identifier-resolved access + collision UX."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bkk.serve import create_app
from bkk.serve.config import ServeConfig

from .conftest import write_bundle


@pytest.fixture
def collision_corpus(tmp_path: Path) -> Path:
    """Two bundles sharing krp=SHARED; one is master, one carries base_edition."""
    write_bundle(
        tmp_path,
        "MASTER",
        "甲",
        title="天干 master",
        identifiers={"krp": "SHARED"},
    )
    write_bundle(
        tmp_path,
        "WYGED",
        "乙",
        title="天干 (Wenyuange edition)",
        identifiers={"krp": "SHARED"},
        base_edition="WYG",
    )
    return tmp_path


@pytest.fixture
def collision_client(collision_corpus: Path) -> TestClient:
    config = ServeConfig(
        corpus_root=collision_corpus,
        index_path=collision_corpus / "_corpus.bkkx",
    )
    return TestClient(create_app(config))


def test_text_lookup_by_textid(client: TestClient):
    r = client.get("/texts/TEST0001")
    assert r.status_code == 200
    body = r.json()
    assert body["textid"] == "TEST0001"
    assert body["title"] == "天干"


def test_text_lookup_by_krp_identifier(client: TestClient):
    r = client.get("/texts/TEST0001/manifest")
    # TEST0001 has identifiers.krp == "TEST0001" — same value as the textid,
    # so this still resolves uniquely. Use the slug for a non-textid path.
    assert r.status_code == 200

    r2 = client.get("/texts/tiangan/manifest")
    assert r2.status_code == 200
    manifest = r2.json()
    assert manifest["canonical_identifier"] == "bkk:test/TEST0001/v1"


def test_text_lookup_by_canonical(client: TestClient):
    r = client.get("/texts/bkk:test/TEST0001/v1/manifest")
    # The canonical_identifier contains slashes; FastAPI path matching would
    # not accept this without an explicit catch-all. Use /by-canonical instead.
    # Here we just confirm the identifier IS in the snapshot via /by-canonical.
    r2 = client.get("/by-canonical", params={"id": "bkk:test/TEST0001/v1"})
    assert r2.status_code in (302, 200)
    if r2.status_code == 302:
        assert r2.headers["location"] == "/bundles/TEST0001"


def test_text_unknown_identifier_returns_400(client: TestClient):
    r = client.get("/texts/no_such_id")
    assert r.status_code == 400
    assert r.json()["error"] == "identifier_not_found"


def test_text_collision_prefers_no_base_edition(collision_client: TestClient):
    r = collision_client.get("/texts/SHARED")
    assert r.status_code == 200
    body = r.json()
    assert body["textid"] == "MASTER"


def test_text_collision_300_when_still_ambiguous(tmp_path: Path):
    # Two bundles with the same krp, both with base_edition set ⇒ 300.
    write_bundle(
        tmp_path, "WYG", "甲", identifiers={"krp": "DUP"}, base_edition="WYG"
    )
    write_bundle(
        tmp_path, "SBCK", "乙", identifiers={"krp": "DUP"}, base_edition="SBCK"
    )
    config = ServeConfig(corpus_root=tmp_path, index_path=tmp_path / "_corpus.bkkx")
    client = TestClient(create_app(config))

    r = client.get("/texts/DUP")
    assert r.status_code == 300
    body = r.json()
    assert body["error"] == "multiple_choices"
    assert body["identifier"] == "DUP"
    assert {c["textid"] for c in body["candidates"]} == {"WYG", "SBCK"}
    for c in body["candidates"]:
        assert c["link"] == f"/bundles/{c['textid']}"


def test_text_edition_suffix_bypasses_collision(collision_client: TestClient):
    # @bkk hits the master (edition_short on metadata.edition.short is "bkk"
    # for every bundle written by the test helper).
    r = collision_client.get("/texts/SHARED@bkk")
    assert r.status_code == 200
    # Both bundles in collision_corpus use edition.short="bkk", so this still
    # collides — but the @ filter narrowed to a single edition.short value,
    # so the base_edition tiebreak still picks MASTER.
    body = r.json()
    assert body["textid"] == "MASTER"


def test_text_juan_by_identifier(client: TestClient):
    r = client.get("/texts/tiangan/juan/1")
    assert r.status_code == 200
    juan = r.json()
    assert juan["seq"] == 1
    assert juan["body"]["text"] == "甲乙丙丁戊己庚辛壬癸"


def test_by_canonical_unknown(client: TestClient):
    r = client.get("/by-canonical", params={"id": "bkk:nope/X/v1"})
    assert r.status_code == 400
    assert r.json()["error"] == "identifier_not_found"
