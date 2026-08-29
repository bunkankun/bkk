from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from bkk.serve import create_app
from bkk.serve.config import ServeConfig

from .conftest import ORIGINAL_TESTCLIENT_REQUEST, write_bundle


class _RawClient(TestClient):
    def request(self, method, url, *args, **kwargs):  # type: ignore[override]
        return ORIGINAL_TESTCLIENT_REQUEST(self, method, url, *args, **kwargs)


def _client(tmp_path: Path, *, ctf: bool = False, web_dist: Path | None = None) -> TestClient:
    write_bundle(tmp_path, "KR1h0004", "甲乙丙丁戊己庚辛", title="論語")
    ctf_root = None
    if ctf:
        ctf_root = tmp_path / "ctf"
        section = ctf_root / "KR1h"
        section.mkdir(parents=True)
        (section / "KR1h0004_002.ctf.yaml").write_text(
            yaml.safe_dump(
                {
                    "kind": "bkk.ctf/v1",
                    "textid": "KR1h0004",
                    "seq": 2,
                    "bucket": "body",
                    "nodes": [
                        {
                            "id": "KR1h0004/2/12/@4+2",
                            "parent_id": "KR1h0004/2",
                            "label": "戊己",
                            "level": 1,
                            "span_ref": "KR1h0004/2/@4+4",
                        }
                    ],
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
    app = create_app(ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
        ctf_root=ctf_root,
        web_dist=web_dist,
    ))
    return _RawClient(app)


def test_view_path_ref_redirects_to_root_query(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/view/KR1h0004/2/12/@13+16", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/?view_textid=KR1h0004&view_seq=2&view_bucket=body&"
        "view_offset=13&view_length=16"
    )


def test_view_bare_text_id_opens_first_juan(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/view/KR1h0004", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/?view_textid=KR1h0004&view_seq=1&view_bucket=body"
    )


def test_view_query_ref_accepts_unescaped_plus(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/view?ref=1h4/2/@13+16", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].endswith(
        "view_textid=KR1h0004&view_seq=2&view_bucket=body&"
        "view_offset=13&view_length=16"
    )


def test_view_accepts_saved_location_bucket_slash_form(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.get("/api/view/KR1h0004/2/front/@3+5", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/?view_textid=KR1h0004&view_seq=2&view_bucket=front&"
        "view_offset=3&view_length=5"
    )


def test_view_ctf_prefix_ref_resolves_to_ctf_span(tmp_path: Path) -> None:
    client = _client(tmp_path, ctf=True)

    response = client.get("/api/view/1h4/2/12", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/?view_textid=KR1h0004&view_seq=2&view_bucket=body&"
        "view_offset=4&view_length=4"
    )


def test_public_view_route_wins_before_spa_fallback(tmp_path: Path) -> None:
    web_dist = tmp_path / "dist"
    web_dist.mkdir()
    (web_dist / "index.html").write_text("<!doctype html>SPA", encoding="utf-8")
    client = _client(tmp_path, web_dist=web_dist)

    response = client.get("/view/1h4/2/@1+2", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == (
        "/?view_textid=KR1h0004&view_seq=2&view_bucket=body&"
        "view_offset=1&view_length=2"
    )
