"""GitHub login + per-user BKK workspace bootstrap."""

from __future__ import annotations

import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlparse

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..state import AppState, UserSession

router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_COOKIE = "bkk_session"
OAUTH_STATE_COOKIE = "bkk_oauth_state"
GITHUB_API = "https://api.github.com"
REF_READY_ATTEMPTS = 12


def _state(request: Request) -> AppState:
    return request.app.state.bkk


def _origin(request: Request) -> str:
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    return f"{request.url.scheme}://{request.url.netloc}"


def _callback_url(request: Request) -> str:
    config = _state(request).config
    if config.github_callback_url:
        return config.github_callback_url
    return f"{_origin(request)}/api/auth/github/callback"


def _require_github_config(state: AppState) -> tuple[str, str]:
    client_id = state.config.github_client_id
    client_secret = state.config.github_client_secret
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=503,
            detail=(
                "GitHub login is not configured. Set BKK_GITHUB_CLIENT_ID and "
                "BKK_GITHUB_CLIENT_SECRET."
            ),
        )
    return client_id, client_secret


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "bkk-serve",
    }


def _github_json(method: str, path: str, token: str, **kwargs: Any) -> Any:
    url = path if path.startswith("https://") else f"{GITHUB_API}{path}"
    try:
        r = requests.request(
            method,
            url,
            headers=_github_headers(token),
            timeout=30,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"GitHub request failed: {exc}") from exc
    if r.status_code >= 400:
        detail: Any
        try:
            detail = r.json()
        except ValueError:
            detail = r.text
        raise HTTPException(
            status_code=502,
            detail={"github_status": r.status_code, "body": detail},
        )
    if not r.content:
        return None
    return r.json()


def _repo_exists(token: str, owner: str, repo: str) -> dict[str, Any] | None:
    try:
        return _github_json("GET", f"/repos/{owner}/{repo}", token)
    except HTTPException as exc:
        detail = exc.detail
        if (
            exc.status_code == 502
            and isinstance(detail, dict)
            and detail.get("github_status") == 404
        ):
            return None
        raise


def _get_branch_ref(
    *,
    token: str,
    owner: str,
    repo: str,
    branch: str,
    attempts: int = REF_READY_ATTEMPTS,
) -> dict[str, Any]:
    last_exc: HTTPException | None = None
    for i in range(attempts):
        try:
            return _github_json(
                "GET",
                f"/repos/{owner}/{repo}/git/ref/heads/{branch}",
                token,
            )
        except HTTPException as exc:
            last_exc = exc
            if _github_status(exc) != 409 or i == attempts - 1:
                raise
            time.sleep(1)
    assert last_exc is not None
    raise last_exc


def _repo_is_empty(token: str, owner: str, repo: str, default_branch: str) -> bool:
    try:
        _get_branch_ref(token=token, owner=owner, repo=repo, branch=default_branch)
    except HTTPException as exc:
        return _github_status(exc) == 409
    return False


def _github_status(exc: HTTPException) -> int | None:
    detail = exc.detail
    if exc.status_code == 502 and isinstance(detail, dict):
        status = detail.get("github_status")
        if isinstance(status, int):
            return status
    return None


def _split_repo(full_name: str) -> tuple[str, str]:
    parts = full_name.split("/", 1)
    if len(parts) != 2 or not all(parts):
        raise HTTPException(
            status_code=500,
            detail=f"Invalid repository name: {full_name}",
        )
    return parts[0], parts[1]


def _ensure_workspace_repo(
    *,
    token: str,
    login: str,
    template_repo: str,
    workspace_repo_name: str,
) -> dict[str, Any]:
    existing = _repo_exists(token, login, workspace_repo_name)
    if existing is not None:
        default_branch = existing.get("default_branch") or "main"
        if _repo_is_empty(token, login, workspace_repo_name, default_branch):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{login}/{workspace_repo_name} already exists but is empty. "
                    "Delete that repository and log in again so BKK can create it "
                    f"from {template_repo}."
                ),
            )
        return existing

    template_owner, template_name = _split_repo(template_repo)
    generated = _github_json(
        "POST",
        f"/repos/{template_owner}/{template_name}/generate",
        token,
        json={
            "owner": login,
            "name": workspace_repo_name,
            "private": True,
            "include_all_branches": False,
        },
    )
    for _ in range(10):
        repo = _repo_exists(token, login, workspace_repo_name)
        if repo is not None:
            return repo
        time.sleep(1)
    return generated


def _ensure_user_default_branch(
    *,
    token: str,
    login: str,
    repo: str,
    branch: str,
    default_branch: str,
) -> None:
    if branch != default_branch:
        try:
            _github_json("GET", f"/repos/{login}/{repo}/branches/{branch}", token)
        except HTTPException as exc:
            if _github_status(exc) != 404:
                raise
            ref = _get_branch_ref(
                token=token,
                owner=login,
                repo=repo,
                branch=default_branch,
            )
            sha = ref.get("object", {}).get("sha")
            if not isinstance(sha, str):
                raise HTTPException(
                    status_code=502,
                    detail="GitHub default branch ref has no SHA",
                )
            _github_json(
                "POST",
                f"/repos/{login}/{repo}/git/refs",
                token,
                json={"ref": f"refs/heads/{branch}", "sha": sha},
            )
    _github_json(
        "PATCH",
        f"/repos/{login}/{repo}",
        token,
        json={"default_branch": branch, "private": True},
    )


def _workspace_for_user(state: AppState, token: str, login: str) -> dict[str, Any]:
    repo_name = state.config.workspace_repo_name
    repo = _ensure_workspace_repo(
        token=token,
        login=login,
        template_repo=state.config.workspace_template_repo,
        workspace_repo_name=repo_name,
    )
    default_branch = repo.get("default_branch") or "main"
    _ensure_user_default_branch(
        token=token,
        login=login,
        repo=repo_name,
        branch=login,
        default_branch=default_branch,
    )
    return {
        "repo": f"{login}/{repo_name}",
        "html_url": f"https://github.com/{login}/{repo_name}",
        "branch": login,
        "private": True,
    }


def _is_team_member(token: str, team_path: str, login: str) -> bool:
    """Return True iff ``login`` is an active member of ``team_path`` (``org/slug``).

    Treats 403/404 as "not a member" (covers both genuine non-membership and the
    case where the OAuth token lacks ``read:org`` scope or the team is hidden).
    Re-raises other GitHub errors.
    """
    org, _, slug = team_path.partition("/")
    if not org or not slug:
        return False
    try:
        body = _github_json(
            "GET",
            f"/orgs/{org}/teams/{slug}/memberships/{login}",
            token,
        )
    except HTTPException as exc:
        if _github_status(exc) in (403, 404):
            return False
        raise
    return bool(body) and body.get("state") == "active"


def _session_from_request(request: Request) -> UserSession | None:
    return _state(request).sessions.get(request.cookies.get(SESSION_COOKIE))


@router.get("/session", summary="Current GitHub login + workspace status")
def session(request: Request) -> dict[str, Any]:
    user_session = _session_from_request(request)
    return {
        "authenticated": user_session is not None,
        "user": user_session.public_dict() if user_session else None,
    }


@router.get("/github/start", summary="Start GitHub OAuth login")
def github_start(request: Request) -> RedirectResponse:
    state = _state(request)
    client_id, _ = _require_github_config(state)
    oauth_state = secrets.token_urlsafe(24)
    params = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": _callback_url(request),
            "scope": "repo read:user read:org",
            "state": oauth_state,
        }
    )
    response = RedirectResponse(
        f"https://github.com/login/oauth/authorize?{params}",
        status_code=302,
    )
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        oauth_state,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=600,
    )
    return response


@router.get("/github/callback", summary="Complete GitHub OAuth login")
def github_callback(request: Request, code: str, state: str) -> RedirectResponse:
    app_state = _state(request)
    client_id, client_secret = _require_github_config(app_state)
    expected_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not expected_state or not secrets.compare_digest(expected_state, state):
        raise HTTPException(status_code=400, detail="Invalid GitHub OAuth state")

    try:
        token_response = requests.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json", "User-Agent": "bkk-serve"},
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": _callback_url(request),
            },
            timeout=30,
        )
        token_response.raise_for_status()
        token_data = token_response.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"GitHub token exchange failed: {exc}") from exc
    access_token = token_data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise HTTPException(
            status_code=502,
            detail="GitHub did not return an access token",
        )

    user = _github_json("GET", "/user", access_token)
    login = user.get("login")
    if not isinstance(login, str) or not login:
        raise HTTPException(status_code=502, detail="GitHub user payload has no login")

    workspace = _workspace_for_user(app_state, access_token, login)
    is_admin = _is_team_member(access_token, app_state.config.admin_team, login)
    user_session = app_state.sessions.create(
        login=login,
        name=user.get("name") if isinstance(user.get("name"), str) else None,
        avatar_url=(
            user.get("avatar_url")
            if isinstance(user.get("avatar_url"), str)
            else None
        ),
        html_url=user.get("html_url") if isinstance(user.get("html_url"), str) else None,
        access_token=access_token,
        workspace=workspace,
        is_admin=is_admin,
    )

    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(OAUTH_STATE_COOKIE)
    response.set_cookie(
        SESSION_COOKIE,
        user_session.id,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=60 * 60 * 24 * 30,
    )
    return response


@router.post("/logout", summary="Log out of the current BKK session")
def logout(request: Request) -> JSONResponse:
    _state(request).sessions.delete(request.cookies.get(SESSION_COOKIE))
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE)
    return response
