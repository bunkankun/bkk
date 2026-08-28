"""Remote translation bundle reads and cache refresh for ``bkk serve``."""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING, Literal

from fastapi import HTTPException

from bkk.index.catalog import refresh_translation_catalog
from bkk.index.translation import merge_translations

from . import errors
from .remote_bundles import GitHubBundleClient, _client
from .routers.auth import SESSION_COOKIE, _github_status
from .state import UserSession
from .translations import (
    TranslationBundle,
    TranslationJuan,
    list_translation_bundles,
    load_translation_bundle,
    load_translation_bundle_from_catalog,
    read_frontmatter_text,
    read_translation_juan_text,
    _summary,
)

if TYPE_CHECKING:
    from fastapi import Request

    from .state import AppState

log = logging.getLogger("bkk.serve.remote_translations")

TranslationOrigin = Literal["remote-user", "remote-canonical", "local"]

_SOURCE_ID_RE = re.compile(r"bkk:[^/]+/(KR\d+[a-z]\d{4})/")
_SOURCE_TEXTID_RE = re.compile(r"KR\d+[a-z]\d{4}")
_TRANSLATION_ID_RE = re.compile(r"KR\d+[a-z]\d{4}-[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class ResolvedTranslationBundle:
    bundle: TranslationBundle
    origin: TranslationOrigin
    repo: str | None = None
    ref: str | None = None
    fallback_from_remote: bool = False
    remote_error: str | None = None


def resolve_translation_bundle(
    request: "Request",
    translation_id: str,
    *,
    source_textid: str | None = None,
    include_juans: bool = True,
) -> ResolvedTranslationBundle | None:
    state: AppState = request.app.state.bkk
    session = _session_from_request(request)
    if state.config.bundle_load_mode == "prefer_local":
        local = _load_local_translation(
            state,
            translation_id,
            source_textid=source_textid,
            include_juans=include_juans,
        )
        if local is not None:
            return local
        return _load_remote_translation_visible(
            state,
            session,
            translation_id,
            source_textid=source_textid,
            include_juans=include_juans,
        )

    read_token = (
        state.config.translation_github_read_token or state.config.github_read_token
    )
    has_remote_credentials = bool(read_token) or (
        session is not None and bool(state.config.github_client_id)
    )
    if not has_remote_credentials:
        return _load_local_translation(
            state,
            translation_id,
            source_textid=source_textid,
            include_juans=include_juans,
        )

    remote_error: Exception | None = None
    try:
        remote = _load_remote_translation_visible(
            state,
            session,
            translation_id,
            source_textid=source_textid,
            include_juans=include_juans,
        )
        if remote is not None:
            return remote
        if _valid_translation_repo_name(translation_id):
            remote_error = FileNotFoundError("remote translation not found")
    except Exception as exc:
        remote_error = exc

    local = _load_local_translation(
        state,
        translation_id,
        source_textid=source_textid,
        include_juans=include_juans,
    )
    if local is not None:
        return ResolvedTranslationBundle(
            bundle=local.bundle,
            origin=local.origin,
            fallback_from_remote=remote_error is not None,
            remote_error=str(remote_error) if remote_error else None,
        )
    if isinstance(remote_error, HTTPException):
        raise remote_error
    return None


def refresh_remote_translations(state: "AppState", *, token: str | None) -> dict[str, Any]:
    """Mirror canonical remote translation repos into the local cache and rebuild indexes."""
    if not token:
        raise errors.bad_request(
            "github_token_missing",
            reason=(
                "set serve.translation_github_read_token, serve.github_read_token, "
                "or log in with GitHub before refreshing translations"
            ),
        )
    cache_root = state.config.translation_remote_cache_root
    if cache_root is None:
        raise errors.bad_request(
            "translation_remote_cache_root_missing",
            reason="set serve.translation_remote_cache_root",
        )
    client = _client(state)
    org = state.config.translation_github_org
    repos = client.list_repos(f"/orgs/{org}/repos?type=all", token)
    refreshed: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for repo_row in repos:
        name = repo_row.get("name")
        full_name = repo_row.get("full_name")
        if not isinstance(name, str) or not isinstance(full_name, str):
            continue
        if not _valid_translation_repo_name(name):
            continue
        try:
            refreshed.append(
                _sync_remote_translation_repo(
                    client,
                    repo=full_name,
                    translation_id=name,
                    branch=state.config.translation_github_branch,
                    token=token,
                    cache_root=cache_root,
                )
            )
        except Exception as exc:
            log.warning("translation refresh skipped %s: %s", full_name, exc)
            skipped.append({"repo": full_name, "error": str(exc)})

    catalog_path = None
    if state.catalog_path is not None:
        catalog_path = refresh_translation_catalog(
            cache_root,
            state.catalog_path,
            translation_root=cache_root,
        )
    search_path = None
    if state.translation_search_path is not None:
        search_path = merge_translations(
            cache_root,
            state.translation_search_path,
            translation_root=cache_root,
            rebuild=True,
        )
    return {
        "translation_remote_cache_root": str(cache_root),
        "translation_github_org": org,
        "refreshed": refreshed,
        "skipped": skipped,
        "catalog_path": str(catalog_path) if catalog_path is not None else None,
        "translation_search_path": str(search_path) if search_path is not None else None,
    }


def _load_local_translation(
    state: "AppState",
    translation_id: str,
    *,
    source_textid: str | None,
    include_juans: bool,
) -> ResolvedTranslationBundle | None:
    conn = state.open_catalog()
    if conn is not None:
        try:
            bundle = load_translation_bundle_from_catalog(
                conn,
                translation_id=translation_id,
                source_textid=source_textid,
                include_juans=include_juans,
            )
        except Exception as exc:
            log.warning("catalog translation load failed for %s: %s", translation_id, exc)
            bundle = None
        finally:
            conn.close()
        if bundle is not None:
            return ResolvedTranslationBundle(bundle=bundle, origin="local")
    for bundle in list_translation_bundles(
        state.corpus_root,
        translation_root_path=state.config.translation_root,
        source_textid=source_textid,
    ):
        if bundle.id != translation_id:
            continue
        loaded = (
            load_translation_bundle(bundle.path, include_juans=True)
            if include_juans
            else bundle
        )
        return ResolvedTranslationBundle(bundle=loaded, origin="local")
    cache_root = state.config.translation_remote_cache_root
    if cache_root is not None:
        for bundle in list_translation_bundles(
            state.corpus_root,
            translation_root_path=cache_root,
            source_textid=source_textid,
        ):
            if bundle.id != translation_id:
                continue
            loaded = (
                load_translation_bundle(bundle.path, include_juans=True)
                if include_juans
                else bundle
            )
            return ResolvedTranslationBundle(bundle=loaded, origin="local")
    return None


def _load_remote_translation_visible(
    state: "AppState",
    session: UserSession | None,
    translation_id: str,
    *,
    source_textid: str | None,
    include_juans: bool,
) -> ResolvedTranslationBundle | None:
    if not _valid_translation_repo_name(translation_id):
        return None
    client = _client(state)
    if (
        session is not None
        and state.config.github_client_id
        and (
            not session.repo_inventory_ready
            or translation_id in session.user_translation_repos
        )
    ):
        user = _load_remote_translation(
            client,
            repo=f"{session.login}/{translation_id}",
            token=session.access_token,
            branch="auto",
            translation_id=translation_id,
            origin="remote-user",
            source_textid=source_textid,
            include_juans=include_juans,
            expected_missing=True,
        )
        if user is not None:
            return user
    token = state.config.translation_github_read_token or state.config.github_read_token or (
        session.access_token
        if session is not None and state.config.github_client_id
        else None
    )
    return _load_remote_translation(
        client,
        repo=f"{state.config.translation_github_org}/{translation_id}",
        token=token,
        branch=state.config.translation_github_branch,
        translation_id=translation_id,
        origin="remote-canonical",
        source_textid=source_textid,
        include_juans=include_juans,
        expected_missing=True,
    )


def _load_remote_translation(
    client: GitHubBundleClient,
    *,
    repo: str,
    token: str | None,
    branch: str,
    translation_id: str,
    origin: TranslationOrigin,
    source_textid: str | None,
    include_juans: bool,
    expected_missing: bool,
) -> ResolvedTranslationBundle | None:
    try:
        resolved_branch = client.repo_default_branch(repo, token) if branch == "auto" else branch
        ref = client.head_sha(repo, resolved_branch, token)
        manifest_raw = client.fetch_text(repo, ref, f"{translation_id}.md", token)
    except HTTPException as exc:
        if expected_missing and (_github_status(exc) == 404 or exc.status_code == 404):
            return None
        raise
    manifest, _body = read_frontmatter_text(manifest_raw)
    actual_source = _source_textid_from_manifest_or_id(translation_id, manifest)
    if source_textid is not None and actual_source != source_textid:
        return None
    bundle = _translation_bundle_from_manifest(
        client,
        repo=repo,
        ref=ref,
        token=token,
        translation_id=translation_id,
        manifest=manifest,
        source_textid=actual_source,
        include_juans=include_juans,
    )
    return ResolvedTranslationBundle(
        bundle=bundle,
        origin=origin,
        repo=repo,
        ref=ref,
    )


def _translation_bundle_from_manifest(
    client: GitHubBundleClient,
    *,
    repo: str,
    ref: str,
    token: str | None,
    translation_id: str,
    manifest: dict[str, Any],
    source_textid: str,
    include_juans: bool,
) -> TranslationBundle:
    juans: dict[int, TranslationJuan] = {}
    source_juans: list[int] = []
    segment_count = 0
    for entry in manifest.get("juan") or []:
        if not isinstance(entry, dict):
            continue
        seq = entry.get("seq")
        label = entry.get("label")
        filename = entry.get("file")
        if not isinstance(seq, int) or not isinstance(filename, str):
            continue
        try:
            source_seq = int(label)
            source_juans.append(source_seq)
        except (TypeError, ValueError):
            source_seq = seq
        if not include_juans:
            if isinstance(entry.get("segs"), int):
                segment_count += entry["segs"]
            continue
        raw = client.fetch_text(repo, ref, filename, token)
        juan = read_translation_juan_text(Path(filename), raw)
        juans[source_seq] = juan
        segment_count += len(juan.segments)
    summary = _summary(
        translation_id,
        manifest,
        source_textid,
        len(manifest.get("juan") or []) if not include_juans else len(juans),
        segment_count,
        source_juans,
    )
    return TranslationBundle(
        id=translation_id,
        path=Path("/__remote_translations__") / repo / translation_id,
        manifest=manifest,
        source_textid=source_textid,
        summary=summary,
        juans=juans,
    )


def _sync_remote_translation_repo(
    client: GitHubBundleClient,
    *,
    repo: str,
    translation_id: str,
    branch: str,
    token: str | None,
    cache_root: Path,
) -> dict[str, Any]:
    resolved_branch = client.repo_default_branch(repo, token) if branch == "auto" else branch
    ref = client.head_sha(repo, resolved_branch, token)
    manifest_raw = client.fetch_text(repo, ref, f"{translation_id}.md", token)
    manifest, _body = read_frontmatter_text(manifest_raw)
    source_textid = _source_textid_from_manifest_or_id(translation_id, manifest)
    language = manifest.get("language") if isinstance(manifest.get("language"), str) else "und"
    bundle_dir = _cache_bundle_dir(cache_root, source_textid, language, translation_id)
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    fetched = 0
    for item in client.tree(repo, ref, token):
        if item.get("type") != "blob":
            continue
        path = item.get("path")
        if not isinstance(path, str) or not _safe_repo_path(path):
            continue
        _payload, raw = client.fetch_file(repo, ref, path, token)
        dest = bundle_dir / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        fetched += 1
    if not (bundle_dir / f"{translation_id}.md").is_file():
        (bundle_dir / f"{translation_id}.md").write_text(manifest_raw, encoding="utf-8")
        fetched += 1
    return {
        "id": translation_id,
        "source_textid": source_textid,
        "language": language,
        "repo": repo,
        "ref": ref,
        "path": str(bundle_dir),
        "files": fetched,
    }


def _cache_bundle_dir(
    cache_root: Path,
    source_textid: str,
    language: str,
    translation_id: str,
) -> Path:
    section = source_textid[:4] if _SOURCE_TEXTID_RE.fullmatch(source_textid) else "_unknown"
    return cache_root / section / source_textid / language / translation_id


def _source_textid_from_manifest_or_id(
    translation_id: str,
    manifest: dict[str, Any],
) -> str:
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    cid = source.get("canonical_identifier")
    if isinstance(cid, str):
        match = _SOURCE_ID_RE.match(cid)
        if match:
            return match.group(1)
    match = _SOURCE_TEXTID_RE.match(translation_id)
    return match.group(0) if match else "_unknown"


def _session_from_request(request: "Request") -> UserSession | None:
    return request.app.state.bkk.sessions.get(request.cookies.get(SESSION_COOKIE))


def _safe_repo_path(path: str) -> bool:
    candidate = Path(path)
    return not candidate.is_absolute() and ".." not in candidate.parts


def _valid_translation_repo_name(value: str) -> bool:
    return _TRANSLATION_ID_RE.fullmatch(value) is not None
