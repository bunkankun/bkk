"""``bkk annotations`` CLI."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from bkk.config import load_rc

from .harvest import harvest


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bkk annotations",
        description="Harvest Bluesky annotation records into the bkk-annotations archive.",
    )
    sub = p.add_subparsers(dest="subcommand", required=True)

    h = sub.add_parser("harvest", help="pull records from one or more DIDs and merge into the archive")
    h.add_argument("--did", action="append", default=None,
                   help="DID to harvest from; repeatable. Defaults to [annotations].dids in .bkkrc.")
    h.add_argument("--annotations-root", type=Path, default=None,
                   help="archive root (default: [annotations].annotations_root or [serve].annotations_root)")
    h.add_argument("--corpus", type=Path, default=None,
                   help="corpus root (default: [global].corpus)")
    h.add_argument("--limit", type=int, default=None,
                   help="max records per DID (default: unlimited)")
    h.add_argument("--dry-run", action="store_true",
                   help="report counts without writing files")
    h.add_argument("--verbose", "-v", action="store_true", help="log harvest progress")
    return p


def _cmd_harvest(args: argparse.Namespace) -> int:
    rc = load_rc()
    g = rc.get("global", {})
    ann_rc = rc.get("annotations", {})
    serve_rc = rc.get("serve", {})

    dids = args.did or ann_rc.get("dids") or []
    if isinstance(dids, str):
        print(
            f"error: [annotations].dids must be a YAML list, got scalar {dids!r}. "
            "Use:\n  dids:\n    - did:plc:...",
            file=sys.stderr,
        )
        return 2
    if not dids:
        print("error: no DIDs to harvest "
              "(pass --did or set [annotations].dids in .bkkrc)", file=sys.stderr)
        return 2

    annotations_root = (
        args.annotations_root
        or ann_rc.get("annotations_root")
        or serve_rc.get("annotations_root")
    )
    if annotations_root is None:
        print("error: no archive root configured "
              "(pass --annotations-root or set [serve].annotations_root)",
              file=sys.stderr)
        return 2
    annotations_root = Path(annotations_root)

    corpus_root = args.corpus or g.get("corpus")
    if corpus_root is None:
        print("error: no corpus configured "
              "(pass --corpus or set [global].corpus)", file=sys.stderr)
        return 2
    corpus_root = Path(corpus_root)

    summary = harvest(
        dids=list(dids),
        annotations_root=annotations_root,
        corpus_root=corpus_root,
        limit_per_did=args.limit,
        dry_run=args.dry_run,
    )
    json.dump(summary, sys.stdout)
    sys.stdout.write("\n")
    return 0


def run(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "verbose", False):
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.subcommand == "harvest":
        return _cmd_harvest(args)
    parser.error(f"unknown subcommand: {args.subcommand}")
    return 2


__all__ = ["run"]
