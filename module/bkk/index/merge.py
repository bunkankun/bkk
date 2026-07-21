"""Merge per-bundle ``.bkkx`` files into a corpus-level index.

Walks a corpus root for ``<dir>/<dir>.manifest.yaml``, builds any missing or
stale per-bundle index, then unions every per-bundle ``.bkkx`` into a single
SQLite output. Primary keys from each source are shifted by per-source offsets
so they remain unique in the merged file; ``trigram.source_id`` is shifted in
parallel with the bucket/witness id it points at.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import sys
import time
from collections.abc import Collection, Sequence
from pathlib import Path

from bkk.short_refs import normalize_text_id

from .build import build_index, compute_bkkx_hash
from .schema import SCHEMA_VERSION, TABLES_DDL, create_heavy_indices

log = logging.getLogger("bkk.index")
_KR_TEXTID_RE = re.compile(r"^KR[0-9][a-z][0-9]{4}$")


def discover_bundles(
    corpus_root: Path | str,
    prefix: str | None = None,
    max_depth: int = 4,
    text_ids: Collection[str] | None = None,
) -> list[Path]:
    """Return bundle directories under ``corpus_root``, sorted by textid.

    A directory ``X/`` qualifies iff ``X/X.manifest.yaml`` exists. Several
    on-disk layouts are supported, up to ``max_depth`` levels under
    ``corpus_root``:

    - flat:               ``<corpus>/<text-id>/``                       (depth 1)
    - sectioned:          ``<corpus>/<section>/<text-id>/``             (depth 2,
      e.g. ``bkk import --by-section``)
    - sub-sectioned:      ``<corpus>/<sub>/<section>/<text-id>/``       (depth 3,
      e.g. the devcorpus layout with ``krp/<section>/<bundle>/``)

    Any non-bundle directory is descended into until ``max_depth`` is reached;
    bundle dirs are never descended into (their internal edition manifests
    don't share their parent's basename, so they can't be mistaken for
    nested bundles). Mixed corpora work too.

    ``prefix`` filters by the *leaf* (text-id) name, mirroring the importer's
    ``--section`` flag — so ``prefix="KR1a"`` matches bundle ids starting
    with ``KR1a`` regardless of which directory layout they live under.
    ``text_ids`` applies an exact leaf-id filter.
    """
    corpus_root = Path(corpus_root)
    wanted = set(text_ids) if text_ids is not None else None
    out: list[Path] = []

    def walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        for sub in sorted(d.iterdir()):
            if not sub.is_dir():
                continue
            if (sub / f"{sub.name}.manifest.yaml").exists():
                if prefix and not sub.name.startswith(prefix):
                    continue
                if wanted is not None and sub.name not in wanted:
                    continue
                out.append(sub)
                continue
            walk(sub, depth + 1)

    walk(corpus_root, 1)
    out.sort(key=lambda p: p.name)
    return out


def read_text_id_list(path: Path | str) -> list[str]:
    """Read a text-list file and return unique normalized KR text ids.

    The accepted format is one bundle id per non-comment line. Only the first
    whitespace-delimited token is significant, so exported list files may keep
    hit counts or titles in later columns. Rows whose first token is not a
    complete KR text id are ignored.
    """
    path = Path(path)
    text_ids: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue
        token = trimmed.split(None, 1)[0]
        try:
            text_id = normalize_text_id(token)
        except ValueError:
            continue
        if _KR_TEXTID_RE.fullmatch(text_id) is None:
            continue
        if text_id in seen:
            continue
        seen.add(text_id)
        text_ids.append(text_id)
    return text_ids


def find_bundle(corpus_root: Path | str, textid: str) -> Path | None:
    """Return the bundle directory for ``textid`` under any supported layout,
    or ``None`` if no bundle with that textid exists.

    Tries the flat-layout fast path first, then falls back to a depth-bounded
    walk via :func:`discover_bundles`.
    """
    corpus_root = Path(corpus_root)
    flat = corpus_root / textid
    if (flat / f"{textid}.manifest.yaml").exists():
        return flat
    for bd in discover_bundles(corpus_root, prefix=textid):
        if bd.name == textid:
            return bd
    return None


def is_stale(bundle_dir: Path, bkkx_path: Path) -> bool:
    """Return True iff ``bkkx_path`` is missing, has the wrong schema version,
    or is older than any source file."""
    if not bkkx_path.exists():
        return True
    if _schema_version(bkkx_path) != SCHEMA_VERSION:
        return True
    bkkx_mtime = bkkx_path.stat().st_mtime
    textid = bundle_dir.name
    sources = [bundle_dir / f"{textid}.manifest.yaml"]
    sources.extend(sorted(bundle_dir.glob(f"{textid}_*.yaml")))
    for src in sources:
        if src.exists() and src.stat().st_mtime > bkkx_mtime:
            return True
    return False


def _schema_version(bkkx_path: Path) -> int:
    try:
        conn = sqlite3.connect(f"file:{bkkx_path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return 0


def merge_bundles(
    corpus_root: Path | str,
    out_path: Path | str,
    *,
    prefix: str | None = None,
    text_ids: Sequence[str] | None = None,
    rebuild: bool = False,
    no_build: bool = False,
    jobs: int = 1,
    progress: bool = False,
) -> Path:
    """Build (if needed) and merge every bundle under ``corpus_root``.

    ``text_ids`` restricts the merge to an exact subset of bundle ids.
    ``rebuild=True`` forces every per-bundle ``.bkkx`` to be rebuilt regardless
    of mtime. ``no_build=True`` errors instead of building when a per-bundle
    ``.bkkx`` is missing or stale. ``jobs`` is forwarded to per-bundle index
    builds. ``progress=True`` writes one status line per bundle to stderr for
    each pass (build, merge), plus a final summary.
    """
    if jobs < 1:
        raise ValueError("jobs must be >= 1")
    corpus_root = Path(corpus_root)
    out_path = Path(out_path)
    wanted_text_ids = list(text_ids) if text_ids is not None else None

    bundles = discover_bundles(
        corpus_root,
        prefix,
        text_ids=set(wanted_text_ids) if wanted_text_ids is not None else None,
    )
    if not bundles:
        raise FileNotFoundError(
            f"no bundles found under {corpus_root}"
            + (f" with prefix {prefix!r}" if prefix else "")
            + (" from text id list" if wanted_text_ids is not None else "")
        )
    if wanted_text_ids is not None:
        found = {bundle.name for bundle in bundles}
        missing = [text_id for text_id in wanted_text_ids if text_id not in found]
        if missing:
            raise FileNotFoundError(
                "bundle(s) not found under "
                f"{corpus_root}: {', '.join(missing)}"
            )

    if out_path.exists():
        out_path.unlink()

    n = len(bundles)
    t0 = time.monotonic()
    sources: list[tuple[str, Path]] = []
    failures: list[tuple[str, str]] = []
    for i, bundle_dir in enumerate(bundles, 1):
        textid = bundle_dir.name
        bkkx = bundle_dir / f"{textid}.bkkx"
        t_bundle = time.monotonic()
        stale = rebuild or is_stale(bundle_dir, bkkx)
        # --no-build is an explicit precondition the caller asserts up front,
        # not a per-bundle failure to recover from — surface it loudly.
        if stale and no_build:
            raise FileNotFoundError(
                f"per-bundle index missing or stale at {bkkx} "
                "(--no-build forbids rebuilding)"
            )
        try:
            if stale:
                build_index(bundle_dir, bkkx, jobs=jobs)
                action = "built"
            else:
                action = "cached"
        except Exception as e:
            failures.append((textid, f"build: {e}"))
            if progress:
                _emit_progress(f"[build {i}/{n}] {textid} SKIPPED ({e})")
            continue
        sources.append((textid, bkkx))
        if progress:
            dt = time.monotonic() - t_bundle
            _emit_progress(f"[build {i}/{n}] {textid} {action} ({dt:.2f}s)")

    conn = sqlite3.connect(str(out_path))
    try:
        # Trade durability for throughput; the conn is single-use and closed
        # below, so we don't bother restoring these pragmas.
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA journal_mode = MEMORY")
        # FILE (not MEMORY) so the heavy-index CREATE INDEX sort can spill to
        # disk; corpus-scale trigram tables (billions of rows) blow past RAM.
        conn.execute("PRAGMA temp_store = FILE")
        conn.execute(f"PRAGMA temp_store_directory = '{out_path.parent}'")
        # Bounded page cache so we never OOM regardless of corpus size.
        conn.execute("PRAGMA cache_size = -2000000")
        conn.executescript(TABLES_DDL)
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [
                ("schema_version", str(SCHEMA_VERSION)),
                ("kind", "corpus"),
            ],
        )
        offsets = {"juan": 0, "bucket": 0, "witness": 0, "variant": 0, "voice_range": 0}
        m = len(sources)
        for i, (textid, bkkx) in enumerate(sources, 1):
            t_bundle = time.monotonic()
            try:
                _merge_one(conn, textid, bkkx, offsets)
            except Exception as e:
                failures.append((textid, f"merge: {e}"))
                if progress:
                    _emit_progress(f"[merge {i}/{m}] {textid} SKIPPED ({e})")
                continue
            offsets = _refresh_offsets(conn)
            if progress:
                dt = time.monotonic() - t_bundle
                _emit_progress(f"[merge {i}/{m}] {textid} ({dt:.2f}s)")
        if progress:
            _emit_progress("building heavy indices…")
        create_heavy_indices(conn)
        conn.commit()
    finally:
        conn.close()
    if progress:
        _emit_progress(f"done in {time.monotonic() - t0:.1f}s → {out_path}")
    if failures:
        sys.stderr.write(f"\nskipped {len(failures)} bundle(s):\n")
        for textid, reason in failures:
            sys.stderr.write(f"  {textid}: {reason}\n")
        sys.stderr.flush()
    return out_path


def _emit_progress(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


# -- internals ----------------------------------------------------------------


def _merge_one(conn: sqlite3.Connection, textid: str, bkkx: Path,
               offsets: dict[str, int]) -> None:
    src_hash = compute_bkkx_hash(bkkx)
    conn.execute("ATTACH ? AS src", (str(bkkx),))
    try:
        # Sanity-check the source schema version so we don't silently union a
        # v1 file (which would lack the parts we expect).
        row = conn.execute(
            "SELECT value FROM src.meta WHERE key = 'schema_version'"
        ).fetchone()
        src_version = int(row[0]) if row else 0
        if src_version != SCHEMA_VERSION:
            raise ValueError(
                f"source {bkkx} has schema version {src_version}, "
                f"expected {SCHEMA_VERSION}; rebuild it first"
            )

        ed_row = conn.execute(
            "SELECT value FROM src.meta WHERE key = 'editions'"
        ).fetchone()
        editions_json = ed_row[0] if ed_row else "[]"

        conn.execute(
            "INSERT INTO bundle(textid, editions, source_path, source_hash) "
            "VALUES (?, ?, ?, ?)",
            (textid, editions_json, str(bkkx), src_hash),
        )

        j = offsets["juan"]
        b = offsets["bucket"]
        w = offsets["witness"]
        v = offsets["variant"]
        vr = offsets["voice_range"]

        conn.execute(
            "INSERT INTO juan(juan_id, textid, seq, hash) "
            "SELECT juan_id + ?, textid, seq, hash FROM src.juan",
            (j,),
        )
        conn.execute(
            "INSERT INTO bucket(bucket_id, juan_id, kind, text) "
            "SELECT bucket_id + ?, juan_id + ?, kind, text FROM src.bucket",
            (b, j),
        )
        conn.execute(
            "INSERT INTO witness(witness_id, bucket_id, label, text, segments) "
            "SELECT witness_id + ?, bucket_id + ?, label, text, segments "
            "FROM src.witness",
            (w, b),
        )
        conn.execute(
            "INSERT INTO variant(variant_id, bucket_id, master_offset, length, "
            "content, witness, witness_form) "
            "SELECT variant_id + ?, bucket_id + ?, master_offset, length, "
            "content, witness, witness_form FROM src.variant",
            (v, b),
        )
        conn.execute(
            "INSERT INTO voice_range(voice_range_id, bucket_id, master_offset, "
            "length, name, voice_id, responds_to) "
            "SELECT voice_range_id + ?, bucket_id + ?, master_offset, length, "
            "name, voice_id, responds_to FROM src.voice_range",
            (vr, b),
        )
        conn.execute("INSERT INTO toc SELECT * FROM src.toc")
        conn.execute(
            "INSERT INTO trigram(gram, source_kind, source_id, position) "
            "SELECT gram, source_kind, "
            "CASE source_kind "
            "  WHEN 'bucket'  THEN source_id + ? "
            "  WHEN 'witness' THEN source_id + ? "
            "END, position FROM src.trigram",
            (b, w),
        )
        # Commit so the implicit write transaction releases its lock on the
        # attached source database before we DETACH it.
        conn.commit()
    except Exception:
        # Same reasoning as the commit above: release the write txn so DETACH
        # doesn't trip on "database is locked".
        conn.rollback()
        raise
    finally:
        conn.execute("DETACH src")


def _refresh_offsets(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "juan":        conn.execute("SELECT COALESCE(MAX(juan_id), 0) FROM juan").fetchone()[0],
        "bucket":      conn.execute("SELECT COALESCE(MAX(bucket_id), 0) FROM bucket").fetchone()[0],
        "witness":     conn.execute("SELECT COALESCE(MAX(witness_id), 0) FROM witness").fetchone()[0],
        "variant":     conn.execute("SELECT COALESCE(MAX(variant_id), 0) FROM variant").fetchone()[0],
        "voice_range": conn.execute("SELECT COALESCE(MAX(voice_range_id), 0) FROM voice_range").fetchone()[0],
    }
