"""Endpoints under /bundles."""

from __future__ import annotations


def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "bkk-serve"
    assert body["docs"] == "/docs"


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_list_bundles(client):
    r = client.get("/bundles")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    textids = {b["textid"] for b in body["bundles"]}
    assert textids == {"TEST0001", "TEST0002"}
    by_id = {b["textid"]: b for b in body["bundles"]}
    assert by_id["TEST0001"]["title"] == "天干"
    assert by_id["TEST0001"]["canonical_identifier"] == "bkk:test/TEST0001/v1"
    assert by_id["TEST0001"]["edition_short"] == "bkk"


def test_list_bundles_prefix(client):
    r = client.get("/bundles?prefix=TEST0001")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["bundles"][0]["textid"] == "TEST0001"


def test_list_bundles_pagination(client):
    r = client.get("/bundles?limit=1&offset=1")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert len(body["bundles"]) == 1
    assert body["limit"] == 1
    assert body["offset"] == 1


def test_get_bundle(client):
    r = client.get("/bundles/TEST0001")
    assert r.status_code == 200
    body = r.json()
    assert body["textid"] == "TEST0001"
    assert body["title"] == "天干"
    assert body["editions"] == [{"short": "X", "label": "x"}]


def test_get_bundle_not_found(client):
    r = client.get("/bundles/TEST9999")
    assert r.status_code == 404
    assert r.json()["error"] == "bundle_not_found"


def test_get_manifest(client):
    r = client.get("/bundles/TEST0001/manifest")
    assert r.status_code == 200
    manifest = r.json()
    assert manifest["canonical_identifier"] == "bkk:test/TEST0001/v1"
    assert manifest["metadata"]["identifiers"]["krp"] == "TEST0001"
    assert manifest["assets"]["parts"][0]["seq"] == 1


def test_list_juan(client):
    r = client.get("/bundles/TEST0001/juan")
    assert r.status_code == 200
    parts = r.json()
    assert len(parts) == 1
    assert parts[0]["seq"] == 1
    assert parts[0]["filename"] == "TEST0001_001.yaml"


def test_get_juan(client):
    r = client.get("/bundles/TEST0001/juan/1")
    assert r.status_code == 200
    juan = r.json()
    assert juan["seq"] == 1
    assert juan["body"]["text"] == "甲乙丙丁戊己庚辛壬癸"


def test_get_juan_not_found(client):
    r = client.get("/bundles/TEST0001/juan/9")
    assert r.status_code == 404
    assert r.json()["error"] == "juan_not_found"


def test_get_juan_unknown_bundle(client):
    r = client.get("/bundles/TEST9999/juan/1")
    assert r.status_code == 404
    assert r.json()["error"] == "bundle_not_found"
