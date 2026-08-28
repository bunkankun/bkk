"""Remote bundle reads for ``bkk serve``.

The v1 remote path keeps GitHub I/O behind a small file-like service so the
bundle router can read manifests, juan YAML, and declared assets without
assuming an on-disk bundle directory.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING, Literal
from urllib.parse import quote

import requests
import yaml
from fastapi import HTTPException

from bkk.marker_assets import hydrate_juan_markers, marker_asset_entry_for_seq

from . import errors, selection
from .resolver import BundleRecord
from .routers.auth import GITHUB_API, SESSION_COOKIE, _github_status
from .state import UserSession

if TYPE_CHECKING:
    from fastapi import Request

    from .state import AppState

log = logging.getLogger("bkk.serve.remote_bundles")

BundleOrigin = Literal[
    "remote-user", "remote-canonical", "local-user", "local-corpus"
]

_TEXTID_RE = r"KR\d+[a-z]\d{4}"


@dataclass
class _CacheEntry:
    value: Any
    expires_at: float


class GitHubBundleClient:
    """Small GitHub API client with a short TTL cache."""

    def __init__(self, ttl_s: float = 60.0):
        self.ttl_s = max(0.0, ttl_s)
        self._cache: dict[tuple[Any, ...], _CacheEntry] = {}

    def _token_key(self, token: str | None) -> str:
        if not token:
            return "anonymous"
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _cached(self, key: tuple[Any, ...]) -> Any | None:
        if self.ttl_s <= 0:
            return None
        entry = self._cache.get(key)
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            self._cache.pop(key, None)
            return None
        return entry.value

    def _store(self, key: tuple[Any, ...], value: Any) -> Any:
        if self.ttl_s > 0:
            self._cache[key] = _CacheEntry(
                value=value,
                expires_at=time.monotonic() + self.ttl_s,
            )
        return value

    def json(
        self,
        method: str,
        path: str,
        token: str | None,
        *,
        cache: bool = True,
        expected_statuses: set[int] | None = None,
        **kwargs: Any,
    ) -> Any:
        key = (
            "json",
            method,
            path,
            self._token_key(token),
            tuple(sorted((expected_statuses or set()))),
        )
        if method == "GET" and cache:
            cached = self._cached(key)
            if cached is not None:
                return cached
        result = self._request_json(
            method, path, token, expected_statuses=expected_statuses or set(), **kwargs
        )
        if method == "GET" and cache:
            return self._store(key, result)
        return result

    def _request_json(
        self,
        method: str,
        path: str,
        token: str | None,
        *,
        expected_statuses: set[int],
        **kwargs: Any,
    ) -> Any:
        url = path if path.startswith("https://") else f"{GITHUB_API}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "bkk-serve",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                timeout=30,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise HTTPException(
                status_code=502, detail=f"GitHub request failed: {exc}"
            ) from exc
        if response.status_code >= 400:
            try:
                detail: Any = response.json()
            except ValueError:
                detail = response.text
            log_method = (
                log.debug if response.status_code in expected_statuses else log.warning
            )
            log_method(
                "GitHub API error: %s %s -> %s: %r",
                method,
                path,
                response.status_code,
                detail,
            )
            raise HTTPException(
                status_code=502,
                detail={"github_status": response.status_code, "body": detail},
            )
        if not response.content:
            return None
        return response.json()

    def repo_default_branch(self, repo: str, token: str | None) -> str:
        payload = self.json("GET", f"/repos/{repo}", token)
        branch = (payload or {}).get("default_branch")
        if not isinstance(branch, str) or not branch:
            raise HTTPException(
                status_code=502,
                detail=f"GitHub repository {repo} has no default branch",
            )
        return branch

    def head_sha(self, repo: str, branch: str, token: str | None) -> str:
        payload = self.json(
            "GET", f"/repos/{repo}/git/ref/heads/{quote(branch, safe='')}", token
        )
        sha = ((payload or {}).get("object") or {}).get("sha")
        if not isinstance(sha, str):
            raise HTTPException(
                status_code=502,
                detail=f"GitHub branch response for {repo}@{branch} has no SHA",
            )
        return sha

    def fetch_file(
        self, repo: str, ref: str, path: str, token: str | None
    ) -> tuple[dict[str, Any], bytes]:
        payload = self.json(
            "GET",
            f"/repos/{repo}/contents/{quote(path, safe='/')}?ref={quote(ref, safe='')}",
            token,
        )
        if not isinstance(payload, dict) or payload.get("type") != "file":
            raise HTTPException(status_code=404, detail=f"{path} not found in {repo}")
        if isinstance(payload.get("content"), str) and payload["content"]:
            return payload, _decode_bytes(payload, path)
        blob_sha = payload.get("sha")
        if not isinstance(blob_sha, str):
            raise HTTPException(
                status_code=502,
                detail=f"GitHub file {path} has no blob SHA",
            )
        blob = self.json("GET", f"/repos/{repo}/git/blobs/{blob_sha}", token)
        if not isinstance(blob, dict):
            raise HTTPException(
                status_code=502, detail=f"GitHub blob for {path} is invalid"
            )
        return payload, _decode_bytes(blob, path)

    def fetch_text(self, repo: str, ref: str, path: str, token: str | None) -> str:
        _payload, raw = self.fetch_file(repo, ref, path, token)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                status_code=502, detail=f"GitHub file {path} is not UTF-8"
            ) from exc

    def tree(self, repo: str, ref: str, token: str | None) -> list[dict[str, Any]]:
        payload = self.json(
            "GET", f"/repos/{repo}/git/trees/{quote(ref, safe='')}?recursive=1", token
        )
        tree = (payload or {}).get("tree") or []
        return [item for item in tree if isinstance(item, dict)]

    def list_repos(
        self, path: str, token: str | None, *, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        repos: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            sep = "&" if "?" in path else "?"
            payload = self.json(
                "GET",
                f"{path}{sep}per_page=100&page={page}",
                token,
            )
            if not isinstance(payload, list) or not payload:
                break
            repos.extend(item for item in payload if isinstance(item, dict))
            if len(payload) < 100:
                break
        return repos


def _decode_bytes(payload: dict[str, Any], path: str) -> bytes:
    content = payload.get("content")
    if not isinstance(content, str):
        raise HTTPException(status_code=502, detail=f"GitHub file {path} has no content")
    try:
        return base64.b64decode(content, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"invalid GitHub blob {path}") from exc


@dataclass(frozen=True)
class RemoteBundleRecord:
    textid: str
    repo: str
    branch: str
    ref: str
    token: str | None
    origin: BundleOrigin
    manifest: dict[str, Any]
    client: GitHubBundleClient

    def fetch_yaml(self, path: str) -> dict[str, Any]:
        text = self.client.fetch_text(self.repo, self.ref, path, self.token)
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise HTTPException(status_code=422, detail=f"{path} is not a mapping")
        return data

    def fetch_bytes(self, path: str) -> tuple[dict[str, Any], bytes]:
        return self.client.fetch_file(self.repo, self.ref, path, self.token)

    def manifest_for_edition(self, edition: str | None) -> tuple[str, dict[str, Any]]:
        if edition is None:
            return "", self.manifest
        if not selection._EDITION_RE.fullmatch(edition):
            raise errors.bad_request("bad_edition", edition=edition)
        prefix = f"editions/{edition}/"
        path = f"{prefix}{self.textid}-{edition}.manifest.yaml"
        try:
            return prefix, self.fetch_yaml(path)
        except HTTPException as exc:
            if _github_status(exc) == 404 or exc.status_code == 404:
                raise errors.bad_request(
                    "edition_not_found", textid=self.textid, edition=edition
                ) from exc
            raise

    def load_juan(self, seq: int, edition: str | None = None) -> dict[str, Any]:
        prefix, manifest = self.manifest_for_edition(edition)
        parts = (manifest.get("assets") or {}).get("parts") or []
        entry = next(
            (p for p in parts if isinstance(p, dict) and p.get("seq") == seq),
            None,
        )
        if entry is None or not isinstance(entry.get("filename"), str):
            raise errors.juan_not_found(self.textid, seq)
        juan = self.fetch_yaml(f"{prefix}{entry['filename']}")
        marker_asset: dict[str, Any] | None = None
        marker_entry = marker_asset_entry_for_seq(manifest, seq)
        if marker_entry is not None and isinstance(marker_entry.get("filename"), str):
            try:
                marker_asset = self.fetch_yaml(f"{prefix}{marker_entry['filename']}")
            except HTTPException as exc:
                if _github_status(exc) != 404 and exc.status_code != 404:
                    raise
        return hydrate_juan_markers(juan, marker_asset)

    def available_editions(self) -> list[dict[str, str | None]]:
        declared: dict[str, str | None] = {}
        for entry in self.manifest.get("editions") or []:
            if not isinstance(entry, dict):
                continue
            short = entry.get("short")
            if not isinstance(short, str) or not short:
                continue
            label = entry.get("label")
            declared[short] = label if isinstance(label, str) and label else None
        wanted = {
            f"editions/{short}/{self.textid}-{short}.manifest.yaml": short
            for short in declared
        }
        out: list[dict[str, str | None]] = []
        try:
            paths = {
                item.get("path")
                for item in self.client.tree(self.repo, self.ref, self.token)
                if item.get("type") == "blob"
            }
        except HTTPException:
            paths = set()
        for path, short in sorted(wanted.items(), key=lambda p: p[1]):
            if path in paths:
                out.append({"short": short, "label": declared.get(short)})
        return out


@dataclass(frozen=True)
class ResolvedBundle:
    textid: str
    manifest: dict[str, Any]
    origin: BundleOrigin
    local: BundleRecord | None = None
    remote: RemoteBundleRecord | None = None
    fallback_from_remote: bool = False
    remote_error: str | None = None

    @property
    def is_remote(self) -> bool:
        return self.remote is not None

    @property
    def bundle_dir(self) -> Path:
        if self.local is None:
            raise RuntimeError("remote bundle has no local directory")
        return self.local.bundle_dir

    @property
    def repo(self) -> str | None:
        return self.remote.repo if self.remote is not None else None

    @property
    def ref(self) -> str | None:
        return self.remote.ref if self.remote is not None else None

    def load_juan(self, seq: int, edition: str | None = None) -> dict[str, Any]:
        if self.remote is not None:
            return self.remote.load_juan(seq, edition)
        assert self.local is not None
        return selection.load_juan_file_for_edition(
            self.local.bundle_dir, self.manifest, self.textid, seq, edition
        )

    def manifest_for_edition(self, edition: str | None) -> tuple[str, dict[str, Any]]:
        if self.remote is not None:
            return self.remote.manifest_for_edition(edition)
        assert self.local is not None
        bundle_dir, manifest = selection.load_manifest_for_edition_scope(
            self.local.bundle_dir, self.manifest, self.textid, edition
        )
        return str(bundle_dir), manifest

    def available_editions(self) -> list[dict[str, str | None]]:
        if self.remote is not None:
            return self.remote.available_editions()
        assert self.local is not None
        from .routers.bundles import _available_editions

        return _available_editions(self.local.bundle_dir, self.textid, self.manifest)

    def asset_bytes(self, name: str) -> tuple[dict[str, Any], bytes] | None:
        if self.remote is None:
            return None
        return self.remote.fetch_bytes(name)


def resolve_bundle(request: "Request", textid: str) -> ResolvedBundle | None:
    state: AppState = request.app.state.bkk
    session = _session_from_request(request)
    owner = session.login if session is not None else None
    if state.config.bundle_load_mode == "prefer_local":
        local = _local_visible(state, textid, owner)
        if local is not None:
            return local
        return _remote_visible(state, textid, session)

    if owner is not None:
        private = state.lookup_user_text(owner, textid)
        if private is not None:
            return _from_local_record(state, private, owner)

    has_remote_credentials = bool(state.config.github_read_token) or (
        session is not None and bool(state.config.github_client_id)
    )
    if not has_remote_credentials:
        return _local_visible(state, textid, owner)

    if (
        session is None
        and not state.config.github_read_token
        and state.lookup_bundle(textid) is not None
    ):
        return _local_visible(state, textid, owner)

    remote_error: Exception | None = None
    try:
        remote = _remote_visible(state, textid, session)
        if remote is not None:
            return remote
        if _valid_repo_name(textid):
            remote_error = FileNotFoundError("remote bundle not found")
    except Exception as exc:
        remote_error = exc
    local = _local_visible(state, textid, owner)
    if local is not None:
        return ResolvedBundle(
            textid=local.textid,
            manifest=local.manifest,
            origin=local.origin,
            local=local.local,
            fallback_from_remote=remote_error is not None,
            remote_error=str(remote_error) if remote_error else None,
        )
    if isinstance(remote_error, HTTPException):
        raise remote_error
    return None


def visible_bundles(request: "Request") -> list[ResolvedBundle]:
    state: AppState = request.app.state.bkk
    session = _session_from_request(request)
    owner = session.login if session is not None else None
    if (
        state.config.bundle_load_mode == "prefer_local"
        or (
            not state.config.github_read_token
            and not (session is not None and state.config.github_client_id)
        )
    ):
        return [_from_local_record(state, r, owner) for r in state.visible_bundle_records(owner)]

    records: dict[str, ResolvedBundle] = {}
    try:
        for rec in _remote_records_for_listing(state, session):
            records.setdefault(rec.textid, rec)
    except Exception as exc:
        log.warning("remote bundle listing failed; falling back to local: %s", exc)
        return [_from_local_record(state, r, owner) for r in state.visible_bundle_records(owner)]
    for rec in state.visible_bundle_records(owner):
        records.setdefault(rec.textid, _from_local_record(state, rec, owner))
    return sorted(records.values(), key=lambda r: r.textid)


def _session_from_request(request: "Request") -> UserSession | None:
    return request.app.state.bkk.sessions.get(request.cookies.get(SESSION_COOKIE))


def _from_local_record(
    state: "AppState", rec: BundleRecord, owner: str | None,
) -> ResolvedBundle:
    origin: BundleOrigin = "local-corpus"
    if owner is not None:
        try:
            rec.bundle_dir.relative_to(state.user_texts_root / owner)
        except ValueError:
            pass
        else:
            origin = "local-user"
    return ResolvedBundle(
        textid=rec.textid,
        manifest=rec.manifest,
        origin=origin,
        local=rec,
    )


def _local_visible(
    state: "AppState", textid: str, owner: str | None,
) -> ResolvedBundle | None:
    rec = state.lookup_visible_bundle(textid, owner)
    return _from_local_record(state, rec, owner) if rec is not None else None


def _client(state: "AppState") -> GitHubBundleClient:
    client = getattr(state, "_remote_github_client", None)
    ttl = state.config.remote_cache_ttl_s
    if not isinstance(client, GitHubBundleClient) or client.ttl_s != max(0.0, ttl):
        client = GitHubBundleClient(ttl)
        setattr(state, "_remote_github_client", client)
    return client


def _remote_visible(
    state: "AppState", textid: str, session: UserSession | None,
) -> ResolvedBundle | None:
    if not _valid_repo_name(textid):
        return None
    client = _client(state)
    if session is not None and state.config.github_client_id:
        user = _load_remote_bundle(
            client,
            repo=f"{session.login}/{textid}",
            token=session.access_token,
            branch="auto",
            textid=textid,
            origin="remote-user",
            expected_missing=True,
        )
        if user is not None:
            return user
    canonical_token = state.config.github_read_token or (
        session.access_token
        if session is not None and state.config.github_client_id
        else None
    )
    return _load_remote_bundle(
        client,
        repo=f"{state.config.bundle_github_org}/{textid}",
        token=canonical_token,
        branch=state.config.bundle_github_branch,
        textid=textid,
        origin="remote-canonical",
        expected_missing=True,
    )


def _load_remote_bundle(
    client: GitHubBundleClient,
    *,
    repo: str,
    token: str | None,
    branch: str,
    textid: str,
    origin: BundleOrigin,
    expected_missing: bool,
) -> ResolvedBundle | None:
    try:
        resolved_branch = client.repo_default_branch(repo, token) if branch == "auto" else branch
        ref = client.head_sha(repo, resolved_branch, token)
        manifest_text = client.fetch_text(repo, ref, f"{textid}.manifest.yaml", token)
    except HTTPException as exc:
        if expected_missing and (_github_status(exc) == 404 or exc.status_code == 404):
            return None
        raise
    manifest = yaml.safe_load(manifest_text) or {}
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=422, detail=f"{repo}: manifest is not a mapping")
    remote = RemoteBundleRecord(
        textid=textid,
        repo=repo,
        branch=resolved_branch,
        ref=ref,
        token=token,
        origin=origin,
        manifest=manifest,
        client=client,
    )
    return ResolvedBundle(
        textid=textid,
        manifest=manifest,
        origin=origin,
        remote=remote,
    )


def _remote_records_for_listing(
    state: "AppState", session: UserSession | None,
) -> list[ResolvedBundle]:
    client = _client(state)
    out: list[ResolvedBundle] = []
    if session is not None and state.config.github_client_id:
        for repo in client.list_repos("/user/repos?type=owner", session.access_token):
            name = repo.get("name")
            full_name = repo.get("full_name")
            if isinstance(name, str) and isinstance(full_name, str) and _valid_repo_name(name):
                loaded = _load_remote_bundle(
                    client,
                    repo=full_name,
                    token=session.access_token,
                    branch="auto",
                    textid=name,
                    origin="remote-user",
                    expected_missing=True,
                )
                if loaded is not None:
                    out.append(loaded)
    token = state.config.github_read_token or (
        session.access_token
        if session is not None and state.config.github_client_id
        else None
    )
    for repo in client.list_repos(f"/orgs/{state.config.bundle_github_org}/repos?type=all", token):
        name = repo.get("name")
        full_name = repo.get("full_name")
        if isinstance(name, str) and isinstance(full_name, str) and _valid_repo_name(name):
            loaded = _load_remote_bundle(
                client,
                repo=full_name,
                token=token,
                branch=state.config.bundle_github_branch,
                textid=name,
                origin="remote-canonical",
                expected_missing=True,
            )
            if loaded is not None:
                out.append(loaded)
    return out


def _valid_repo_name(value: str) -> bool:
    import re

    return re.fullmatch(_TEXTID_RE, value) is not None
