from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient

from bkk.serve import create_app
from bkk.serve.config import ServeConfig
from bkk.serve.remote_bundles import GitHubBundleClient

from .conftest import write_bundle


def _file_payload(data: dict[str, Any], *, size: int | None = None) -> dict[str, Any]:
    text = yaml.safe_dump(data, allow_unicode=True)
    raw = text.encode("utf-8")
    return {
        "type": "file",
        "sha": "blob-sha",
        "size": len(raw) if size is None else size,
        "content": base64.b64encode(raw).decode("ascii"),
    }


def _manifest(textid: str, title: str) -> dict[str, Any]:
    return {
        "canonical_identifier": f"bkk:test/{textid}/v1",
        "assets": {
            "parts": [
                {"seq": 1, "filename": f"{textid}_001.yaml", "hash": "sha256:0"}
            ],
            "references": [
                {"filename": "notes.md", "role": "notes", "hash": "sha256:1"}
            ],
        },
        "editions": [{"short": "WYG", "label": "WYG"}],
        "metadata": {"title": title, "edition": {"short": "bkk"}},
    }


def _juan(text: str) -> dict[str, Any]:
    return {
        "seq": 1,
        "body": {
            "text": text,
            "hash": "sha256:0",
            "markers": [{"type": "voice", "offset": 0, "length": len(text), "name": "root"}],
        },
        "hash": "sha256:0",
    }


@pytest.fixture
def fake_remote(monkeypatch):
    calls: list[tuple[str, str, str | None]] = []
    manifests = {
        "bkkbooks/KR1h0004": _manifest("KR1h0004", "Remote Canonical"),
        "alice/KR1h0004": _manifest("KR1h0004", "Remote User"),
    }
    juans = {
        "bkkbooks/KR1h0004": _juan("遠端正文"),
        "alice/KR1h0004": _juan("用戶正文"),
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
        calls.append((method, path, token))
        if method == "GET" and path.startswith("/repos/"):
            rest = path[len("/repos/"):]
            if "/contents/" in rest:
                repo, content = rest.split("/contents/", 1)
                file_path = content.split("?", 1)[0]
                if repo not in manifests:
                    raise HTTPException(
                        status_code=502,
                        detail={"github_status": 404, "body": {"message": "Not Found"}},
                    )
                if file_path.endswith(".manifest.yaml"):
                    return _file_payload(manifests[repo])
                if file_path.endswith("_001.yaml"):
                    return _file_payload(juans[repo])
                if file_path == "notes.md":
                    raw = b"# remote notes\n"
                    return {
                        "type": "file",
                        "sha": "notes-sha",
                        "size": len(raw),
                        "content": base64.b64encode(raw).decode("ascii"),
                    }
                raise AssertionError(path)
            if "/git/ref/heads/" in rest:
                repo, _branch = rest.split("/git/ref/heads/", 1)
                if repo not in manifests:
                    raise HTTPException(
                        status_code=502,
                        detail={"github_status": 404, "body": {"message": "Not Found"}},
                    )
                return {"object": {"sha": f"{repo}-sha"}}
            if "/git/trees/" in rest:
                return {
                    "tree": [
                        {
                            "type": "blob",
                            "path": "editions/WYG/KR1h0004-WYG.manifest.yaml",
                        }
                    ]
                }
            repo = rest
            if repo not in manifests:
                raise HTTPException(
                    status_code=502,
                    detail={"github_status": 404, "body": {"message": "Not Found"}},
                )
            return {"default_branch": "main", "full_name": repo}
        raise AssertionError((method, path, token, kwargs))

    monkeypatch.setattr(GitHubBundleClient, "_request_json", fake_json)
    return calls


def test_remote_canonical_manifest_and_juan_prefer_github(
    tmp_path: Path, fake_remote,
):
    write_bundle(tmp_path, "KR1h0004", "本地正文", title="Local Canonical")
    client = TestClient(create_app(ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
        github_read_token="server-token",
        github_client_id="client-id",
        github_client_secret="client-secret",
    )))

    manifest = client.get("/bundles/KR1h0004/manifest")
    assert manifest.status_code == 200, manifest.text
    assert manifest.json()["metadata"]["title"] == "Remote Canonical"
    assert manifest.json()["available_editions"] == [{"short": "WYG", "label": "WYG"}]
    assert manifest.headers["x-bkk-bundle-origin"] == "remote-canonical"
    assert manifest.headers["x-bkk-bundle-repo"] == "bkkbooks/KR1h0004"

    juan = client.get("/bundles/KR1h0004/juan/1")
    assert juan.status_code == 200, juan.text
    assert juan.json()["body"]["text"] == "遠端正文"

    asset = client.get("/bundles/KR1h0004/assets/notes.md")
    assert asset.status_code == 200, asset.text
    assert asset.text == "# remote notes\n"


def test_remote_user_repo_beats_canonical(tmp_path: Path, fake_remote):
    client = TestClient(create_app(ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
        github_read_token="server-token",
        github_client_id="client-id",
        github_client_secret="client-secret",
    )))
    session = client.app.state.bkk.sessions.create(
        login="alice",
        name="Alice",
        avatar_url=None,
        html_url="https://github.com/alice",
        access_token="alice-token",
        workspace={"repo": "alice/BKK-Workspace", "branch": "alice"},
    )
    client.cookies.set("bkk_session", session.id)

    response = client.get("/bundles/KR1h0004/juan/1")

    assert response.status_code == 200, response.text
    assert response.json()["body"]["text"] == "用戶正文"
    assert response.headers["x-bkk-bundle-origin"] == "remote-user"
    assert response.headers["x-bkk-bundle-repo"] == "alice/KR1h0004"


def test_remote_failure_falls_back_to_local(tmp_path: Path, monkeypatch):
    write_bundle(tmp_path, "KR1h0005", "本地可用", title="Local Fallback")

    def fail_remote(self, method, path, token, *, expected_statuses, **kwargs):
        raise HTTPException(status_code=502, detail="GitHub unavailable")

    monkeypatch.setattr(GitHubBundleClient, "_request_json", fail_remote)
    client = TestClient(create_app(ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
        github_read_token="server-token",
    )))

    response = client.get("/bundles/KR1h0005/manifest")

    assert response.status_code == 200, response.text
    assert response.json()["metadata"]["title"] == "Local Fallback"
    assert response.headers["x-bkk-bundle-origin"] == "local-corpus"
    assert response.headers["x-bkk-bundle-fallback"] == "local"
