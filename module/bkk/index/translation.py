"""Build per-bundle ``.bkkt`` translation search indices and merge them.

Workflow mirrors the main ``.bkkx`` index:

1. ``build_translation_index(bundle_dir)`` — writes a per-bundle ``.bkkt``
   next to the bundle's manifest ``.md``.
2. ``merge_translations(corpus, out_path)`` — builds any missing/stale
   per-bundle files, then ATTACHes them one by one into the corpus-level
   ``_translations.bkkt``.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import sys
import time
from pathlib import Path

from .catalog import (
    _find_translation_roots,
    _read_markdown_frontmatter,
    normalize_search_text,
)

_SPAN_RE = re.compile(r"\[((?:\\.|[^\]\\])*)\]\{([^}]*)\}")

log = logging.getLogger("bkk.index")

TRANSLATION_SCHEMA_VERSION = 1

# Tables only — heavy search index is deferred until end of merge.
_TABLES_DDL = """
CREATE TABLE meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE translation_segment (
  translation_id TEXT NOT NULL,
  juan_seq INTEGER NOT NULL,
  text TEXT NOT NULL,
  text_search TEXT
);

CREATE INDEX idx_translation_segment_id ON translation_segment(translation_id);
"""

_SEARCH_INDEX_DDL = """
CREATE INDEX idx_translation_segment_search ON translation_segment(text_search);
"""


def build_translation_index(
    bundle_dir: Path | str,
    out_path: Path | str | None = None,
) -> Path:
    """Build ``<bundle_id>.bkkt`` from a translation bundle directory."""
    bundle_dir = Path(bundle_dir)
    bundle_id = bundle_dir.name
    if out_path is None:
        out_path = bundle_dir / f"{bundle_id}.bkkt"
    else:
        out_path = Path(out_path)
    records = _segment_records_for_bundle(bundle_dir, bundle_id)
    if out_path.exists():
        out_path.unlink()
    conn = sqlite3.connect(str(out_path))
    try:
        conn.executescript(_TABLES_DDL + _SEARCH_INDEX_DDL)
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [
                ("schema_version", str(TRANSLATION_SCHEMA_VERSION)),
                ("kind", "translation"),
                ("bundle_id", bundle_id),
            ],
        )
        conn.executemany(
            "INSERT INTO translation_segment"
            "(translation_id, juan_seq, text, text_search) VALUES (?,?,?,?)",
            records,
        )
        conn.commit()
    finally:
        conn.close()
    return out_path


def merge_translations(
    corpus: Path | str,
    out_path: Path | str,
    *,
    rebuild: bool = False,
    no_build: bool = False,
    progress: bool = False,
) -> Path:
    """Build (if needed) and merge every translation bundle under ``corpus``.

    ``rebuild=True`` forces every per-bundle ``.bkkt`` to be rebuilt.
    ``no_build=True`` errors instead of building when a ``.bkkt`` is missing
    or stale.  ``progress=True`` writes one status line per bundle to stderr.
    """
    corpus = Path(corpus)
    out_path = Path(out_path)
    if out_path.exists():
        out_path.unlink()

    bundles = discover_translation_bundles(corpus)
    if not bundles:
        log.info("no translation bundles found under %s", corpus)
        _write_empty(out_path, corpus)
        return out_path

    n = len(bundles)
    t0 = time.monotonic()
    sources: list[Path] = []
    failures: list[tuple[str, str]] = []
    for i, bundle_dir in enumerate(bundles, 1):
        bundle_id = bundle_dir.name
        bkkt = bundle_dir / f"{bundle_id}.bkkt"
        t_bundle = time.monotonic()
        stale = rebuild or is_translation_stale(bundle_dir, bkkt)
        if stale and no_build:
            raise FileNotFoundError(
                f"per-bundle translation index missing or stale at {bkkt} "
                "(--no-build forbids rebuilding)"
            )
        try:
            if stale:
                build_translation_index(bundle_dir, bkkt)
                action = "built"
            else:
                action = "cached"
        except Exception as exc:
            failures.append((bundle_id, f"build: {exc}"))
            if progress:
                _emit(f"[build {i}/{n}] {bundle_id} SKIPPED ({exc})")
            continue
        sources.append(bkkt)
        if progress:
            dt = time.monotonic() - t_bundle
            _emit(f"[build {i}/{n}] {bundle_id} {action} ({dt:.2f}s)")

    conn = sqlite3.connect(str(out_path))
    try:
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA journal_mode = MEMORY")
        conn.executescript(_TABLES_DDL)
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [
                ("schema_version", str(TRANSLATION_SCHEMA_VERSION)),
                ("kind", "translation_corpus"),
                ("corpus", str(corpus)),
            ],
        )
        m = len(sources)
        for i, bkkt in enumerate(sources, 1):
            bundle_id = bkkt.parent.name
            t_bundle = time.monotonic()
            try:
                _merge_one(conn, bkkt)
            except Exception as exc:
                failures.append((bundle_id, f"merge: {exc}"))
                if progress:
                    _emit(f"[merge {i}/{m}] {bundle_id} SKIPPED ({exc})")
                continue
            if progress:
                dt = time.monotonic() - t_bundle
                _emit(f"[merge {i}/{m}] {bundle_id} ({dt:.2f}s)")
        if progress:
            _emit("building search index…")
        conn.executescript(_SEARCH_INDEX_DDL)
        conn.commit()
    finally:
        conn.close()
    if progress:
        _emit(f"done in {time.monotonic() - t0:.1f}s → {out_path}")
    if failures:
        sys.stderr.write(f"\nskipped {len(failures)} bundle(s):\n")
        for bundle_id, reason in failures:
            sys.stderr.write(f"  {bundle_id}: {reason}\n")
        sys.stderr.flush()
    return out_path


def discover_translation_bundles(corpus: Path | str) -> list[Path]:
    """Return all translation bundle directories under any ``translations/`` root in ``corpus``."""
    corpus = Path(corpus)
    seen: set[Path] = set()
    for root in _find_translation_roots(corpus):
        for pattern in ("*/*/*/*.md", "*/*/*/*/*.md"):
            for md in root.glob(pattern):
                if md.parent.name == md.stem and md.parent not in seen:
                    seen.add(md.parent)
    return sorted(seen, key=lambda p: p.name)


def is_translation_stale(bundle_dir: Path, bkkt_path: Path) -> bool:
    """Return True iff ``bkkt_path`` is missing, wrong version, or older than sources."""
    if not bkkt_path.exists():
        return True
    if _schema_version(bkkt_path) != TRANSLATION_SCHEMA_VERSION:
        return True
    mtime = bkkt_path.stat().st_mtime
    return any(src.stat().st_mtime > mtime for src in bundle_dir.glob("*.md"))


# -- internals ----------------------------------------------------------------


def _merge_one(conn: sqlite3.Connection, bkkt: Path) -> None:
    conn.execute("ATTACH ? AS src", (str(bkkt),))
    try:
        row = conn.execute(
            "SELECT value FROM src.meta WHERE key = 'schema_version'"
        ).fetchone()
        src_version = int(row[0]) if row else 0
        if src_version != TRANSLATION_SCHEMA_VERSION:
            raise ValueError(
                f"{bkkt} has schema version {src_version}, "
                f"expected {TRANSLATION_SCHEMA_VERSION}; rebuild it first"
            )
        conn.execute(
            "INSERT INTO translation_segment SELECT * FROM src.translation_segment"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("DETACH src")


def _write_empty(out_path: Path, corpus: Path) -> None:
    conn = sqlite3.connect(str(out_path))
    try:
        conn.executescript(_TABLES_DDL + _SEARCH_INDEX_DDL)
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [
                ("schema_version", str(TRANSLATION_SCHEMA_VERSION)),
                ("kind", "translation_corpus"),
                ("corpus", str(corpus)),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _schema_version(path: Path) -> int:
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return 0


def _segment_records_for_bundle(
    bundle_dir: Path,
    bundle_id: str,
) -> list[tuple[str, int, str, str | None]]:
    manifest_path = bundle_dir / f"{bundle_id}.md"
    try:
        manifest, _ = _read_markdown_frontmatter(manifest_path)
    except Exception as exc:
        log.warning("%s: translation manifest parse failed: %s", manifest_path, exc)
        return []
    records: list[tuple[str, int, str, str | None]] = []
    for entry in manifest.get("juan") or []:
        if not isinstance(entry, dict):
            continue
        seq = entry.get("seq")
        filename = entry.get("file")
        if not isinstance(seq, int) or not isinstance(filename, str):
            continue
        juan_path = bundle_dir / filename
        if not juan_path.is_file():
            continue
        try:
            _, body = _read_markdown_frontmatter(juan_path)
        except Exception:
            continue
        for span in _SPAN_RE.finditer(body):
            text = _unescape(span.group(1)).strip()
            if not text:
                continue
            records.append((bundle_id, seq, text, normalize_search_text(text)))
    return records


def _unescape(text: str) -> str:
    out: list[str] = []
    escaped = False
    for ch in text:
        if escaped:
            out.append(ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        else:
            out.append(ch)
    if escaped:
        out.append("\\")
    return "".join(out)


def _emit(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()
