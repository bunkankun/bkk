"""Merge per-bundle ``.bkkx`` files into a corpus-level index.

Walks a corpus root for ``<dir>/<dir>.manifest.yaml``, builds any missing or
stale per-bundle index, then unions every per-bundle ``.bkkx`` into a single
SQLite output. Primary keys from each source are shifted by per-source offsets
so they remain unique in the merged file; ``trigram.source_id`` is shifted in
parallel with the bucket/witness id it points at.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import time
from pathlib import Path

from .build import build_index, compute_bkkx_hash
from .schema import SCHEMA_VERSION, TABLES_DDL, create_heavy_indices

log = logging.getLogger("bkk.index")


def discover_bundles(corpus_root: Path | str, prefix: str | None = None) -> list[Path]:
    """Return bundle directories under ``corpus_root``, sorted by textid.

    A directory ``X/`` qualifies iff ``X/X.manifest.yaml`` exists. Both the
    flat layout (``<corpus>/<text-id>/``) and the sectioned layout produced
    by ``bkk import --by-section`` (``<corpus>/<section>/<text-id>/``) are
    discovered: any subdirectory that doesn't itself look like a bundle is
    probed one level deeper for sectioned bundles. Mixed corpora work too.

    ``prefix`` filters by the *leaf* (text-id) name, mirroring the importer's
    ``--section`` flag — so ``prefix="KR1a"`` matches bundle ids starting
    with ``KR1a`` regardless of which directory layout they live under.
    """
    corpus_root = Path(corpus_root)
    out: list[Path] = []
    for sub in sorted(corpus_root.iterdir()):
        if not sub.is_dir():
            continue
        if (sub / f"{sub.name}.manifest.yaml").exists():
            if prefix and not sub.name.startswith(prefix):
                continue
            out.append(sub)
            continue
        # ``sub`` isn't a bundle itself; treat it as a possible section dir
        # and probe one level deeper. Non-section folders (no nested
        # bundles) yield nothing and are silently skipped.
        for grand in sorted(sub.iterdir()):
            if not grand.is_dir():
                continue
            if not (grand / f"{grand.name}.manifest.yaml").exists():
                continue
            if prefix and not grand.name.startswith(prefix):
                continue
            out.append(grand)
    out.sort(key=lambda p: p.name)
    return out


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
    rebuild: bool = False,
    no_build: bool = False,
    progress: bool = False,
) -> Path:
    """Build (if needed) and merge every bundle under ``corpus_root``.

    ``rebuild=True`` forces every per-bundle ``.bkkx`` to be rebuilt regardless
    of mtime. ``no_build=True`` errors instead of building when a per-bundle
    ``.bkkx`` is missing or stale. ``progress=True`` writes one status line
    per bundle to stderr for each pass (build, merge), plus a final summary.
    """
    corpus_root = Path(corpus_root)
    out_path = Path(out_path)
    if out_path.exists():
        out_path.unlink()

    bundles = discover_bundles(corpus_root, prefix)
    if not bundles:
        raise FileNotFoundError(
            f"no bundles found under {corpus_root}"
            + (f" with prefix {prefix!r}" if prefix else "")
        )

    n = len(bundles)
    t0 = time.monotonic()
    sources: list[tuple[str, Path]] = []
    for i, bundle_dir in enumerate(bundles, 1):
        textid = bundle_dir.name
        bkkx = bundle_dir / f"{textid}.bkkx"
        t_bundle = time.monotonic()
        if rebuild or is_stale(bundle_dir, bkkx):
            if no_build:
                raise FileNotFoundError(
                    f"per-bundle index missing or stale at {bkkx} "
                    "(--no-build forbids rebuilding)"
                )
            build_index(bundle_dir, bkkx)
            action = "built"
        else:
            action = "cached"
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
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.executescript(TABLES_DDL)
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [
                ("schema_version", str(SCHEMA_VERSION)),
                ("kind", "corpus"),
            ],
        )
        offsets = {"juan": 0, "bucket": 0, "witness": 0, "variant": 0}
        for i, (textid, bkkx) in enumerate(sources, 1):
            t_bundle = time.monotonic()
            _merge_one(conn, textid, bkkx, offsets)
            offsets = _refresh_offsets(conn)
            if progress:
                dt = time.monotonic() - t_bundle
                _emit_progress(f"[merge {i}/{n}] {textid} ({dt:.2f}s)")
        if progress:
            _emit_progress("building heavy indices…")
        create_heavy_indices(conn)
        conn.commit()
    finally:
        conn.close()
    if progress:
        _emit_progress(f"done in {time.monotonic() - t0:.1f}s → {out_path}")
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
    finally:
        conn.execute("DETACH src")


def _refresh_offsets(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "juan":    conn.execute("SELECT COALESCE(MAX(juan_id), 0) FROM juan").fetchone()[0],
        "bucket":  conn.execute("SELECT COALESCE(MAX(bucket_id), 0) FROM bucket").fetchone()[0],
        "witness": conn.execute("SELECT COALESCE(MAX(witness_id), 0) FROM witness").fetchone()[0],
        "variant": conn.execute("SELECT COALESCE(MAX(variant_id), 0) FROM variant").fetchone()[0],
    }
