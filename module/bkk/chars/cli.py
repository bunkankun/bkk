"""Command-line entry point for ``bkk chars``.

Currently exposes one verb:

``canonicalize`` walks each master bundle under the corpus root, applies
step 5 of the canonicalization procedure (substitution against the
declared canonical character set), emits ``substitution`` markers, and
patches the master manifest's reference-asset declarations.

    python -m bkk chars canonicalize
    python -m bkk chars canonicalize --text-id KR1a0001
    python -m bkk chars canonicalize --out-root /data/bkk/out --dry-run

The corpus root is resolved from ``chars.out`` → ``import.out`` →
``global.corpus`` in ``.bkkrc``, mirroring ``bkk voice`` / ``bkk repair``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .refs import DEFAULT_REFS_DIR, load_context
from .run import run_canonicalize


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bkk chars")
    sub = p.add_subparsers(dest="op", required=True)

    pc = sub.add_parser(
        "canonicalize",
        help="apply step 5 (substitution against the canonical character "
             "set) to each master bundle and rewrite text + markers + hashes",
    )
    pc.add_argument(
        "--out-root", dest="out_root", type=Path, default=None,
        help="corpus root containing bundle dirs "
             "(default: chars.out / import.out / global.corpus from .bkkrc)",
    )
    pc.add_argument(
        "--text-id", dest="text_ids", action="append", default=None,
        help="restrict the run to the named bundle (repeatable; default: "
             "every bundle under the corpus root)",
    )
    pc.add_argument(
        "--refs-dir", dest="refs_dir", type=Path, default=None,
        help=f"override the reference-assets directory (default: {DEFAULT_REFS_DIR})",
    )
    pc.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="report what would be substituted without modifying files",
    )
    pc.add_argument(
        "--log-file", dest="log_file", type=Path, default=None,
        help="append errors and warnings to this file (default: "
             "chars-canonicalize.log in the current directory)",
    )
    pc.add_argument(
        "--abort-on-error", dest="abort_on_error", action="store_true",
        help="restore legacy behaviour: abort a bundle on the first "
             "unmapped codepoint instead of surveying the whole bundle",
    )
    return p


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.op != "canonicalize":
        parser.error(f"unknown op: {args.op}")
        return 2

    out_root = args.out_root
    if out_root is None:
        from bkk.config import load_rc
        rc = load_rc()
        out_root = (
            rc.get("chars", {}).get("out")
            or rc.get("import", {}).get("out")
            or rc.get("global", {}).get("corpus")
        )
    if out_root is None:
        print(
            "error: no corpus root resolved; pass --out-root or set "
            "chars.out / import.out / global.corpus in .bkkrc",
            file=sys.stderr,
        )
        return 2

    try:
        ctx = load_context(args.refs_dir)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    log_file = args.log_file
    if log_file is None:
        log_file = Path("chars-canonicalize.log")

    return run_canonicalize(
        Path(out_root),
        ctx=ctx,
        text_ids=args.text_ids,
        dry_run=args.dry_run,
        log_file=log_file,
        abort_on_error=args.abort_on_error,
    )


def main() -> None:
    raise SystemExit(run())
