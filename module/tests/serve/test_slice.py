"""Endpoint tests for /bundles/{textid}/juan/{seq}/slice."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bkk.serve import create_app
from bkk.serve.config import ServeConfig

from .conftest import write_bundle


@pytest.fixture
def slice_corpus(tmp_path: Path) -> Path:
    write_bundle(
        tmp_path,
        "SLC0001",
        "甲乙丙丁戊己庚辛壬癸",
        title="天干",
        variants=[
            {"id": "m_start", "offset": 0},
            {"id": "m_mid", "offset": 5},
            {"id": "m_end", "offset": 10},
        ],
    )
    return tmp_path


@pytest.fixture
def slice_client(slice_corpus: Path) -> TestClient:
    config = ServeConfig(
        corpus_root=slice_corpus,
        index_path=slice_corpus / "_corpus.bkkx",
    )
    return TestClient(create_app(config))


def test_slice_whole(slice_client):
    r = slice_client.get("/bundles/SLC0001/juan/1/slice")
    assert r.status_code == 200
    body = r.json()
    assert body["bucket"] == "body"
    assert body["span"] == [0, 10]
    assert body["text"] == "甲乙丙丁戊己庚辛壬癸"
    assert len(body["markers"]) == 3


def test_slice_by_offset(slice_client):
    r = slice_client.get("/bundles/SLC0001/juan/1/slice?offset=2&length=4")
    assert r.status_code == 200
    body = r.json()
    assert body["span"] == [2, 6]
    assert body["text"] == "丙丁戊己"
    assert [m["id"] for m in body["markers"]] == ["m_mid"]
    assert body["markers"][0]["offset"] == 3  # rebased to slice start


def test_slice_by_offset_bad_range(slice_client):
    r = slice_client.get("/bundles/SLC0001/juan/1/slice?offset=20&length=5")
    assert r.status_code == 400
    assert r.json()["error"] == "bad_slice_range"


def test_slice_by_markers(slice_client):
    r = slice_client.get(
        "/bundles/SLC0001/juan/1/slice?from=m_start&to=m_end"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["span"] == [0, 10]
    assert body["text"] == "甲乙丙丁戊己庚辛壬癸"


def test_slice_by_markers_unknown(slice_client):
    r = slice_client.get(
        "/bundles/SLC0001/juan/1/slice?from=m_nope&to=m_end"
    )
    assert r.status_code == 400
    assert r.json()["error"] == "marker_not_found"


def test_slice_by_toc(slice_client):
    r = slice_client.get("/bundles/SLC0001/juan/1/slice?toc=SLC0001_001-1a")
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "甲乙丙丁戊己庚辛壬癸"


def test_slice_form_conflict(slice_client):
    r = slice_client.get(
        "/bundles/SLC0001/juan/1/slice?from=m_start&to=m_end&offset=0&length=5"
    )
    assert r.status_code == 400
    assert r.json()["error"] == "slice_form_conflict"


def test_slice_marker_range_partial(slice_client):
    r = slice_client.get("/bundles/SLC0001/juan/1/slice?from=m_start")
    assert r.status_code == 400
    assert r.json()["error"] == "marker_range_requires_both"


def test_slice_via_texts_alias(slice_client):
    r = slice_client.get(
        "/texts/SLC0001/juan/1/slice?offset=0&length=3"
    )
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "甲乙丙"
    assert body["textid"] == "SLC0001"
