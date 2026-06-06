"""Inline editing of core records on a user's GitHub fork."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bkk.index.core import build_core_index
from bkk.serialize.yaml_io import dumps_record
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
CONCEPT_PATH = f"concepts/0/{CONCEPT_UUID}.yml"
CONCEPT_RECORD = {
    "uuid": CONCEPT_UUID,
    "type": "concept",
    "concept": "TEST",
    "zh": "測試",
    "definition": "an initial definition\n",
}
CONCEPT_TEXT = dumps_record(CONCEPT_RECORD)


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
        json={"data": {}},
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
        json={"data": {}},
    )
    assert r.status_code == 503
    assert "upstream_repo" in r.json()["detail"]


def test_edit_record_404_when_uuid_unknown(tmp_path, core_root):
    client = _make_client(tmp_path, core_root)
    _login(client)
    r = client.patch(
        "/core/concepts/99999999-9999-9999-9999-999999999999",
        json={"data": {}},
    )
    assert r.status_code == 404


def test_edit_record_allows_new_keys(tmp_path, core_root, monkeypatch):
    """The pre-overhaul "no new keys" rule is gone — per-type Pydantic
    models are the contract now. New keys pass through."""
    client = _make_client(tmp_path, core_root)
    _login(client, login="alice")

    def fake_github_json(method, path, token, **kwargs):
        if method == "GET" and path.startswith("/repos/bunkankun/bkk-core/contents/"):
            return {"type": "file", "sha": "base-sha", "content": _b64(CONCEPT_TEXT)}
        if method == "GET" and path == "/repos/alice/bkk-core":
            return {"full_name": "alice/bkk-core", "fork": True,
                    "parent": {"full_name": "bunkankun/bkk-core"}}
        if method == "GET" and path.startswith("/repos/alice/bkk-core/git/ref/heads/"):
            return {"object": {"sha": "branch-sha"}}
        if method == "PUT":
            return {"content": {"sha": "blob"}, "commit": {"sha": "cmt"}}
        if method == "GET" and "/pulls?" in path:
            return []
        raise AssertionError(f"unexpected call: {method} {path}")

    _patch_github(monkeypatch, fake_github_json)

    r = client.patch(
        f"/core/concepts/{CONCEPT_UUID}",
        json={
            "data": {
                "uuid": CONCEPT_UUID,
                "type": "concept",
                "concept": "TEST",
                "zh": "測試",
                "definition": "new def\n",
                "notes": "a brand new key\n",
            },
            "branch": "bkk-edit/concepts/x",
            "parent_sha": "base-sha",
        },
    )
    assert r.status_code == 200, r.json()
    assert r.json()["data"]["notes"] == "a brand new key\n"


def test_edit_record_rejects_locked_key_change(tmp_path, core_root, monkeypatch):
    client = _make_client(tmp_path, core_root)
    _login(client)

    def fake_github_json(method, path, token, **kwargs):
        if method == "GET" and path.startswith("/repos/bunkankun/bkk-core/contents/"):
            return {"type": "file", "sha": "base-sha", "content": _b64(CONCEPT_TEXT)}
        raise AssertionError(f"unexpected call: {method} {path}")

    _patch_github(monkeypatch, fake_github_json)

    r = client.patch(
        f"/core/concepts/{CONCEPT_UUID}",
        json={
            "data": {
                "uuid": "ffffffff-ffff-ffff-ffff-ffffffffffff",
                "type": "concept",
                "concept": "TEST",
                "zh": "測試",
            },
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
        if method == "GET" and path.startswith("/repos/bunkankun/bkk-core/contents/"):
            return {"type": "file", "sha": "upstream-blob-sha",
                    "content": _b64(CONCEPT_TEXT)}
        if method == "GET" and path == "/repos/alice/bkk-core":
            return {"full_name": "alice/bkk-core", "default_branch": "master",
                    "fork": True, "parent": {"full_name": "bunkankun/bkk-core"}}
        if method == "GET" and path == "/repos/alice/bkk-core/git/ref/heads/master":
            # Fork's base branch is ready (used by _fork_branch_ready).
            return {"object": {"sha": "fork-master-sha"}}
        if method == "GET" and path.startswith("/repos/alice/bkk-core/git/ref/heads/"):
            # Edit branch doesn't exist yet → triggers branch creation.
            from fastapi import HTTPException
            raise HTTPException(
                status_code=502,
                detail={"github_status": 404, "body": "Not Found"},
            )
        if method == "GET" and path == "/repos/bunkankun/bkk-core/git/ref/heads/master":
            return {"object": {"sha": "upstream-master-sha"}}
        if method == "POST" and path == "/repos/alice/bkk-core/git/refs":
            return {"ref": kwargs["json"]["ref"], "object": {"sha": kwargs["json"]["sha"]}}
        if (method == "GET" and "/repos/alice/bkk-core/contents/" in path
                and "bkk-edit" in path):
            return {"type": "file", "sha": "fork-blob-sha",
                    "content": _b64(CONCEPT_TEXT)}
        if method == "PUT" and path.startswith("/repos/alice/bkk-core/contents/"):
            return {"content": {"sha": "new-blob-sha"},
                    "commit": {"sha": "new-commit-sha"}}
        if method == "GET" and "/pulls?" in path:
            return []
        raise AssertionError(f"unexpected call: {method} {path}")

    _patch_github(monkeypatch, fake_github_json)

    r = client.patch(
        f"/core/concepts/{CONCEPT_UUID}",
        json={
            "data": {
                "uuid": CONCEPT_UUID,
                "type": "concept",
                "concept": "TEST",
                "zh": "测试",
                "definition": "edited definition\n",
            },
        },
    )

    assert r.status_code == 200, r.json()
    data = r.json()
    assert data["fork_repo"] == "alice/bkk-core"
    assert data["branch"].startswith(f"bkk-edit/concepts/{CONCEPT_UUID}-")
    assert data["commit_sha"] == "new-commit-sha"
    assert data["parent_sha"] == "new-blob-sha"
    assert data["pr_url"] is None
    assert data["data"]["zh"] == "测试"
    assert data["data"]["definition"] == "edited definition\n"
    assert data["extras"] == []

    put_calls = [c for c in calls if c[0] == "PUT"]
    assert len(put_calls) == 1
    put_json = put_calls[0][2]
    assert put_json["branch"] == data["branch"]
    assert put_json["sha"] == "fork-blob-sha"
    decoded = base64.b64decode(put_json["content"]).decode("utf-8")
    assert "concept: TEST" in decoded
    assert "zh: 测试" in decoded
    assert "edited definition" in decoded
    # No legacy frontmatter fence:
    assert not decoded.startswith("---")


def test_edit_record_reuses_supplied_branch_and_parent_sha(
    tmp_path, core_root, monkeypatch,
):
    client = _make_client(tmp_path, core_root)
    _login(client, login="alice", token="alice-tok")
    calls = []

    def fake_github_json(method, path, token, **kwargs):
        calls.append((method, path))
        if method == "GET" and path.startswith("/repos/bunkankun/bkk-core/contents/"):
            return {"type": "file", "sha": "upstream-sha",
                    "content": _b64(CONCEPT_TEXT)}
        if method == "GET" and path == "/repos/alice/bkk-core":
            return {"full_name": "alice/bkk-core", "fork": True,
                    "parent": {"full_name": "bunkankun/bkk-core"}}
        if method == "GET" and path.startswith("/repos/alice/bkk-core/git/ref/heads/"):
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
            "data": {
                "uuid": CONCEPT_UUID, "type": "concept", "concept": "TEST",
            },
            "branch": "bkk-edit/concepts/existing-branch",
            "parent_sha": "client-provided-sha",
        },
    )

    assert r.status_code == 200, r.json()
    assert not any(c == ("POST", "/repos/alice/bkk-core/git/refs") for c in calls)
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
            return {"full_name": "alice/bkk-core", "fork": True,
                    "parent": {"full_name": "bunkankun/bkk-core"}}
        if method == "GET" and path.startswith("/repos/alice/bkk-core/git/ref/heads/"):
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
            "data": {
                "uuid": CONCEPT_UUID, "type": "concept", "concept": "TEST",
            },
            "branch": "bkk-edit/concepts/x",
            "parent_sha": "stale-sha",
        },
    )
    assert r.status_code == 409
    assert "reload" in r.json()["detail"]


def test_edit_record_with_extra_files_creates_new_sense(
    tmp_path, core_root, monkeypatch,
):
    """A sense add: PATCH the word with updated sense_uuids, send the
    new sense YAML in extra_files. Verify both PUTs hit the same branch."""
    # Build a word fixture (so the index has a word to look up).
    word_uuid = "11111111-1111-1111-1111-111111111111"
    word_path = f"words/1/{word_uuid}.yml"
    word_record = {
        "uuid": word_uuid,
        "type": "word",
        "super_entry_uuid": "22222222-2222-2222-2222-222222222222",
        "sense_uuids": ["33333333-3333-3333-3333-333333333333"],
    }
    word_text = dumps_record(word_record)
    (core_root / "words" / "1").mkdir(parents=True)
    (core_root / word_path).write_text(word_text, encoding="utf-8")

    new_sense_uuid = "44444444-4444-4444-4444-444444444444"
    new_sense_path = f"senses/4/{new_sense_uuid}.yml"
    new_sense_record = {
        "uuid": new_sense_uuid,
        "type": "sense",
        "word_uuid": word_uuid,
        "definition": "a freshly minted sense\n",
    }
    updated_word = {**word_record, "sense_uuids": [
        "33333333-3333-3333-3333-333333333333", new_sense_uuid,
    ]}

    client = _make_client(tmp_path, core_root)
    _login(client, login="alice")
    put_calls: list[tuple[str, dict]] = []

    def fake_github_json(method, path, token, **kwargs):
        if method == "GET" and path.startswith("/repos/bunkankun/bkk-core/contents/"):
            return {"type": "file", "sha": "upstream-word-sha",
                    "content": _b64(word_text)}
        if method == "GET" and path == "/repos/alice/bkk-core":
            return {"full_name": "alice/bkk-core", "fork": True,
                    "parent": {"full_name": "bunkankun/bkk-core"}}
        if method == "GET" and path.startswith("/repos/alice/bkk-core/git/ref/heads/"):
            return {"object": {"sha": "branch-sha"}}
        if method == "PUT" and path.startswith("/repos/alice/bkk-core/contents/"):
            put_calls.append((path, kwargs["json"]))
            return {"content": {"sha": f"blob-{len(put_calls)}"},
                    "commit": {"sha": f"commit-{len(put_calls)}"}}
        if method == "GET" and "/pulls?" in path:
            return []
        raise AssertionError(f"unexpected call: {method} {path}")

    _patch_github(monkeypatch, fake_github_json)

    r = client.patch(
        f"/core/words/{word_uuid}",
        json={
            "data": updated_word,
            "branch": "bkk-edit/words/add-sense",
            "parent_sha": "upstream-word-sha",
            "extra_files": [
                {"path": new_sense_path, "data": new_sense_record},
            ],
        },
    )

    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["branch"] == "bkk-edit/words/add-sense"
    assert body["commit_sha"] == "commit-2"  # last commit (the sense)
    assert body["parent_sha"] == "blob-1"
    assert len(body["extras"]) == 1
    extra = body["extras"][0]
    assert extra["path"] == new_sense_path
    assert extra["commit_sha"] == "commit-2"
    assert extra["parent_sha"] == "blob-2"
    assert extra["deleted"] is False

    assert len(put_calls) == 2
    word_put_path, word_put = put_calls[0]
    sense_put_path, sense_put = put_calls[1]
    assert word_put_path.endswith(word_path)
    assert sense_put_path.endswith(new_sense_path)
    assert word_put["branch"] == "bkk-edit/words/add-sense"
    assert sense_put["branch"] == "bkk-edit/words/add-sense"
    sense_decoded = base64.b64decode(sense_put["content"]).decode("utf-8")
    assert "freshly minted sense" in sense_decoded


def test_edit_record_rejects_extra_files_with_bad_path(tmp_path, core_root, monkeypatch):
    client = _make_client(tmp_path, core_root)
    _login(client, login="alice")

    def fake_github_json(method, path, token, **kwargs):
        if method == "GET" and path.startswith("/repos/bunkankun/bkk-core/contents/"):
            return {"type": "file", "sha": "sha", "content": _b64(CONCEPT_TEXT)}
        # Bad-path validation happens before _ensure_fork so the fork
        # endpoints don't get hit.
        raise AssertionError(f"unexpected call: {method} {path}")

    _patch_github(monkeypatch, fake_github_json)

    r = client.patch(
        f"/core/concepts/{CONCEPT_UUID}",
        json={
            "data": {
                "uuid": CONCEPT_UUID, "type": "concept", "concept": "TEST",
            },
            "extra_files": [
                {"path": "../escape.yml", "data": {"uuid": "x", "type": "concept"}},
            ],
        },
    )
    assert r.status_code == 400
    assert "extra_files" in r.json()["detail"]


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
