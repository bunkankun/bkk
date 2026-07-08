"""Command-line entry point for ``bkk chars``.

Exposes two verbs:

``canonicalize`` walks each master bundle under the corpus root, applies
step 5 of the canonicalization procedure (substitution against the
declared canonical character set), emits ``substitution`` markers, and
patches the master manifest's reference-asset declarations.

``revert`` reverses those substitution markers: it restores each marker's
``original`` character at its offset, removes the marker, and refreshes
hashes / marker assets.

    python -m bkk chars canonicalize
    python -m bkk chars canonicalize --text-id KR1a0001
    python -m bkk chars canonicalize --jobs 8
    python -m bkk chars canonicalize --corpus /data/bkk/corpus --dry-run
    python -m bkk chars revert --text-id KR1a0001

The corpus root is resolved from ``chars.corpus`` → ``info.corpus`` →
``global.corpus`` in ``.bkkrc``, matching ``bkk info`` by default.
Legacy ``chars.out`` / ``import.out`` are accepted only as deprecated
fallbacks when no corpus setting exists.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bkk.cli_common import warn_deprecated
from bkk.short_refs import text_id_arg

from .refs import DEFAULT_REFS_DIR, load_context
from .run import run_canonicalize, run_revert


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bkk chars")
    sub = p.add_subparsers(dest="op", required=True)

    pc = sub.add_parser(
        "canonicalize",
        help="apply step 5 (substitution against the canonical character "
             "set) to each master bundle and rewrite text + markers + hashes",
    )
    pc.add_argument(
        "--corpus", dest="corpus", type=Path, default=None,
        help="corpus root containing bundle dirs "
             "(default: chars.corpus / info.corpus / global.corpus from .bkkrc)",
    )
    pc.add_argument(
        "--out-root", dest="out_root", type=Path, default=None,
        help="deprecated; use --corpus. Corpus root containing bundle dirs",
    )
    pc.add_argument(
        "--text-id", dest="text_ids", action="append", default=None,
        type=text_id_arg,
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
        "--unmapped-report", "--failed-chars-report",
        dest="unmapped_report",
        type=Path,
        default=None,
        help="write a TSV report of unmapped characters that caused bundle "
             "writes to be skipped",
    )
    pc.add_argument(
        "--abort-on-error", dest="abort_on_error", action="store_true",
        help="restore legacy behaviour: abort a bundle on the first "
             "unmapped codepoint instead of surveying the whole bundle",
    )
    pc.add_argument(
        "--jobs", dest="jobs", type=int, default=1,
        help="number of bundles to process in parallel (default: 1)",
    )

    pr = sub.add_parser(
        "revert",
        aliases=["decanonicalize"],
        help="undo bkk chars canonicalize substitutions: restore originals "
             "and remove substitution markers",
    )
    pr.add_argument(
        "--corpus", dest="corpus", type=Path, default=None,
        help="corpus root containing bundle dirs "
             "(default: chars.corpus / info.corpus / global.corpus from .bkkrc)",
    )
    pr.add_argument(
        "--out-root", dest="out_root", type=Path, default=None,
        help="deprecated; use --corpus. Corpus root containing bundle dirs",
    )
    pr.add_argument(
        "--text-id", dest="text_ids", action="append", default=None,
        type=text_id_arg,
        help="restrict the run to the named bundle (repeatable; default: "
             "every bundle under the corpus root)",
    )
    pr.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="report what would be reverted without modifying files",
    )
    pr.add_argument(
        "--log-file", dest="log_file", type=Path, default=None,
        help="append errors and warnings to this file (default: "
             "chars-revert.log in the current directory)",
    )
    pr.add_argument(
        "--jobs", dest="jobs", type=int, default=1,
        help="number of bundles to process in parallel (default: 1)",
    )
    return p


def _resolve_corpus_root(
    *,
    corpus: Path | str | None,
    out_root: Path | str | None,
) -> Path | None:
    if corpus is not None and out_root is not None:
        raise ValueError("provide only one of --corpus or --out-root")
    if corpus is not None:
        return Path(corpus)
    if out_root is not None:
        warn_deprecated("--out-root", "--corpus")
        return Path(out_root)

    from bkk.config import load_rc
    rc = load_rc()
    selected = (
        rc.get("chars", {}).get("corpus")
        or rc.get("info", {}).get("corpus")
        or rc.get("global", {}).get("corpus")
    )
    if selected is not None:
        return Path(selected)

    legacy = rc.get("chars", {}).get("out") or rc.get("import", {}).get("out")
    if legacy is not None:
        warn_deprecated("chars.out/import.out fallback", "chars.corpus or global.corpus")
        return Path(legacy)
    return None


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        corpus_root = _resolve_corpus_root(
            corpus=args.corpus,
            out_root=args.out_root,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if corpus_root is None:
        print(
            "error: no corpus root resolved; pass --corpus or set "
            "chars.corpus / info.corpus / global.corpus in .bkkrc",
            file=sys.stderr,
        )
        return 2

    if args.op == "canonicalize":
        try:
            ctx = load_context(args.refs_dir)
        except (FileNotFoundError, RuntimeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

        log_file = args.log_file
        if log_file is None:
            log_file = Path("chars-canonicalize.log")

        return run_canonicalize(
            corpus_root,
            ctx=ctx,
            text_ids=args.text_ids,
            dry_run=args.dry_run,
            log_file=log_file,
            unmapped_report=args.unmapped_report,
            abort_on_error=args.abort_on_error,
            jobs=args.jobs,
        )

    if args.op in {"revert", "decanonicalize"}:
        log_file = args.log_file
        if log_file is None:
            log_file = Path("chars-revert.log")

        return run_revert(
            corpus_root,
            text_ids=args.text_ids,
            dry_run=args.dry_run,
            log_file=log_file,
            jobs=args.jobs,
        )

    parser.error(f"unknown op: {args.op}")
    return 2


def main() -> None:
    raise SystemExit(run())
