"""``bkk core`` CLI.

Currently exposes ``bkk core sync``: fast-forward the local bkk-core
clone from upstream and rebuild ``_core.bkki``. Reads ``[core].root``
and ``[core].pr_base`` from ``.bkkrc``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from bkk.config import load_rc
from bkk.index.core import build_core_index


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bkk core",
        description="Maintenance commands for the bkk-core knowledge layer.",
    )
    sub = p.add_subparsers(dest="subcommand", required=True)

    s = sub.add_parser(
        "sync",
        help="git fetch + ff-merge the local bkk-core clone from upstream, then rebuild the index",
    )
    s.add_argument("--core-root", type=Path, default=None,
                   help="bkk-core clone directory (default: [core].root)")
    s.add_argument("--core-index", type=Path, default=None,
                   help="output .bkki path (default: [core].index, else <core-root>/_core.bkki)")
    s.add_argument("--pr-base", default=None,
                   help="upstream branch to fast-forward from (default: [core].pr_base, else 'master')")

    return p


def _resolve_core_root(args, core_rc) -> Path:
    if args.core_root is not None:
        return Path(args.core_root).resolve()
    rc_root = core_rc.get("root")
    if rc_root is not None:
        return Path(rc_root).resolve()
    sys.exit(
        "error: core root not configured; pass --core-root or set "
        "[core].root in .bkkrc"
    )


def _resolve_index_path(args, core_rc, core_root: Path) -> Path:
    if args.core_index is not None:
        return Path(args.core_index).resolve()
    rc_index = core_rc.get("index")
    if rc_index is not None:
        return Path(rc_index).resolve()
    return core_root / "_core.bkki"


def _cmd_sync(args: argparse.Namespace) -> int:
    rc = load_rc()
    core_rc = rc.get("core", {})

    core_root = _resolve_core_root(args, core_rc)
    core_index = _resolve_index_path(args, core_rc, core_root)
    pr_base = args.pr_base or core_rc.get("pr_base", "master")

    if not (core_root / ".git").is_dir():
        sys.exit(f"error: {core_root} is not a git checkout")

    print(f"fetching origin/{pr_base} in {core_root}...", file=sys.stderr)
    fetch = subprocess.run(
        ["git", "-C", str(core_root), "fetch", "origin", pr_base],
        capture_output=True, text=True,
    )
    if fetch.returncode != 0:
        sys.exit(f"git fetch failed: {fetch.stderr.strip()}")

    print(f"fast-forward merge origin/{pr_base}...", file=sys.stderr)
    merge = subprocess.run(
        ["git", "-C", str(core_root), "merge", "--ff-only", f"origin/{pr_base}"],
        capture_output=True, text=True,
    )
    if merge.returncode != 0:
        sys.exit(
            f"git merge --ff-only origin/{pr_base} failed: "
            f"{merge.stderr.strip() or merge.stdout.strip()}"
        )

    head = subprocess.run(
        ["git", "-C", str(core_root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    pulled_sha = head.stdout.strip()
    print(f"local HEAD now at {pulled_sha}", file=sys.stderr)

    print(f"rebuilding {core_index}...", file=sys.stderr)
    out = build_core_index(core_root, core_index)
    print(f"wrote {out}", file=sys.stderr)
    return 0


def run(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.subcommand == "sync":
        return _cmd_sync(args)
    return 2


def main() -> None:
    raise SystemExit(run())
