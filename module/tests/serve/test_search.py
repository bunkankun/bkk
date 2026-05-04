"""Endpoint /search wraps bkk.index.Index."""

from __future__ import annotations


def test_search_master_hit(client):
    r = client.get("/search", params={"q": "丙丁"})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "丙丁"
    assert body["total"] >= 1
    hit = body["hits"][0]
    assert hit["textid"] == "TEST0001"
    assert hit["bucket"] == "body"
    assert hit["match"] == "丙丁"
    assert hit["matched_via"] == "master"


def test_search_no_match(client):
    r = client.get("/search", params={"q": "未存在"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["hits"] == []


def test_search_textid_scope(client):
    # Substring exists in TEST0002 but we restrict to TEST0001.
    r = client.get("/search", params={"q": "DEF", "textid": "TEST0001"})
    assert r.status_code == 200
    assert r.json()["total"] == 0

    r = client.get("/search", params={"q": "DEF", "textid": "TEST0002"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert all(h["textid"] == "TEST0002" for h in body["hits"])


def test_search_hit_carries_recipe(client):
    r = client.get("/search", params={"q": "丙丁"})
    assert r.status_code == 200
    hit = r.json()["hits"][0]
    recipe = hit["recipe"]
    assert recipe["pins"][0]["textid"] == hit["textid"]
    sel = recipe["pins"][0]["selection"]
    assert sel["juan"] == hit["juan_seq"]
    assert sel["bucket"] == hit["bucket"]
    assert sel["offset"] == hit["master_offset"]
    assert sel["length"] == hit["master_length"]


def test_search_hit_recipe_round_trip(client):
    r = client.get("/search", params={"q": "丙丁"})
    hit = r.json()["hits"][0]
    fulfil = client.post("/recipes:fulfil", json=hit["recipe"])
    assert fulfil.status_code == 200
    data = fulfil.json()
    assert data["errors"] == []
    assert data["results"][0]["content"]["text"] == "丙丁"


def test_search_pagination(client):
    r = client.get("/search", params={"q": "甲", "limit": 1, "offset": 0})
    assert r.status_code == 200
    body = r.json()
    assert body["limit"] == 1
    assert body["offset"] == 0
    assert len(body["hits"]) <= 1
