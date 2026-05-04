"""/catalog: curated whitelist filtering + recipe-shaped response."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bkk.serve import create_app
from bkk.serve.config import ServeConfig

from .conftest import write_bundle


@pytest.fixture
def rich_corpus(tmp_path: Path) -> Path:
    write_bundle(
        tmp_path,
        "TEXT_A",
        "甲",
        title="孟子",
        identifiers={"krp": "KR1h0001", "slug": ["mengzi"]},
        extra_metadata={
            "tags": {"kr-categories": ["KR1h"]},
            "authors": [{"name": "孟子"}],
            "composition_period": "戰國",
            "alt_titles": ["孟子注疏"],
            "source": {"name": "Kanripo"},
        },
    )
    write_bundle(
        tmp_path,
        "TEXT_B",
        "乙",
        title="論語",
        identifiers={"krp": "KR1h0002"},
        extra_metadata={
            "tags": {"kr-categories": ["KR1h"]},
            "authors": [{"name": "孔子"}],
            "composition_period": "春秋",
        },
    )
    write_bundle(
        tmp_path,
        "TEXT_C",
        "丙",
        title="說文解字",
        identifiers={"krp": "KR3a0001"},
        extra_metadata={
            "tags": {"kr-categories": ["KR3a"]},
            "authors": [{"name": "許慎"}],
        },
    )
    return tmp_path


@pytest.fixture
def cat_client(rich_corpus: Path) -> TestClient:
    config = ServeConfig(corpus_root=rich_corpus, index_path=rich_corpus / "_corpus.bkkx")
    return TestClient(create_app(config))


def test_catalog_no_filters_returns_all(cat_client: TestClient):
    r = cat_client.get("/catalog")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert {m["textid"] for m in body["matches"]} == {"TEXT_A", "TEXT_B", "TEXT_C"}
    assert "recipe" in body
    assert len(body["recipe"]["pins"]) == 3
    for pin in body["recipe"]["pins"]:
        assert pin["role"] == "match"


def test_catalog_filter_by_kr_category(cat_client: TestClient):
    r = cat_client.get("/catalog", params={"tags.kr-categories": "KR1h"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert {m["textid"] for m in body["matches"]} == {"TEXT_A", "TEXT_B"}


def test_catalog_filter_by_author(cat_client: TestClient):
    r = cat_client.get("/catalog", params={"authors.name": "孔子"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["matches"][0]["textid"] == "TEXT_B"


def test_catalog_filter_by_identifier(cat_client: TestClient):
    r = cat_client.get("/catalog", params={"metadata.identifiers.slug": "mengzi"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["matches"][0]["textid"] == "TEXT_A"


def test_catalog_and_across_keys(cat_client: TestClient):
    r = cat_client.get(
        "/catalog",
        params=[("tags.kr-categories", "KR1h"), ("authors.name", "孟子")],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["matches"][0]["textid"] == "TEXT_A"


def test_catalog_or_within_key(cat_client: TestClient):
    r = cat_client.get(
        "/catalog",
        params=[("authors.name", "孟子"), ("authors.name", "孔子")],
    )
    assert r.status_code == 200
    body = r.json()
    assert {m["textid"] for m in body["matches"]} == {"TEXT_A", "TEXT_B"}


def test_catalog_unknown_filter_400(cat_client: TestClient):
    r = cat_client.get("/catalog", params={"not_a_real_key": "x"})
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "unknown_filter_keys"
    assert "not_a_real_key" in body["unknown"]
    assert "title" in body["allowed"]


def test_catalog_pagination(cat_client: TestClient):
    r = cat_client.get("/catalog", params={"limit": 2, "offset": 0})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert body["next_offset"] == 2
    assert len(body["matches"]) == 2

    r2 = cat_client.get("/catalog", params={"limit": 2, "offset": 2})
    body2 = r2.json()
    assert body2["next_offset"] is None
    assert len(body2["matches"]) == 1
