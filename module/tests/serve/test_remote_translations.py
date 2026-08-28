from __future__ import annotations

import base64
import sqlite3
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient

from bkk.serve import create_app
from bkk.serve.config import ServeConfig
from bkk.serve.remote_bundles import GitHubBundleClient
from bkk.serve.remote_translations import refresh_remote_translations
from bkk.serve.remote_translations import _load_remote_translation_visible
from bkk.serve.translations import list_translation_bundles_from_catalog

from .test_translations import _write_source, _write_translation


def _file_payload_text(raw: str) -> dict:
    data = raw.encode("utf-8")
    return {
        "type": "file",
        "sha": "blob-sha",
        "size": len(data),
        "content": base64.b64encode(data).decode("ascii"),
    }


def _remote_manifest(title: str) -> str:
    return f"""---
canonical_identifier: bkk:translation/KR1h0004-en-test/v1
source:
  canonical_identifier: bkk:krp/KR1h0004/v1
language: en
title: {title}
responsibility:
- {{role: translator, name: Remote Tester}}
juan:
- {{seq: 1, label: '001', file: KR1h0004-en-test_001.md, segs: 1}}
hash: sha256:0
---
# {title}
"""


def _remote_juan(text: str) -> str:
    return f"""---
juan_seq: 1
juan_label: '001'
markers:
- {{ref: 001-1a.3, corresp: [001-1a.3]}}
---
[{text}]{{@001-1a.3}}
"""


def _install_fake_remote(monkeypatch, *, title: str = "Remote Translation"):
    files = {
        "KR1h0004-en-test.md": _remote_manifest(title),
        "KR1h0004-en-test_001.md": _remote_juan("remote selected text"),
    }

    def fake_json(
        self,
        method: str,
        path: str,
        token: str | None,
        *,
        expected_statuses: set[int],
        **kwargs,
    ):
        if method == "GET" and path.startswith("/orgs/bkktranslations/repos"):
            return [{
                "name": "KR1h0004-en-test",
                "full_name": "bkktranslations/KR1h0004-en-test",
                "default_branch": "main",
            }]
        if method == "GET" and path.startswith("/repos/"):
            rest = path[len("/repos/"):]
            if "/contents/" in rest:
                repo, content = rest.split("/contents/", 1)
                file_path = content.split("?", 1)[0]
                if repo != "bkktranslations/KR1h0004-en-test" or file_path not in files:
                    raise _not_found()
                return _file_payload_text(files[file_path])
            if "/git/ref/heads/" in rest:
                repo, _branch = rest.split("/git/ref/heads/", 1)
                if repo != "bkktranslations/KR1h0004-en-test":
                    raise _not_found()
                return {"object": {"sha": "remote-sha"}}
            if "/git/trees/" in rest:
                repo, _ref = rest.split("/git/trees/", 1)
                if repo != "bkktranslations/KR1h0004-en-test":
                    raise _not_found()
                return {
                    "tree": [
                        {"type": "blob", "path": "KR1h0004-en-test.md"},
                        {"type": "blob", "path": "KR1h0004-en-test_001.md"},
                    ]
                }
            repo = rest
            if repo != "bkktranslations/KR1h0004-en-test":
                raise _not_found()
            return {"default_branch": "main", "full_name": repo}
        raise AssertionError((method, path, token, kwargs))

    monkeypatch.setattr(GitHubBundleClient, "_request_json", fake_json)


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=502,
        detail={"github_status": 404, "body": {"message": "Not Found"}},
    )


def test_translation_alignment_prefers_remote_then_honors_prefer_local(
    tmp_path: Path,
    monkeypatch,
):
    _write_source(tmp_path)
    _write_translation(tmp_path)
    _install_fake_remote(monkeypatch)

    remote_client = TestClient(create_app(ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
        translation_github_read_token="translation-token",
    )))
    remote_response = remote_client.get(
        "/bundles/KR1h0004/juan/1/translations/KR1h0004-en-test"
    )
    assert remote_response.status_code == 200, remote_response.text
    assert remote_response.json()["translation"]["title"] == "Remote Translation"
    assert remote_response.json()["rows"][0]["translation_text"] == "remote selected text"

    local_client = TestClient(create_app(ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
        bundle_load_mode="prefer_local",
        translation_github_read_token="translation-token",
    )))
    local_response = local_client.get(
        "/bundles/KR1h0004/juan/1/translations/KR1h0004-en-test"
    )
    assert local_response.status_code == 200, local_response.text
    assert local_response.json()["translation"]["title"] == "Test Translation"
    assert local_response.json()["rows"][0]["translation_text"] == "The Master said:"


def test_ready_inventory_skips_absent_user_translation_probe(client, monkeypatch):
    state = client.app.state.bkk
    session = state.sessions.create(
        login="alice",
        name=None,
        avatar_url=None,
        html_url=None,
        access_token="secret-token",
        workspace={"repo": "alice/BKK-Workspace", "branch": "alice"},
        repo_inventory_ready=True,
    )
    calls: list[str] = []

    def fake_load(*args, **kwargs):
        calls.append(kwargs["repo"])
        return None

    monkeypatch.setattr(
        "bkk.serve.remote_translations._load_remote_translation",
        fake_load,
    )

    _load_remote_translation_visible(
        state,
        session,
        "KR1h0004-en-test",
        source_textid="KR1h0004",
        include_juans=False,
    )

    assert calls == ["bkktranslations/KR1h0004-en-test"]


def test_remote_translation_refresh_builds_local_catalog_and_search(
    tmp_path: Path,
    monkeypatch,
):
    _write_source(tmp_path)
    _install_fake_remote(monkeypatch, title="Cached Remote Translation")
    config = ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
        catalog_path=tmp_path / "_catalog.bkkc",
        translation_search_path=tmp_path / "_translations.bkkt",
        translation_github_read_token="translation-token",
    )
    app = create_app(config)

    result = refresh_remote_translations(app.state.bkk, token="translation-token")

    cache_root = config.translation_remote_cache_root
    bundle_dir = cache_root / "KR1h" / "KR1h0004" / "en" / "KR1h0004-en-test"
    assert result["refreshed"][0]["path"] == str(bundle_dir)
    assert (bundle_dir / "KR1h0004-en-test.md").is_file()
    assert (bundle_dir / "KR1h0004-en-test_001.md").is_file()

    conn = sqlite3.connect(config.catalog_path)
    search_conn = sqlite3.connect(config.translation_search_path)
    try:
        matches, total = list_translation_bundles_from_catalog(
            conn,
            search_conn=search_conn,
            source_textid="KR1h0004",
            q="remote selected",
        )
    finally:
        conn.close()
        search_conn.close()
    assert total == 1
    assert matches[0].summary.title == "Cached Remote Translation"
    assert matches[0].path == bundle_dir
