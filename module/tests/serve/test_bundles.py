"""Endpoints under /bundles."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from bkk.serve import create_app
from bkk.serve.config import ServeConfig
from bkk.serve.state import AppState

from .conftest import write_bundle


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


def test_direct_bundle_lookup_does_not_build_corpus_cache(tmp_path: Path):
    write_bundle(tmp_path, "FAST0001", "甲乙", title="Fast path")
    state = AppState(ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
    ))

    class ExplodingCache:
        def lookup(self, textid: str):
            raise AssertionError(f"unexpected full cache lookup for {textid}")

        def get(self):
            raise AssertionError("unexpected full cache build")

    state._cache = ExplodingCache()

    rec = state.lookup_bundle("FAST0001")

    assert rec is not None
    assert rec.textid == "FAST0001"
    assert rec.title == "Fast path"


def test_bundle_search_hits_in_text_order(client):
    r = client.get("/bundles/TEST0001/search?q=丙丁")
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "丙丁"
    assert body["capped"] is False
    assert body["total"] == 1
    hit = body["hits"][0]
    assert hit["textid"] == "TEST0001"
    assert hit["juan_seq"] == 1
    assert hit["bucket"] == "body"
    assert hit["match"] == "丙丁"
    assert hit["master_offset"] == 2


def test_bundle_search_supports_compound_keywords(client):
    r = client.get(
        "/bundles/TEST0001/search",
        params={"q": "甲 NEAR 丁", "search_distance": 2},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "甲 NEAR 丁"
    assert body["capped"] is False
    assert body["total"] == 1
    assert body["hits"][0]["match"] == "甲"

    too_far = client.get(
        "/bundles/TEST0001/search",
        params={"q": "甲 NEAR 丁", "search_distance": 1},
    )
    assert too_far.status_code == 200
    assert too_far.json()["total"] == 0


def test_bundle_search_scopes_to_one_textid(client):
    # 'A' appears in TEST0002 only; querying TEST0001 must return zero.
    r = client.get("/bundles/TEST0001/search?q=A")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["hits"] == []


def test_bundle_search_unknown_bundle(client):
    r = client.get("/bundles/TEST9999/search?q=甲")
    assert r.status_code == 404
    assert r.json()["error"] == "bundle_not_found"


def test_get_juan_not_found(client):
    r = client.get("/bundles/TEST0001/juan/9")
    assert r.status_code == 404
    assert r.json()["error"] == "juan_not_found"


def test_get_juan_unknown_bundle(client):
    r = client.get("/bundles/TEST9999/juan/1")
    assert r.status_code == 404
    assert r.json()["error"] == "bundle_not_found"


def test_get_manifest_image_base_urls_override(tmp_path: Path):
    """ServeConfig.image_base_urls replaces bundle entries per-edition; unlisted editions are preserved."""
    write_bundle(
        tmp_path,
        "IMGT0001",
        "甲乙",
        title="img test",
        extra_metadata={
            "image_base_urls": {
                "krp": "https://old.example/krp/",
                "tls": "https://old.example/tls/",
            }
        },
    )
    config = ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
        image_base_urls={"krp": "https://new.example/krp/"},
    )
    client = TestClient(create_app(config))

    r = client.get("/bundles/IMGT0001/manifest")
    assert r.status_code == 200
    base_urls = r.json()["metadata"]["image_base_urls"]
    assert base_urls["krp"] == "https://new.example/krp/"
    assert base_urls["tls"] == "https://old.example/tls/"


def test_get_manifest_image_base_urls_added_when_bundle_lacks_field(tmp_path: Path):
    """When the bundle has no image_base_urls, the override is added verbatim."""
    write_bundle(tmp_path, "IMGT0002", "甲", title="img test 2")
    config = ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
        image_base_urls={"krp": "https://new.example/krp/"},
    )
    client = TestClient(create_app(config))

    r = client.get("/bundles/IMGT0002/manifest")
    assert r.status_code == 200
    assert r.json()["metadata"]["image_base_urls"] == {
        "krp": "https://new.example/krp/"
    }


def test_get_manifest_no_override_leaves_manifest_untouched(tmp_path: Path):
    """With no override configured, bundle's image_base_urls passes through unchanged."""
    write_bundle(
        tmp_path,
        "IMGT0003",
        "甲",
        title="img test 3",
        extra_metadata={"image_base_urls": {"krp": "https://bundle.example/krp/"}},
    )
    config = ServeConfig(
        corpus_root=tmp_path, index_path=tmp_path / "_corpus.bkkx"
    )
    client = TestClient(create_app(config))

    r = client.get("/bundles/IMGT0003/manifest")
    assert r.status_code == 200
    assert r.json()["metadata"]["image_base_urls"] == {
        "krp": "https://bundle.example/krp/"
    }
