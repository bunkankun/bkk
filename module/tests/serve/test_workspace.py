"""GitHub-backed workspace API behavior."""

from __future__ import annotations

import base64

import pytest
from fastapi import HTTPException

from bkk.serve.routers import workspace as workspace_router


def _session(client):
    state = client.app.state.bkk
    session = state.sessions.create(
        login="alice",
        name=None,
        avatar_url=None,
        html_url=None,
        access_token="secret-token",
        workspace={
            "repo": "alice/BKK-Workspace",
            "html_url": "https://github.com/alice/BKK-Workspace",
            "branch": "alice",
            "private": True,
        },
    )
    client.cookies.set("bkk_session", session.id)
    return session


@pytest.mark.parametrize(
    "path",
    ["../settings/ui.json", "/settings/ui.json", "private/ui.json", "settings/../x"],
)
def test_workspace_path_validation_rejects_unsafe_paths(path):
    with pytest.raises(HTTPException):
        workspace_router._normalize_workspace_path(path)


def test_workspace_path_validation_allows_workspace_roots():
    assert workspace_router._normalize_workspace_path("settings/ui.json") == "settings/ui.json"
    assert workspace_router._normalize_workspace_path("notes/n1.md") == "notes/n1.md"
    assert workspace_router._normalize_workspace_path("searches/s1.json") == "searches/s1.json"
    assert workspace_router._normalize_workspace_path("lists/favorites.txt") == "lists/favorites.txt"
    assert (
        workspace_router._normalize_workspace_path("locations/20260707T120000000.yaml")
        == "locations/20260707T120000000.yaml"
    )


def test_workspace_read_uses_user_repo_and_branch(client, monkeypatch):
    _session(client)
    calls = []

    def fake_github_json(method, path, token, **kwargs):
        calls.append((method, path, token, kwargs))
        return {
            "type": "file",
            "path": "settings/ui.json",
            "sha": "sha1",
            "content": base64.b64encode(b'{"theme":"dark"}').decode("ascii"),
        }

    monkeypatch.setattr(workspace_router, "_github_json", fake_github_json)

    r = client.get("/workspace/files/settings/ui.json")

    assert r.status_code == 200
    assert r.json()["content"] == '{"theme":"dark"}'
    assert calls == [
        (
            "GET",
            "/repos/alice/BKK-Workspace/contents/settings/ui.json?ref=alice",
            "secret-token",
            {},
        )
    ]


def test_workspace_write_requires_current_sha_for_existing_file(client, monkeypatch):
    _session(client)

    def fake_github_json(method, path, token, **kwargs):
        if method == "GET":
            return {
                "type": "file",
                "path": "settings/ui.json",
                "sha": "remote-sha",
                "content": base64.b64encode(b"{}").decode("ascii"),
            }
        raise AssertionError("PUT should not run when sha is stale")

    monkeypatch.setattr(workspace_router, "_github_json", fake_github_json)

    r = client.put(
        "/workspace/files/settings/ui.json",
        json={"content": "{}", "sha": "old-sha"},
    )

    assert r.status_code == 409
    assert "reload before saving" in r.json()["detail"]


def test_workspace_write_uses_user_repo_branch_and_sha(client, monkeypatch):
    _session(client)
    calls = []

    def fake_github_json(method, path, token, **kwargs):
        calls.append((method, path, token, kwargs))
        if method == "GET":
            return {
                "type": "file",
                "path": "settings/ui.json",
                "sha": "remote-sha",
                "content": base64.b64encode(b"{}").decode("ascii"),
            }
        return {"content": {"sha": "new-sha"}, "commit": {"sha": "commit-sha"}}

    monkeypatch.setattr(workspace_router, "_github_json", fake_github_json)

    r = client.put(
        "/workspace/files/settings/ui.json",
        json={"content": '{"theme":"light"}', "sha": "remote-sha"},
    )

    assert r.status_code == 200
    assert r.json()["sha"] == "new-sha"
    assert calls[1] == (
        "PUT",
        "/repos/alice/BKK-Workspace/contents/settings/ui.json",
        "secret-token",
        {
            "json": {
                "message": "Update BKK workspace file: settings/ui.json",
                "content": base64.b64encode(b'{"theme":"light"}').decode("ascii"),
                "branch": "alice",
                "sha": "remote-sha",
            }
        },
    )


def test_workspace_delete_uses_user_repo_branch_and_sha(client, monkeypatch):
    session = _session(client)
    calls = []

    def fake_github_json(method, path, token, **kwargs):
        calls.append((method, path, token, kwargs))
        if method == "GET":
            return {
                "type": "file",
                "path": "lists/favorites.txt",
                "sha": "remote-sha",
                "content": base64.b64encode(b"KR1a0001\n").decode("ascii"),
            }
        return {"commit": {"sha": "commit-sha"}}

    monkeypatch.setattr(workspace_router, "_github_json", fake_github_json)

    class Request:
        cookies = {"bkk_session": session.id}
        app = client.app

    body = workspace_router.delete_file(
        Request(),
        "lists/favorites.txt",
        sha="remote-sha",
    )

    assert body["path"] == "lists/favorites.txt"
    assert calls[1] == (
        "DELETE",
        "/repos/alice/BKK-Workspace/contents/lists/favorites.txt",
        "secret-token",
        {
            "json": {
                "message": "Delete BKK workspace file: lists/favorites.txt",
                "sha": "remote-sha",
                "branch": "alice",
            }
        },
    )
