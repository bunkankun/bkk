"""Per-process app state: corpus root + lazily-built corpus index."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from bkk.index import Index, merge_bundles
from bkk.index.merge import find_bundle

from .catalog import CatalogService
from .config import ServeConfig
from .resolver import BundleRecord, CorpusCache, IdentifierResolver

log = logging.getLogger("bkk.serve")


JobStatus = Literal["pending", "running", "success", "error"]


@dataclass
class Job:
    """One admin background task. Lives only in process memory."""

    id: str
    kind: str
    target: str | None
    status: JobStatus = "pending"
    started_at: float | None = None
    finished_at: float | None = None
    result: Any | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "target": self.target,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result": self.result,
            "error": self.error,
        }


class JobRegistry:
    """Thread-safe in-memory registry. Discarded on server restart."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def create(self, kind: str, target: str | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex, kind=kind, target=target)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def mark_running(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "running"
            job.started_at = time.time()

    def mark_done(self, job_id: str, result: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "success"
            job.result = result
            job.finished_at = time.time()

    def mark_error(self, job_id: str, exc: BaseException) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.finished_at = time.time()


@dataclass
class BlueskySession:
    """Stored in process memory only; never persisted, never logged."""

    did: str
    handle: str
    access_jwt: str
    refresh_jwt: str
    service_endpoint: str
    created_at: float = field(default_factory=time.time)


@dataclass
class UserSession:
    id: str
    login: str
    name: str | None
    avatar_url: str | None
    html_url: str | None
    access_token: str
    workspace: dict[str, Any]
    bluesky: BlueskySession | None = None
    created_at: float = field(default_factory=time.time)

    def public_dict(self) -> dict[str, Any]:
        return {
            "login": self.login,
            "name": self.name,
            "avatar_url": self.avatar_url,
            "html_url": self.html_url,
            "workspace": self.workspace,
            "bluesky": (
                {"did": self.bluesky.did, "handle": self.bluesky.handle}
                if self.bluesky is not None
                else None
            ),
        }


class SessionRegistry:
    """Thread-safe in-memory GitHub login sessions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, UserSession] = {}

    def create(
        self,
        *,
        login: str,
        name: str | None,
        avatar_url: str | None,
        html_url: str | None,
        access_token: str,
        workspace: dict[str, Any],
    ) -> UserSession:
        session = UserSession(
            id=uuid.uuid4().hex,
            login=login,
            name=name,
            avatar_url=avatar_url,
            html_url=html_url,
            access_token=access_token,
            workspace=workspace,
        )
        with self._lock:
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str | None) -> UserSession | None:
        if not session_id:
            return None
        with self._lock:
            return self._sessions.get(session_id)

    def delete(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)

    def attach_bluesky(self, session_id: str, bluesky: BlueskySession) -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            session.bluesky = bluesky
            return True

    def detach_bluesky(self, session_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.bluesky = None

    def update_bluesky_tokens(
        self, session_id: str, *, access_jwt: str, refresh_jwt: str,
    ) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.bluesky is None:
                return
            session.bluesky.access_jwt = access_jwt
            session.bluesky.refresh_jwt = refresh_jwt


@dataclass
class AppState:
    config: ServeConfig
    _index_built: bool = False
    _index_error: str | None = None
    _cache: CorpusCache | None = field(default=None, repr=False)
    _bundle_records: dict[str, BundleRecord] = field(default_factory=dict, repr=False)
    jobs: JobRegistry = field(default_factory=JobRegistry, repr=False)
    sessions: SessionRegistry = field(default_factory=SessionRegistry, repr=False)

    @property
    def corpus_root(self) -> Path:
        return self.config.corpus_root

    @property
    def index_path(self) -> Path:
        return self.config.index_path

    @property
    def catalog_path(self) -> Path | None:
        return self.config.catalog_path

    @property
    def translation_search_path(self) -> Path | None:
        return self.config.translation_search_path

    @property
    def annotations_root(self) -> Path | None:
        return self.config.annotations_root

    @property
    def core_root(self) -> Path | None:
        return self.config.core_root

    @property
    def core_index_path(self) -> Path | None:
        return self.config.core_index_path

    @property
    def cache(self) -> CorpusCache:
        if self._cache is None:
            self._cache = CorpusCache(self.corpus_root)
        return self._cache

    @property
    def resolver(self) -> IdentifierResolver:
        return IdentifierResolver(self.cache)

    def ensure_index(self) -> Path | None:
        """Return the index path if available, else ``None``.

        Builds the merged corpus index on first call when the file is missing;
        records the failure on the state if the build raises so subsequent
        calls return ``None`` quickly instead of retrying a failing build.
        """
        if self.index_path.exists():
            return self.index_path
        if self._index_error is not None:
            return None
        log.info("building merged corpus index at %s", self.index_path)
        try:
            merge_bundles(self.corpus_root, self.index_path)
            self._index_built = True
        except Exception as exc:
            self._index_error = f"{type(exc).__name__}: {exc}"
            log.warning("corpus index build failed: %s", self._index_error)
            return None
        return self.index_path

    def open_index(self) -> Index | None:
        """Open a read-only handle on the corpus index, or ``None`` if absent."""
        path = self.ensure_index()
        if path is None:
            return None
        return Index(path)

    def open_catalog(self) -> sqlite3.Connection | None:
        """Open a read-only handle on the catalog index, or ``None`` if absent."""
        path = self.catalog_path
        if path is None or not path.exists():
            return None
        try:
            return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        except sqlite3.DatabaseError as exc:
            log.warning("catalog index unavailable at %s: %s", path, exc)
            return None

    def open_core(self) -> sqlite3.Connection | None:
        """Open a read-only handle on the core .bkki index, or ``None`` if absent."""
        path = self.core_index_path
        if path is None or not path.exists():
            return None
        try:
            return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        except sqlite3.DatabaseError as exc:
            log.warning("core index unavailable at %s: %s", path, exc)
            return None

    def open_translation_search(self) -> sqlite3.Connection | None:
        """Open a read-only handle on the translation search index, or ``None`` if absent."""
        path = self.translation_search_path
        if path is None or not path.exists():
            return None
        try:
            return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        except sqlite3.DatabaseError as exc:
            log.warning("translation search index unavailable at %s: %s", path, exc)
            return None

    def lookup_bundle(self, textid: str) -> BundleRecord | None:
        """Return one bundle by textid without building the full corpus snapshot."""
        cached = self._bundle_records.get(textid)
        if cached is not None:
            try:
                if cached.manifest_path.stat().st_mtime == cached.mtime:
                    return cached
            except FileNotFoundError:
                pass
            self._bundle_records.pop(textid, None)

        bundle_dir = find_bundle(self.corpus_root, textid)
        if bundle_dir is None:
            return None
        manifest_path = bundle_dir / f"{textid}.manifest.yaml"
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            log.warning("bundle manifest unavailable at %s: %s", manifest_path, exc)
            return None
        if not isinstance(manifest, dict):
            log.warning("bundle manifest is not a mapping: %s", manifest_path)
            return None
        rec = BundleRecord(
            textid=textid,
            bundle_dir=bundle_dir,
            manifest_path=manifest_path,
            manifest=manifest,
            mtime=manifest_path.stat().st_mtime,
        )
        self._bundle_records[textid] = rec
        return rec
