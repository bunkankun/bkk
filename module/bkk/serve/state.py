"""Per-process app state: corpus root + lazily-built corpus index."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from bkk.index import Index, merge_bundles

from .catalog import CatalogService
from .config import ServeConfig
from .resolver import CorpusCache, IdentifierResolver

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
class AppState:
    config: ServeConfig
    _index_built: bool = False
    _index_error: str | None = None
    _cache: CorpusCache | None = field(default=None, repr=False)
    jobs: JobRegistry = field(default_factory=JobRegistry, repr=False)

    @property
    def corpus_root(self) -> Path:
        return self.config.corpus_root

    @property
    def index_path(self) -> Path:
        return self.config.index_path

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
