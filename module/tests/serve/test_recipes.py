"""POST /recipes:fulfil — recipe-as-request handler."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from bkk.serve import create_app
from bkk.serve.config import ServeConfig

from .conftest import write_bundle


@pytest.fixture
def recipe_corpus(tmp_path: Path) -> Path:
    write_bundle(
        tmp_path,
        "RCP0001",
        "甲乙丙丁戊己庚辛壬癸",
        title="天干",
        identifiers={"krp": "RCP0001"},
        canonical_identifier="bkk:test/RCP0001/v1",
        variants=[
            {"id": "m_start", "offset": 0},
            {"id": "m_end", "offset": 10},
        ],
        manifest_hash="sha256:rcp0001",
    )
    write_bundle(
        tmp_path,
        "RCP0002",
        "ABCDEFGHIJ",
        title="Latin",
        canonical_identifier="bkk:test/RCP0002/v1",
        manifest_hash="sha256:rcp0002",
    )
    write_bundle(
        tmp_path,
        "KR1h0004",
        "abcdefghij",
        title="Short Ref",
        canonical_identifier="bkk:test/KR1h0004/v1",
        manifest_hash="sha256:kr1h0004",
    )
    return tmp_path


@pytest.fixture
def recipe_client(recipe_corpus: Path) -> TestClient:
    config = ServeConfig(
        corpus_root=recipe_corpus,
        index_path=recipe_corpus / "_corpus.bkkx",
    )
    return TestClient(create_app(config))


def test_fulfil_whole_bundle(recipe_client):
    body = {"pins": [{"role": "base", "textid": "RCP0001"}]}
    r = recipe_client.post("/recipes:fulfil", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["errors"] == []
    assert len(data["results"]) == 1
    res = data["results"][0]
    assert res["verified"] is True
    assert res["role"] == "base"
    assert isinstance(res["content"], list)
    assert res["content"][0]["text"] == "甲乙丙丁戊己庚辛壬癸"
    assert data["resolved_recipe"]["pins"][0]["canonical_identifier"]


def test_fulfil_by_canonical(recipe_client):
    body = {
        "pins": [
            {
                "role": "base",
                "canonical_identifier": "bkk:test/RCP0002/v1",
                "selection": {"juan": 1, "offset": 0, "length": 5},
            }
        ]
    }
    r = recipe_client.post("/recipes:fulfil", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["errors"] == []
    res = data["results"][0]
    assert res["textid"] == "RCP0002"
    assert res["content"]["text"] == "ABCDE"


def test_fulfil_marker_range(recipe_client):
    body = {
        "pins": [
            {
                "role": "base",
                "textid": "RCP0001",
                "selection": {"juan": 1, "from": "m_start", "to": "m_end"},
            }
        ]
    }
    r = recipe_client.post("/recipes:fulfil", json=body)
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["content"]["text"] == "甲乙丙丁戊己庚辛壬癸"


def test_fulfil_short_ref_expands_to_textid_and_selection(recipe_client):
    body = {"pins": [{"role": "base", "ref": "1h4/1/@2+3"}]}
    r = recipe_client.post("/recipes:fulfil", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["errors"] == []
    res = data["results"][0]
    assert res["textid"] == "KR1h0004"
    assert res["selection"] == {"juan": 1, "offset": 2, "length": 3}
    assert res["content"]["text"] == "cde"
    resolved_pin = data["resolved_recipe"]["pins"][0]
    assert "ref" not in resolved_pin
    assert resolved_pin["textid"] == "KR1h0004"
    assert resolved_pin["selection"] == {"juan": 1, "offset": 2, "length": 3}


def test_fulfil_short_ref_requires_explicit_non_body_bucket(recipe_client):
    body = {"pins": [{"role": "base", "ref": "1h4/1/front@0+1"}]}
    r = recipe_client.post("/recipes:fulfil", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["results"][0]["selection"] == {
        "juan": 1,
        "bucket": "front",
        "offset": 0,
        "length": 1,
    }
    assert data["errors"][0]["error"] == "bad_slice_range"


def test_fulfil_unresolved_pin(recipe_client):
    body = {"pins": [{"role": "base", "textid": "MISSING"}]}
    r = recipe_client.post("/recipes:fulfil", json=body)
    assert r.status_code == 200
    data = r.json()
    assert len(data["errors"]) == 1
    assert data["errors"][0]["error"] == "pin_textid_not_found"
    assert data["results"][0]["content"] is None
    assert data["results"][0]["verified"] is False


def test_fulfil_hash_mismatch(recipe_client):
    body = {
        "pins": [
            {"role": "base", "textid": "RCP0001", "hash": "sha256:wrong"}
        ]
    }
    r = recipe_client.post("/recipes:fulfil", json=body)
    assert r.status_code == 200
    data = r.json()
    assert any(e["error"] == "hash_mismatch" for e in data["errors"])
    assert data["results"][0]["verified"] is False
    assert data["results"][0]["content"] is None


def test_fulfil_yaml_body(recipe_client):
    body = yaml.safe_dump(
        {"pins": [{"role": "base", "textid": "RCP0001",
                   "selection": {"juan": 1, "offset": 0, "length": 3}}]}
    )
    r = recipe_client.post(
        "/recipes:fulfil",
        content=body,
        headers={"Content-Type": "application/yaml"},
    )
    assert r.status_code == 200
    res = r.json()["results"][0]
    assert res["content"]["text"] == "甲乙丙"


def test_fulfil_bad_body(recipe_client):
    r = recipe_client.post(
        "/recipes:fulfil",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "bad_request_body"


def test_fulfil_empty_body(recipe_client):
    r = recipe_client.post(
        "/recipes:fulfil",
        content=b"",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "empty_body"


def test_fulfil_recipe_invalid_shape(recipe_client):
    r = recipe_client.post("/recipes:fulfil", json={"foo": "bar"})
    assert r.status_code == 400
    assert r.json()["error"] == "recipe_invalid"


def test_fulfil_multiple_pins(recipe_client):
    body = {
        "pins": [
            {"role": "base", "textid": "RCP0001",
             "selection": {"juan": 1, "offset": 0, "length": 2}},
            {"role": "compare", "textid": "RCP0002",
             "selection": {"juan": 1, "offset": 0, "length": 2}},
        ]
    }
    r = recipe_client.post("/recipes:fulfil", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["errors"] == []
    assert [res["role"] for res in data["results"]] == ["base", "compare"]
    assert data["results"][0]["content"]["text"] == "甲乙"
    assert data["results"][1]["content"]["text"] == "AB"


def test_fulfil_per_pin_slice_error(recipe_client):
    body = {
        "pins": [
            {"role": "base", "textid": "RCP0001",
             "selection": {"juan": 1, "offset": 100, "length": 5}},
        ]
    }
    r = recipe_client.post("/recipes:fulfil", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["results"][0]["content"] is None
    assert any(e["error"] == "bad_slice_range" for e in data["errors"])
