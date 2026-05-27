"""GitHub auth endpoints expose session state without requiring OAuth in tests."""

from __future__ import annotations

from fastapi import HTTPException

from bkk.serve.routers import auth


def _workspace(login: str = "alice") -> dict:
    return {
        "repo": f"{login}/BKK-Workspace",
        "html_url": f"https://github.com/{login}/BKK-Workspace",
        "branch": login,
        "private": True,
    }


def test_auth_session_anonymous(client):
    r = client.get("/auth/session")
    assert r.status_code == 200
    assert r.json() == {"authenticated": False, "user": None}


def test_auth_start_requires_github_config(client):
    r = client.get("/auth/github/start", follow_redirects=False)
    assert r.status_code == 503
    assert "GitHub login is not configured" in r.json()["detail"]


def test_auth_session_returns_public_user(client):
    state = client.app.state.bkk
    session = state.sessions.create(
        login="alice",
        name="Alice",
        avatar_url="https://example.test/avatar.png",
        html_url="https://github.com/alice",
        access_token="secret-token",
        workspace=_workspace(),
    )
    client.cookies.set("bkk_session", session.id)

    r = client.get("/auth/session")
    assert r.status_code == 200
    body = r.json()
    assert body["authenticated"] is True
    assert body["user"]["login"] == "alice"
    assert body["user"]["workspace"]["branch"] == "alice"
    assert "access_token" not in body["user"]


def test_logout_drops_session_cookie(client):
    state = client.app.state.bkk
    session = state.sessions.create(
        login="alice",
        name=None,
        avatar_url=None,
        html_url=None,
        access_token="secret-token",
        workspace=_workspace(),
    )
    client.cookies.set("bkk_session", session.id)

    r = client.post("/auth/logout")
    assert r.status_code == 200
    assert state.sessions.get(session.id) is None


def test_workspace_bootstrap_uses_template_and_user_branch(client, monkeypatch):
    calls = []
    repo_exists_calls = 0

    def fake_repo_exists(token, owner, repo):
        nonlocal repo_exists_calls
        repo_exists_calls += 1
        if repo_exists_calls == 1:
            return None
        return {"default_branch": "main"}

    def fake_github_json(method, path, token, **kwargs):
        calls.append((method, path, kwargs))
        if method == "POST" and path == "/repos/bunkankun/BKK-Workspace/generate":
            return {"default_branch": "main"}
        if method == "GET" and path == "/repos/alice/BKK-Workspace/branches/alice":
            raise HTTPException(
                status_code=502,
                detail={"github_status": 404, "body": {"message": "Not Found"}},
            )
        if method == "GET" and path == "/repos/alice/BKK-Workspace/git/ref/heads/main":
            return {"object": {"sha": "abc123"}}
        return {}

    monkeypatch.setattr(auth, "_repo_exists", fake_repo_exists)
    monkeypatch.setattr(auth, "_github_json", fake_github_json)
    monkeypatch.setattr(auth.time, "sleep", lambda _seconds: None)

    workspace = auth._workspace_for_user(client.app.state.bkk, "token", "alice")

    assert workspace == _workspace()
    assert calls[0] == (
        "POST",
        "/repos/bunkankun/BKK-Workspace/generate",
        {
            "json": {
                "owner": "alice",
                "name": "BKK-Workspace",
                "private": True,
                "include_all_branches": False,
            }
        },
    )
    assert (
        "POST",
        "/repos/alice/BKK-Workspace/git/refs",
        {"json": {"ref": "refs/heads/alice", "sha": "abc123"}},
    ) in calls
    assert (
        "PATCH",
        "/repos/alice/BKK-Workspace",
        {"json": {"default_branch": "alice", "private": True}},
    ) in calls


def test_workspace_bootstrap_rejects_preexisting_empty_repo(client, monkeypatch):
    def fake_repo_exists(token, owner, repo):
        return {"default_branch": "main"}

    def fake_github_json(method, path, token, **kwargs):
        if method == "GET" and path == "/repos/alice/BKK-Workspace/git/ref/heads/main":
            raise HTTPException(
                status_code=502,
                detail={
                    "github_status": 409,
                    "body": {"message": "Git Repository is empty."},
                },
            )
        return {}

    monkeypatch.setattr(auth, "_repo_exists", fake_repo_exists)
    monkeypatch.setattr(auth, "_github_json", fake_github_json)

    try:
        auth._workspace_for_user(client.app.state.bkk, "token", "alice")
    except HTTPException as exc:
        assert exc.status_code == 409
        assert "already exists but is empty" in exc.detail
    else:
        raise AssertionError("expected empty preexisting workspace repo to fail")


def test_workspace_bootstrap_retries_transient_ref_409(client, monkeypatch):
    calls = []
    ref_attempts = 0

    def fake_repo_exists(token, owner, repo):
        return {"default_branch": "main"}

    def fake_github_json(method, path, token, **kwargs):
        nonlocal ref_attempts
        calls.append((method, path, kwargs))
        if method == "GET" and path == "/repos/alice/BKK-Workspace/branches/alice":
            raise HTTPException(
                status_code=502,
                detail={"github_status": 404, "body": {"message": "Not Found"}},
            )
        if method == "GET" and path == "/repos/alice/BKK-Workspace/git/ref/heads/main":
            ref_attempts += 1
            if ref_attempts == 1:
                raise HTTPException(
                    status_code=502,
                    detail={
                        "github_status": 409,
                        "body": {"message": "Git Repository is empty."},
                    },
                )
            return {"object": {"sha": "ready-sha"}}
        return {}

    monkeypatch.setattr(auth, "_repo_exists", fake_repo_exists)
    monkeypatch.setattr(auth, "_github_json", fake_github_json)
    monkeypatch.setattr(auth.time, "sleep", lambda _seconds: None)

    workspace = auth._workspace_for_user(client.app.state.bkk, "token", "alice")

    assert workspace == _workspace()
    assert ref_attempts == 2
    assert (
        "POST",
        "/repos/alice/BKK-Workspace/git/refs",
        {"json": {"ref": "refs/heads/alice", "sha": "ready-sha"}},
    ) in calls
