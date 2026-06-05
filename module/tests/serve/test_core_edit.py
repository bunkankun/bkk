"""Inline editing of core records on a user's GitHub fork."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bkk.index.core import build_core_index
from bkk.serve import create_app
from bkk.serve.config import ServeConfig
from bkk.serve.routers import auth as auth_router
from bkk.serve.routers import core_edit as core_edit_router


def _patch_github(monkeypatch, fake):
    """Patch ``_github_json`` everywhere it's bound — both in the edit
    router itself and in ``auth`` (which ``_repo_exists`` and
    ``_get_branch_ref`` resolve against)."""
    monkeypatch.setattr(core_edit_router, "_github_json", fake)
    monkeypatch.setattr(auth_router, "_github_json", fake)


CONCEPT_UUID = "00000000-0000-0000-0000-000000000001"
CONCEPT_PATH = f"concepts/0/{CONCEPT_UUID}.md"
CONCEPT_TEXT = (
    "---\n"
    f"uuid: {CONCEPT_UUID}\n"
    "type: concept\n"
    "concept: TEST\n"
    "zh: 測試\n"
    "---\n"
    "# Concept: TEST\nbody line\n"
)


@pytest.fixture
def core_root(tmp_path: Path) -> Path:
    root = tmp_path / "bkk-core"
    (root / "concepts" / "0").mkdir(parents=True)
    (root / CONCEPT_PATH).write_text(CONCEPT_TEXT, encoding="utf-8")
    return root


def _make_client(tmp_path: Path, core_root: Path) -> TestClient:
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    build_core_index(core_root, core_root / "_core.bkki")
    config = ServeConfig(
        corpus_root=corpus_root,
        index_path=corpus_root / "_corpus.bkkx",
        core_root=core_root,
        core_index_path=core_root / "_core.bkki",
        core_upstream_repo="bunkankun/bkk-core",
    )
    return TestClient(create_app(config))


def _login(client: TestClient, login: str = "alice", token: str = "tok"):
    state = client.app.state.bkk
    session = state.sessions.create(
        login=login, name=None, avatar_url=None, html_url=None,
        access_token=token,
        workspace={"repo": f"{login}/BKK-Workspace", "branch": login,
                   "html_url": "", "private": True},
    )
    client.cookies.set("bkk_session", session.id)
    return session


def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def test_edit_record_requires_login(tmp_path, core_root):
    client = _make_client(tmp_path, core_root)
    r = client.patch(
        f"/core/concepts/{CONCEPT_UUID}",
        json={"frontmatter": {}, "body": ""},
    )
    assert r.status_code == 401


def test_edit_record_requires_upstream_config(tmp_path, core_root):
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    build_core_index(core_root, core_root / "_core.bkki")
    config = ServeConfig(
        corpus_root=corpus_root,
        index_path=corpus_root / "_corpus.bkkx",
        core_root=core_root,
        core_index_path=core_root / "_core.bkki",
        # core_upstream_repo intentionally unset
    )
    client = TestClient(create_app(config))
    _login(client)
    r = client.patch(
        f"/core/concepts/{CONCEPT_UUID}",
        json={"frontmatter": {}, "body": ""},
    )
    assert r.status_code == 503
    assert "upstream_repo" in r.json()["detail"]


def test_edit_record_404_when_uuid_unknown(tmp_path, core_root):
    client = _make_client(tmp_path, core_root)
    _login(client)
    r = client.patch(
        "/core/concepts/99999999-9999-9999-9999-999999999999",
        json={"frontmatter": {}, "body": ""},
    )
    assert r.status_code == 404


def test_edit_record_rejects_new_frontmatter_key(tmp_path, core_root, monkeypatch):
    client = _make_client(tmp_path, core_root)
    _login(client)

    def fake_github_json(method, path, token, **kwargs):
        if method == "GET" and path.startswith("/repos/bunkankun/bkk-core/contents/"):
            return {"type": "file", "sha": "base-sha",
                    "content": _b64(CONCEPT_TEXT)}
        raise AssertionError(f"unexpected call: {method} {path}")

    _patch_github(monkeypatch, fake_github_json)

    r = client.patch(
        f"/core/concepts/{CONCEPT_UUID}",
        json={
            "frontmatter": {"concept": "TEST", "zh": "測試", "new_key": "x"},
            "body": "# Concept: TEST\nbody line\n",
        },
    )
    assert r.status_code == 400
    assert "new_key" in r.json()["detail"]


def test_edit_record_rejects_locked_key_change(tmp_path, core_root, monkeypatch):
    client = _make_client(tmp_path, core_root)
    _login(client)

    def fake_github_json(method, path, token, **kwargs):
        if method == "GET" and path.startswith("/repos/bunkankun/bkk-core/contents/"):
            return {"type": "file", "sha": "base-sha",
                    "content": _b64(CONCEPT_TEXT)}
        raise AssertionError(f"unexpected call: {method} {path}")

    _patch_github(monkeypatch, fake_github_json)

    r = client.patch(
        f"/core/concepts/{CONCEPT_UUID}",
        json={
            "frontmatter": {
                "uuid": "ffffffff-ffff-ffff-ffff-ffffffffffff",
                "type": "concept",
                "concept": "TEST",
                "zh": "測試",
            },
            "body": "body\n",
        },
    )
    assert r.status_code == 400
    assert "uuid" in r.json()["detail"]


def test_edit_record_happy_path_creates_branch_and_commits(
    tmp_path, core_root, monkeypatch,
):
    client = _make_client(tmp_path, core_root)
    _login(client, login="alice", token="alice-tok")
    calls = []

    def fake_github_json(method, path, token, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        # upstream file fetch
        if (method == "GET" and
                path.startswith("/repos/bunkankun/bkk-core/contents/")):
            return {"type": "file", "sha": "upstream-blob-sha",
                    "content": _b64(CONCEPT_TEXT)}
        # repo-exists check for the user's fork
        if method == "GET" and path == "/repos/alice/bkk-core":
            return {"full_name": "alice/bkk-core", "default_branch": "master"}
        # branch existence check on fork — 404 to force creation
        if (method == "GET" and
                path.startswith("/repos/alice/bkk-core/git/ref/heads/")):
            from fastapi import HTTPException
            raise HTTPException(
                status_code=502,
                detail={"github_status": 404, "body": "Not Found"},
            )
        # upstream master ref for new branch base sha
        if method == "GET" and path == "/repos/bunkankun/bkk-core/git/ref/heads/master":
            return {"object": {"sha": "upstream-master-sha"}}
        # create branch on fork
        if method == "POST" and path == "/repos/alice/bkk-core/git/refs":
            return {"ref": kwargs["json"]["ref"], "object": {"sha": kwargs["json"]["sha"]}}
        # head fetch on fork branch (server reads parent_sha because client
        # didn't send one)
        if (method == "GET" and
                "/repos/alice/bkk-core/contents/" in path
                and "bkk-edit" in path):
            return {"type": "file", "sha": "fork-blob-sha",
                    "content": _b64(CONCEPT_TEXT)}
        # PUT new content
        if method == "PUT" and path.startswith("/repos/alice/bkk-core/contents/"):
            return {
                "content": {"sha": "new-blob-sha"},
                "commit": {"sha": "new-commit-sha"},
            }
        # existing-PR lookup
        if method == "GET" and "/pulls?" in path:
            return []
        raise AssertionError(f"unexpected call: {method} {path}")

    _patch_github(monkeypatch, fake_github_json)

    r = client.patch(
        f"/core/concepts/{CONCEPT_UUID}",
        json={
            "frontmatter": {"concept": "TEST", "zh": "测试"},
            "body": "# Concept: TEST\nupdated body\n",
        },
    )

    assert r.status_code == 200, r.json()
    data = r.json()
    assert data["fork_repo"] == "alice/bkk-core"
    assert data["branch"].startswith(f"bkk-edit/concepts/{CONCEPT_UUID}-")
    assert data["commit_sha"] == "new-commit-sha"
    assert data["parent_sha"] == "new-blob-sha"
    assert data["pr_url"] is None
    assert data["frontmatter"]["zh"] == "测试"
    assert "updated body" in data["body_markdown"]

    # PUT happened with the merged + serialized payload
    put_calls = [c for c in calls if c[0] == "PUT"]
    assert len(put_calls) == 1
    put_json = put_calls[0][2]
    assert put_json["branch"] == data["branch"]
    assert put_json["sha"] == "fork-blob-sha"
    decoded = base64.b64decode(put_json["content"]).decode("utf-8")
    assert "concept: TEST" in decoded
    assert "zh: 测试" in decoded
    assert decoded.endswith("updated body\n")


def test_edit_record_reuses_supplied_branch_and_parent_sha(
    tmp_path, core_root, monkeypatch,
):
    client = _make_client(tmp_path, core_root)
    _login(client, login="alice", token="alice-tok")
    calls = []

    def fake_github_json(method, path, token, **kwargs):
        calls.append((method, path))
        if (method == "GET" and
                path.startswith("/repos/bunkankun/bkk-core/contents/")):
            return {"type": "file", "sha": "upstream-sha",
                    "content": _b64(CONCEPT_TEXT)}
        if method == "GET" and path == "/repos/alice/bkk-core":
            return {"full_name": "alice/bkk-core"}
        if (method == "GET" and
                path.startswith("/repos/alice/bkk-core/git/ref/heads/")):
            return {"object": {"sha": "existing-branch-sha"}}
        if method == "PUT":
            return {"content": {"sha": "patched-sha"},
                    "commit": {"sha": "patched-commit"}}
        if method == "GET" and "/pulls?" in path:
            return []
        raise AssertionError(f"unexpected call: {method} {path}")

    _patch_github(monkeypatch, fake_github_json)

    r = client.patch(
        f"/core/concepts/{CONCEPT_UUID}",
        json={
            "frontmatter": {"concept": "TEST"},
            "body": "body\n",
            "branch": "bkk-edit/concepts/existing-branch",
            "parent_sha": "client-provided-sha",
        },
    )

    assert r.status_code == 200, r.json()
    # No new-branch creation call should have happened.
    assert not any(c == ("POST", "/repos/alice/bkk-core/git/refs") for c in calls)
    # No head-fetch on fork branch — client supplied parent_sha.
    assert not any(
        "/repos/alice/bkk-core/contents/" in c[1] and c[0] == "GET" for c in calls
    )


def test_edit_record_translates_github_conflict_to_409(
    tmp_path, core_root, monkeypatch,
):
    client = _make_client(tmp_path, core_root)
    _login(client)

    def fake_github_json(method, path, token, **kwargs):
        if method == "GET" and path.startswith("/repos/bunkankun/bkk-core/contents/"):
            return {"type": "file", "sha": "upstream-sha",
                    "content": _b64(CONCEPT_TEXT)}
        if method == "GET" and path == "/repos/alice/bkk-core":
            return {"full_name": "alice/bkk-core"}
        if (method == "GET" and
                path.startswith("/repos/alice/bkk-core/git/ref/heads/")):
            return {"object": {"sha": "branch-sha"}}
        if method == "PUT":
            from fastapi import HTTPException
            raise HTTPException(
                status_code=502,
                detail={"github_status": 409, "body": "sha mismatch"},
            )
        raise AssertionError(f"unexpected call: {method} {path}")

    _patch_github(monkeypatch, fake_github_json)

    r = client.patch(
        f"/core/concepts/{CONCEPT_UUID}",
        json={
            "frontmatter": {"concept": "TEST"},
            "body": "body\n",
            "branch": "bkk-edit/concepts/x",
            "parent_sha": "stale-sha",
        },
    )
    assert r.status_code == 409
    assert "reload" in r.json()["detail"]


def test_open_pr_returns_url(tmp_path, core_root, monkeypatch):
    client = _make_client(tmp_path, core_root)
    _login(client, login="alice")
    calls = []

    def fake_github_json(method, path, token, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        if method == "POST" and path == "/repos/bunkankun/bkk-core/pulls":
            return {
                "html_url": "https://github.com/bunkankun/bkk-core/pull/42",
                "number": 42,
            }
        raise AssertionError(f"unexpected call: {method} {path}")

    _patch_github(monkeypatch, fake_github_json)

    r = client.post(
        f"/core/concepts/{CONCEPT_UUID}/pr",
        json={"branch": "bkk-edit/concepts/abc"},
    )
    assert r.status_code == 200, r.json()
    data = r.json()
    assert data["pr_url"].endswith("/pull/42")
    assert data["pr_number"] == 42
    assert data["already_existed"] is False
    pr_json = calls[0][2]
    assert pr_json["head"] == "alice:bkk-edit/concepts/abc"
    assert pr_json["base"] == "master"


def test_open_pr_returns_existing_on_422(tmp_path, core_root, monkeypatch):
    client = _make_client(tmp_path, core_root)
    _login(client, login="alice")

    def fake_github_json(method, path, token, **kwargs):
        if method == "POST" and path == "/repos/bunkankun/bkk-core/pulls":
            from fastapi import HTTPException
            raise HTTPException(
                status_code=502,
                detail={"github_status": 422, "body": "pr already exists"},
            )
        if method == "GET" and path.startswith("/repos/bunkankun/bkk-core/pulls?"):
            return [{
                "html_url": "https://github.com/bunkankun/bkk-core/pull/7",
                "number": 7,
            }]
        raise AssertionError(f"unexpected call: {method} {path}")

    _patch_github(monkeypatch, fake_github_json)

    r = client.post(
        f"/core/concepts/{CONCEPT_UUID}/pr",
        json={"branch": "bkk-edit/concepts/abc"},
    )
    assert r.status_code == 200, r.json()
    data = r.json()
    assert data["pr_number"] == 7
    assert data["already_existed"] is True
