"""Direct-to-master editing of core records."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bkk.index.core import build_core_index
from bkk.serialize.yaml_io import dumps_record, loads_record
from bkk.serve import create_app
from bkk.serve.config import ServeConfig
from bkk.serve.routers import auth as auth_router
from bkk.serve.routers import core_edit as core_edit_router


def _patch_github(monkeypatch, fake):
    """Patch ``_github_json`` everywhere it's bound."""
    monkeypatch.setattr(core_edit_router, "_github_json", fake)
    monkeypatch.setattr(auth_router, "_github_json", fake)


def _disable_background_sync(monkeypatch):
    """Stop the post-edit fast-forward+rebuild from running in tests."""
    monkeypatch.setattr(
        core_edit_router, "_schedule_background_sync", lambda *a, **kw: None,
    )


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


def _login(
    client: TestClient,
    login: str = "alice",
    token: str = "tok",
    is_editor: bool = True,
):
    state = client.app.state.bkk
    session = state.sessions.create(
        login=login, name=None, avatar_url=None, html_url=None,
        access_token=token,
        workspace={"repo": f"{login}/BKK-Workspace", "branch": login,
                   "html_url": "", "private": True},
        is_editor=is_editor,
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


def test_edit_record_requires_editor_role(tmp_path, core_root):
    client = _make_client(tmp_path, core_root)
    _login(client, is_editor=False)
    r = client.patch(
        f"/core/concepts/{CONCEPT_UUID}",
        json={"data": {}},
    )
    assert r.status_code == 403


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
    client = _make_client(tmp_path, core_root)
    _login(client)
    _disable_background_sync(monkeypatch)

    def fake_github_json(method, path, token, **kwargs):
        if method == "GET" and path.startswith("/repos/bunkankun/bkk-core/contents/"):
            return {"type": "file", "sha": "upstream-sha", "content": _b64(CONCEPT_TEXT)}
        if method == "PUT":
            return {
                "content": {"sha": "new-blob"},
                "commit": {
                    "sha": "new-commit",
                    "html_url": "https://github.com/bunkankun/bkk-core/commit/new-commit",
                },
            }
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
        },
    )
    assert r.status_code == 200, r.json()
    assert r.json()["data"]["notes"] == "a brand new key\n"


def test_edit_record_rejects_locked_key_change(tmp_path, core_root, monkeypatch):
    client = _make_client(tmp_path, core_root)
    _login(client)
    _disable_background_sync(monkeypatch)

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


def test_edit_record_commits_directly_to_master(
    tmp_path, core_root, monkeypatch,
):
    client = _make_client(tmp_path, core_root)
    _login(client, login="alice", token="alice-tok")
    _disable_background_sync(monkeypatch)
    calls = []

    def fake_github_json(method, path, token, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        if method == "GET" and path.startswith("/repos/bunkankun/bkk-core/contents/"):
            return {"type": "file", "sha": "upstream-sha", "content": _b64(CONCEPT_TEXT)}
        if method == "PUT" and path.startswith("/repos/bunkankun/bkk-core/contents/"):
            return {
                "content": {"sha": "new-blob-sha"},
                "commit": {
                    "sha": "new-commit-sha",
                    "html_url": "https://github.com/bunkankun/bkk-core/commit/new-commit-sha",
                },
            }
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
    assert data["commit_sha"] == "new-commit-sha"
    assert data["commit_url"].endswith("/commit/new-commit-sha")
    assert data["data"]["zh"] == "测试"
    assert data["data"]["definition"] == "edited definition\n"
    assert data["extras"] == []
    # No fork-side keys leak into the response.
    assert "branch" not in data
    assert "fork_repo" not in data
    assert "pr_url" not in data

    put_calls = [c for c in calls if c[0] == "PUT"]
    assert len(put_calls) == 1
    _, put_path, put_json = put_calls[0]
    assert put_path.startswith("/repos/bunkankun/bkk-core/contents/")
    assert put_json["branch"] == "master"
    assert put_json["sha"] == "upstream-sha"
    decoded = base64.b64decode(put_json["content"]).decode("utf-8")
    assert "concept: TEST" in decoded
    assert "zh: 测试" in decoded
    assert "edited definition" in decoded


def test_edit_record_writes_through_to_local_clone_and_index(
    tmp_path, core_root, monkeypatch,
):
    """The committed YAML must land in the local clone and the SQLite index
    must reflect the new display label before the background sync runs."""
    client = _make_client(tmp_path, core_root)
    _login(client)
    _disable_background_sync(monkeypatch)

    def fake_github_json(method, path, token, **kwargs):
        if method == "GET" and path.startswith("/repos/bunkankun/bkk-core/contents/"):
            return {"type": "file", "sha": "upstream-sha", "content": _b64(CONCEPT_TEXT)}
        if method == "PUT":
            return {
                "content": {"sha": "new-blob"},
                "commit": {
                    "sha": "new-commit",
                    "html_url": "https://github.com/bunkankun/bkk-core/commit/new-commit",
                },
            }
        raise AssertionError(f"unexpected call: {method} {path}")

    _patch_github(monkeypatch, fake_github_json)

    r = client.patch(
        f"/core/concepts/{CONCEPT_UUID}",
        json={
            "data": {
                "uuid": CONCEPT_UUID,
                "type": "concept",
                "concept": "RENAMED",
                "definition": "rewritten\n",
            },
        },
    )
    assert r.status_code == 200, r.json()

    # 1) local YAML file matches the committed bytes
    on_disk = (core_root / CONCEPT_PATH).read_text(encoding="utf-8")
    on_disk_record = loads_record(on_disk)
    assert on_disk_record["concept"] == "RENAMED"
    assert on_disk_record["definition"] == "rewritten\n"

    # 2) the SQLite index reflects the new display label without a full rebuild
    import sqlite3
    conn = sqlite3.connect(core_root / "_core.bkki")
    try:
        row = conn.execute(
            "SELECT display_label FROM notes WHERE uuid = ?", (CONCEPT_UUID,),
        ).fetchone()
        labels = [r[0] for r in conn.execute(
            "SELECT label FROM labels WHERE uuid = ?", (CONCEPT_UUID,),
        )]
    finally:
        conn.close()
    assert row is not None
    assert row[0] == "RENAMED"
    assert "RENAMED" in labels
    assert "TEST" not in labels  # old label gone


def test_edit_record_translates_github_conflict_to_409(
    tmp_path, core_root, monkeypatch,
):
    client = _make_client(tmp_path, core_root)
    _login(client)
    _disable_background_sync(monkeypatch)

    def fake_github_json(method, path, token, **kwargs):
        if method == "GET" and path.startswith("/repos/bunkankun/bkk-core/contents/"):
            return {"type": "file", "sha": "upstream-sha", "content": _b64(CONCEPT_TEXT)}
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
        },
    )
    assert r.status_code == 409
    assert "reload" in r.json()["detail"]


def test_edit_record_with_extra_files_creates_new_sense(
    tmp_path, core_root, monkeypatch,
):
    """A sense add: PATCH the word with updated sense_uuids, send the
    new sense YAML in extra_files. Both PUTs hit upstream master."""
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
    _disable_background_sync(monkeypatch)
    put_calls: list[tuple[str, dict]] = []

    def fake_github_json(method, path, token, **kwargs):
        if method == "GET" and "/repos/bunkankun/bkk-core/contents/" in path:
            if word_path in path:
                return {"type": "file", "sha": "upstream-word-sha",
                        "content": _b64(word_text)}
            if new_sense_path in path:
                # New sense doesn't exist upstream yet — treat as 404.
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=502,
                    detail={"github_status": 404, "body": "Not Found"},
                )
        if method == "PUT" and path.startswith("/repos/bunkankun/bkk-core/contents/"):
            put_calls.append((path, kwargs["json"]))
            return {
                "content": {"sha": f"blob-{len(put_calls)}"},
                "commit": {
                    "sha": f"commit-{len(put_calls)}",
                    "html_url": f"https://example.com/commit-{len(put_calls)}",
                },
            }
        raise AssertionError(f"unexpected call: {method} {path}")

    _patch_github(monkeypatch, fake_github_json)

    r = client.patch(
        f"/core/words/{word_uuid}",
        json={
            "data": updated_word,
            "extra_files": [
                {"path": new_sense_path, "data": new_sense_record},
            ],
        },
    )

    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["commit_sha"] == "commit-2"  # last commit (the sense)
    assert len(body["extras"]) == 1
    extra = body["extras"][0]
    assert extra["path"] == new_sense_path
    assert extra["commit_sha"] == "commit-2"
    assert extra["deleted"] is False

    assert len(put_calls) == 2
    word_put_path, word_put = put_calls[0]
    sense_put_path, sense_put = put_calls[1]
    assert word_put_path.endswith(word_path)
    assert sense_put_path.endswith(new_sense_path)
    assert word_put["branch"] == "master"
    assert sense_put["branch"] == "master"
    # New sense file has no parent sha (didn't exist upstream).
    assert "sha" not in sense_put
    sense_decoded = base64.b64decode(sense_put["content"]).decode("utf-8")
    assert "freshly minted sense" in sense_decoded

    # Local write-through landed both files.
    assert (core_root / new_sense_path).is_file()
    assert "a freshly minted sense" in (core_root / new_sense_path).read_text("utf-8")


def test_edit_record_rejects_extra_files_with_bad_path(tmp_path, core_root, monkeypatch):
    client = _make_client(tmp_path, core_root)
    _login(client, login="alice")
    _disable_background_sync(monkeypatch)

    def fake_github_json(method, path, token, **kwargs):
        if method == "GET" and path.startswith("/repos/bunkankun/bkk-core/contents/"):
            return {"type": "file", "sha": "sha", "content": _b64(CONCEPT_TEXT)}
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


def test_delete_record_commits_delete_to_master(tmp_path, core_root, monkeypatch):
    client = _make_client(tmp_path, core_root)
    _login(client)
    _disable_background_sync(monkeypatch)

    def fake_github_json(method, path, token, **kwargs):
        if method == "GET" and path.startswith("/repos/bunkankun/bkk-core/contents/"):
            return {"type": "file", "sha": "upstream-sha", "content": _b64(CONCEPT_TEXT)}
        if method == "DELETE":
            return {
                "commit": {
                    "sha": "delete-commit",
                    "html_url": "https://github.com/bunkankun/bkk-core/commit/delete-commit",
                },
            }
        raise AssertionError(f"unexpected call: {method} {path}")

    _patch_github(monkeypatch, fake_github_json)

    r = client.request(
        "DELETE",
        f"/core/concepts/{CONCEPT_UUID}",
        json={},
    )
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["commit_sha"] == "delete-commit"
    assert body["commit_url"].endswith("/commit/delete-commit")

    # Local file and index row are gone immediately.
    assert not (core_root / CONCEPT_PATH).exists()
    import sqlite3
    conn = sqlite3.connect(core_root / "_core.bkki")
    try:
        row = conn.execute(
            "SELECT 1 FROM notes WHERE uuid = ?", (CONCEPT_UUID,),
        ).fetchone()
    finally:
        conn.close()
    assert row is None


def test_delete_record_requires_editor_role(tmp_path, core_root):
    client = _make_client(tmp_path, core_root)
    _login(client, is_editor=False)
    r = client.delete(f"/core/concepts/{CONCEPT_UUID}")
    assert r.status_code == 403


def test_open_pr_endpoint_is_gone(tmp_path, core_root):
    """The PR endpoint was retired with the move to direct-to-master."""
    client = _make_client(tmp_path, core_root)
    _login(client)
    r = client.post(
        f"/core/concepts/{CONCEPT_UUID}/pr",
        json={"branch": "anything"},
    )
    assert r.status_code in (404, 405)
