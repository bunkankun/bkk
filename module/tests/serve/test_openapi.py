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
        "/api/bundles",
        "/api/bundles/{textid}",
        "/api/bundles/{textid}/manifest",
        "/api/bundles/{textid}/juan",
        "/api/bundles/{textid}/juan/{seq}",
        "/api/bundles/{textid}/juan/{seq}/{bucket}",
        "/api/bundles/{textid}/juan/{seq}/{bucket}/text",
        "/api/bundles/{textid}/juan/{seq}/{bucket}/markers",
        "/api/bundles/{textid}/assets",
        "/api/bundles/{textid}/assets/{name}",
        "/api/texts/{identifier}",
        "/api/texts/{identifier}/manifest",
        "/api/texts/{identifier}/juan",
        "/api/texts/{identifier}/juan/{seq}",
        "/api/catalog",
        "/api/by-canonical",
        "/api/search",
    }
    missing = expected - paths.keys()
    assert not missing, f"missing paths: {missing}"


def test_docs_served(client):
    r = client.get("/docs")
    assert r.status_code == 200
    assert "swagger" in r.text.lower() or "openapi" in r.text.lower()
