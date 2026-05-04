"""OpenAPI schema is generated and tags are present."""

from __future__ import annotations


def test_openapi_json(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    assert spec["openapi"].startswith("3.")
    assert spec["info"]["title"] == "BKK serve"

    paths = spec["paths"]
    expected = {
        "/",
        "/healthz",
        "/bundles",
        "/bundles/{textid}",
        "/bundles/{textid}/manifest",
        "/bundles/{textid}/juan",
        "/bundles/{textid}/juan/{seq}",
        "/bundles/{textid}/juan/{seq}/{bucket}",
        "/bundles/{textid}/juan/{seq}/{bucket}/text",
        "/bundles/{textid}/juan/{seq}/{bucket}/markers",
        "/bundles/{textid}/assets",
        "/bundles/{textid}/assets/{name}",
        "/texts/{identifier}",
        "/texts/{identifier}/manifest",
        "/texts/{identifier}/juan",
        "/texts/{identifier}/juan/{seq}",
        "/catalog",
        "/by-canonical",
        "/search",
    }
    missing = expected - paths.keys()
    assert not missing, f"missing paths: {missing}"


def test_docs_served(client):
    r = client.get("/docs")
    assert r.status_code == 200
    assert "swagger" in r.text.lower() or "openapi" in r.text.lower()
