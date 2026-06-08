"""SPA static mount + 404 fallback to index.html on non-API paths."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bkk.serve import create_app
from bkk.serve.config import ServeConfig

from .conftest import ORIGINAL_TESTCLIENT_REQUEST


class _RawClient(TestClient):
    """TestClient that bypasses the ``/api`` auto-prefix in ``conftest.py``.

    SPA-vs-API routing tests need literal URLs (e.g. ``/assets/app.js``,
    ``/some/spa/route``) to verify the fallback behavior.
    """

    def request(self, method, url, *args, **kwargs):  # type: ignore[override]
        return ORIGINAL_TESTCLIENT_REQUEST(self, method, url, *args, **kwargs)


@pytest.fixture
def web_dist(tmp_path: Path) -> Path:
    """A throwaway built SPA: index.html + one static asset."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<!doctype html><html><body>SPA INDEX</body></html>",
        encoding="utf-8",
    )
    assets = dist / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('app');", encoding="utf-8")
    return dist


def _client(corpus: Path, *, web_dist: Path | None) -> TestClient:
    config = ServeConfig(
        corpus_root=corpus,
        index_path=corpus / "_corpus.bkkx",
        web_dist=web_dist,
    )
    return _RawClient(create_app(config))


def test_root_serves_index_when_web_dist_set(corpus: Path, web_dist: Path):
    client = _client(corpus, web_dist=web_dist)
    r = client.get("/")
    assert r.status_code == 200
    assert "SPA INDEX" in r.text


def test_root_returns_server_info_without_web_dist(corpus: Path):
    client = _client(corpus, web_dist=None)
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "bkk-serve"


def test_static_asset_served(corpus: Path, web_dist: Path):
    client = _client(corpus, web_dist=web_dist)
    r = client.get("/assets/app.js")
    assert r.status_code == 200
    assert "console.log" in r.text


def test_api_paths_still_return_json_with_web_dist(corpus: Path, web_dist: Path):
    client = _client(corpus, web_dist=web_dist)
    r = client.get("/api/catalog")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")


def test_api_404_returns_json_not_index(corpus: Path, web_dist: Path):
    client = _client(corpus, web_dist=web_dist)
    r = client.get("/api/bundles/NO_SUCH_ID")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["error"] == "bundle_not_found"


def test_unknown_non_api_path_falls_back_to_index(corpus: Path, web_dist: Path):
    client = _client(corpus, web_dist=web_dist)
    r = client.get("/some/spa/route")
    assert r.status_code == 200
    assert "SPA INDEX" in r.text


def test_missing_web_dist_dir_does_not_break_app(corpus: Path, tmp_path: Path):
    nonexistent = tmp_path / "no_such_dist"
    client = _client(corpus, web_dist=nonexistent)
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "bkk-serve"


def test_web_dist_without_index_is_skipped(corpus: Path, tmp_path: Path):
    empty = tmp_path / "empty_dist"
    empty.mkdir()
    client = _client(corpus, web_dist=empty)
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
