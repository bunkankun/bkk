"""Corpus snapshot + identifier-to-bundle resolution.

A :class:`CorpusSnapshot` is a one-shot read of every master manifest under
the corpus root: the in-memory shape that both the resolver and the catalog
consult. Snapshots carry the mtime of every manifest they read so a cheap
stat walk can decide whether a refresh is warranted.

:class:`IdentifierResolver` indexes each manifest under three classes of key:
the bundle's directory name (``textid``), its ``canonical_identifier``, and
every value under ``metadata.identifiers.*`` (``krp``, ``cbeta``, each
element of ``slug``). A single key may resolve to more than one bundle when
distinct editions of the same text are kept side-by-side; callers are
responsible for the collision UX.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from bkk.index.merge import discover_bundles

log = logging.getLogger("bkk.serve")


@dataclass(frozen=True)
class BundleRef:
    """Lightweight pointer to a discovered bundle."""

    textid: str
    bundle_dir: Path
    canonical_identifier: str | None
    title: str | None
    edition_short: str | None
    base_edition: str | None


@dataclass
class BundleRecord:
    """Full snapshot of one bundle's manifest, indexed by the snapshot."""

    textid: str
    bundle_dir: Path
    manifest_path: Path
    manifest: dict[str, Any]
    mtime: float

    @property
    def canonical_identifier(self) -> str | None:
        return self.manifest.get("canonical_identifier")

    @property
    def manifest_hash(self) -> str | None:
        return self.manifest.get("hash")

    @property
    def metadata(self) -> dict[str, Any]:
        return self.manifest.get("metadata") or {}

    @property
    def title(self) -> str | None:
        return self.metadata.get("title")

    @property
    def alt_titles(self) -> list[str]:
        v = self.metadata.get("alt_titles") or []
        return [str(x) for x in v] if isinstance(v, list) else []

    @property
    def edition_short(self) -> str | None:
        ed = self.metadata.get("edition") or {}
        return ed.get("short") if isinstance(ed, dict) else None

    @property
    def base_edition(self) -> str | None:
        v = self.metadata.get("base_edition")
        if isinstance(v, dict):
            return v.get("short")
        return v if isinstance(v, str) else None

    @property
    def identifiers(self) -> dict[str, Any]:
        v = self.metadata.get("identifiers") or {}
        return v if isinstance(v, dict) else {}

    @property
    def tags(self) -> dict[str, Any]:
        v = self.metadata.get("tags") or {}
        return v if isinstance(v, dict) else {}

    @property
    def authors(self) -> list[dict[str, Any]]:
        v = self.metadata.get("authors") or []
        return [a for a in v if isinstance(a, dict)] if isinstance(v, list) else []

    @property
    def composition_period(self) -> str | None:
        return self.metadata.get("composition_period")

    @property
    def source(self) -> dict[str, Any] | str | None:
        return self.metadata.get("source")

    @property
    def editions(self) -> list[dict[str, Any]]:
        v = self.manifest.get("editions") or []
        return [e for e in v if isinstance(e, dict)] if isinstance(v, list) else []

    def to_ref(self) -> BundleRef:
        return BundleRef(
            textid=self.textid,
            bundle_dir=self.bundle_dir,
            canonical_identifier=self.canonical_identifier,
            title=self.title,
            edition_short=self.edition_short,
            base_edition=self.base_edition,
        )


@dataclass
class CorpusSnapshot:
    records: list[BundleRecord]
    by_textid: dict[str, BundleRecord]
    by_identifier: dict[str, list[BundleRecord]]
    built_at: float
    mtimes: dict[Path, float] = field(default_factory=dict)


def _key(value: str) -> str:
    return value.strip()


def _index_keys(rec: BundleRecord) -> list[str]:
    keys: list[str] = [rec.textid]
    if rec.canonical_identifier:
        keys.append(rec.canonical_identifier)
    for key, value in rec.identifiers.items():
        if isinstance(value, str):
            keys.append(value)
        elif isinstance(value, list):
            keys.extend(str(v) for v in value if isinstance(v, (str, int)))
    # de-duplicate while preserving insertion order
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        nk = _key(k)
        if nk and nk not in seen:
            seen.add(nk)
            out.append(nk)
    return out


def build_snapshot(corpus_root: Path) -> CorpusSnapshot:
    records: list[BundleRecord] = []
    mtimes: dict[Path, float] = {}
    for bundle_dir in discover_bundles(corpus_root):
        manifest_path = bundle_dir / f"{bundle_dir.name}.manifest.yaml"
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            log.warning("skipping %s: manifest parse failed (%s)", manifest_path, exc)
            continue
        if not isinstance(manifest, dict):
            log.warning("skipping %s: manifest is not a mapping", manifest_path)
            continue
        mtime = manifest_path.stat().st_mtime
        records.append(BundleRecord(
            textid=bundle_dir.name,
            bundle_dir=bundle_dir,
            manifest_path=manifest_path,
            manifest=manifest,
            mtime=mtime,
        ))
        mtimes[manifest_path] = mtime

    by_textid = {r.textid: r for r in records}
    by_identifier: dict[str, list[BundleRecord]] = {}
    for rec in records:
        for key in _index_keys(rec):
            by_identifier.setdefault(key, []).append(rec)

    return CorpusSnapshot(
        records=records,
        by_textid=by_textid,
        by_identifier=by_identifier,
        built_at=time.monotonic(),
        mtimes=mtimes,
    )


class CorpusCache:
    """Thread-safe holder for the current :class:`CorpusSnapshot`.

    A stat walk gated by ``ttl_seconds`` checks for changed manifest mtimes;
    only when something has actually changed is the snapshot rebuilt.
    """

    def __init__(self, corpus_root: Path, ttl_seconds: float = 5.0):
        self.corpus_root = corpus_root
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._snapshot: CorpusSnapshot | None = None
        self._last_check: float = 0.0

    def get(self) -> CorpusSnapshot:
        with self._lock:
            now = time.monotonic()
            if self._snapshot is None:
                self._snapshot = build_snapshot(self.corpus_root)
                self._last_check = now
                return self._snapshot
            if now - self._last_check < self.ttl_seconds:
                return self._snapshot
            self._last_check = now
            if self._is_stale(self._snapshot):
                log.info("corpus changed on disk, rebuilding snapshot")
                self._snapshot = build_snapshot(self.corpus_root)
            return self._snapshot

    def force_refresh(self) -> CorpusSnapshot:
        with self._lock:
            self._snapshot = build_snapshot(self.corpus_root)
            self._last_check = time.monotonic()
            return self._snapshot

    def lookup(self, textid: str) -> BundleRecord | None:
        """Return the cached :class:`BundleRecord` for ``textid``, or ``None``."""
        return self.get().by_textid.get(textid)

    def _is_stale(self, snap: CorpusSnapshot) -> bool:
        # Cheap: stat each manifest we know about; rebuild on any mtime change.
        # A new manifest appearing or a tracked one disappearing is detected
        # by running the same discovery walk the snapshot itself used.
        current_dirs = {bd.name for bd in discover_bundles(self.corpus_root)}
        snap_dirs = {r.bundle_dir.name for r in snap.records}
        if current_dirs != snap_dirs:
            return True
        for path, mtime in snap.mtimes.items():
            try:
                if path.stat().st_mtime != mtime:
                    return True
            except FileNotFoundError:
                return True
        return False


class IdentifierResolver:
    """Resolve identifier strings to one or more :class:`BundleRef`."""

    def __init__(self, cache: CorpusCache):
        self._cache = cache

    def lookup(self, identifier: str) -> list[BundleRef]:
        snap = self._cache.get()
        recs = snap.by_identifier.get(_key(identifier), [])
        return [r.to_ref() for r in recs]

    def disambiguate(self, candidates: list[BundleRef]) -> BundleRef | None:
        """Return the single preferred candidate, or ``None`` for no preference.

        Per the user-validated UX: prefer the candidate that has no declared
        ``metadata.base_edition`` (i.e. the canonical "master" view of the
        text). If the count of such candidates is not exactly one, the caller
        should treat the request as ambiguous and respond accordingly.
        """
        if len(candidates) <= 1:
            return candidates[0] if candidates else None
        no_base = [c for c in candidates if not c.base_edition]
        if len(no_base) == 1:
            return no_base[0]
        return None
