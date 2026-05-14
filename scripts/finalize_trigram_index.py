"""One-shot recovery: build the missing ``idx_trigram_gram`` on a merged
``_corpus.bkkx`` whose ``bkk index merge`` run was OOM-killed at the heavy-index
phase.

Background: ``bkk.index.merge`` sets ``PRAGMA temp_store = MEMORY``, which forces
SQLite's external sort during ``CREATE INDEX`` to stay in RAM. At corpus scale
(~2 B trigram rows) that exceeds available memory and the kernel kills the
process. This script reopens the existing merged file with ``temp_store = FILE``
and a bounded page cache, points temp at a partition with room to spill, then
creates only the missing index.

Idempotent: if ``idx_trigram_gram`` already exists, exits without doing work.

Usage:
    SQLITE_TMPDIR=/home/chris/00scratch \\
        python scripts/finalize_trigram_index.py \\
        /home/chris/00scratch/bkk-work/output/_corpus.bkkx
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path


INDEX_NAME = "idx_trigram_gram"
INDEX_DDL = f"CREATE INDEX {INDEX_NAME} ON trigram(gram)"


def finalize(bkkx_path: Path, temp_dir: Path) -> None:
    if not bkkx_path.exists():
        sys.exit(f"no such file: {bkkx_path}")
    if not temp_dir.exists():
        sys.exit(f"temp dir does not exist: {temp_dir}")

    size_before = bkkx_path.stat().st_size
    print(f"opening {bkkx_path}  ({size_before / 1024**3:.1f} GiB)")
    conn = sqlite3.connect(str(bkkx_path))
    try:
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        if INDEX_NAME in existing:
            print(f"{INDEX_NAME} already present — nothing to do")
            return

        # Spill the sort to disk on a partition with room. temp_store_directory
        # is deprecated in newer SQLite but still honored in 3.45; SQLITE_TMPDIR
        # in the calling shell is the belt-and-braces alternative.
        conn.execute("PRAGMA temp_store = FILE")
        conn.execute(f"PRAGMA temp_store_directory = '{temp_dir}'")
        # Bounded page cache: ~2 GiB. Negative value = KiB, not pages.
        conn.execute("PRAGMA cache_size = -2000000")
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA journal_mode = MEMORY")

        n_trigram = conn.execute("SELECT COUNT(*) FROM trigram").fetchone()[0]
        print(f"trigram rows: {n_trigram:,}")
        print(f"creating {INDEX_NAME} (sort spills to {temp_dir})…")

        t0 = time.monotonic()
        conn.execute(INDEX_DDL)
        conn.commit()
        dt = time.monotonic() - t0

        size_after = bkkx_path.stat().st_size
        grew = size_after - size_before
        print(
            f"done in {dt / 60:.1f} min "
            f"(file grew by {grew / 1024**3:.1f} GiB → "
            f"{size_after / 1024**3:.1f} GiB)"
        )
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bkkx", type=Path, help="path to merged _corpus.bkkx")
    ap.add_argument(
        "--temp-dir",
        type=Path,
        default=Path("/home/chris/00scratch"),
        help="directory for SQLite sort temp files (default: /home/chris/00scratch)",
    )
    args = ap.parse_args()
    finalize(args.bkkx, args.temp_dir)


if __name__ == "__main__":
    main()
