"""``bkk annotations`` CLI."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from bkk.config import load_rc

from .harvest import harvest
from .validate import (
    DEFAULT_SEARCH_WINDOW,
    format_text_summary,
    run as run_validate,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bkk annotations",
        description="Manage the bkk-annotations archive (harvest, validate, repair).",
    )
    sub = p.add_subparsers(dest="subcommand", required=True)

    h = sub.add_parser("harvest", help="pull records from one or more DIDs and merge into the archive")
    h.add_argument("--did", action="append", default=None,
                   help="DID to harvest from; repeatable. Defaults to [annotations].dids in .bkkrc.")
    h.add_argument("--annotations-root", type=Path, default=None,
                   help="archive root (default: [annotations].annotations_root or [serve].annotations_root)")
    h.add_argument("--comments-root", type=Path, default=None,
                   help="comments archive root "
                        "(default: [annotations].comments_root, else <annotations-root>/../bkk-comments)")
    h.add_argument("--translations-root", type=Path, default=None,
                   help="translations archive root "
                        "(default: [annotations].translations_root, else <annotations-root>/../bkk-translations)")
    h.add_argument("--corpus", type=Path, default=None,
                   help="corpus root (default: [global].corpus)")
    h.add_argument("--limit", type=int, default=None,
                   help="max records per DID (default: unlimited)")
    h.add_argument("--dry-run", action="store_true",
                   help="report counts without writing files")
    h.add_argument("--verbose", "-v", action="store_true", help="log harvest progress")

    for name, help_text in (
        ("validate", "check that each archived annotation's orth matches the body at its anchor"),
        ("repair",   "validate + rewrite records whose anchor can be shifted to a unique nearby match"),
    ):
        s = sub.add_parser(name, help=help_text)
        s.add_argument("text_id", nargs="?", default=None,
                       help="restrict to a single text id (default: scan whole archive)")
        s.add_argument("--annotations-root", type=Path, default=None,
                       help="archive root (default: [annotations].annotations_root or [serve].annotations_root)")
        s.add_argument("--corpus", type=Path, default=None,
                       help="corpus root (default: [global].corpus)")
        s.add_argument("--window", type=int, default=DEFAULT_SEARCH_WINDOW,
                       help=f"chars to search either side of the cached offset (default: {DEFAULT_SEARCH_WINDOW})")
        s.add_argument("--json", action="store_true",
                       help="emit per-finding JSONL on stdout in addition to the summary")
        s.add_argument("--max-findings", type=int, default=25,
                       help="cap the number of findings printed in the text summary (default: 25)")
        s.add_argument("--verbose", "-v", action="store_true", help="log progress")
        s.add_argument("--quiet", action="store_true",
                       help="suppress the per-file stderr progress line")
        if name == "repair":
            s.add_argument("--write", action="store_true",
                           help="actually rewrite files (default: dry-run, report only)")

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

    comments_root = args.comments_root or ann_rc.get("comments_root")
    translations_root = args.translations_root or ann_rc.get("translations_root")

    corpus_root = args.corpus or g.get("corpus")
    if corpus_root is None:
        print("error: no corpus configured "
              "(pass --corpus or set [global].corpus)", file=sys.stderr)
        return 2
    corpus_root = Path(corpus_root)

    summary = harvest(
        dids=list(dids),
        annotations_root=annotations_root,
        comments_root=Path(comments_root) if comments_root else None,
        translations_root=Path(translations_root) if translations_root else None,
        corpus_root=corpus_root,
        limit_per_did=args.limit,
        dry_run=args.dry_run,
    )
    json.dump(summary, sys.stdout)
    sys.stdout.write("\n")
    return 0


def _resolve_roots(args: argparse.Namespace) -> tuple[Path, Path] | int:
    """Shared root resolution for validate/repair. Returns 2 on error."""
    rc = load_rc()
    g = rc.get("global", {})
    ann_rc = rc.get("annotations", {})
    serve_rc = rc.get("serve", {})

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

    corpus_root = args.corpus or g.get("corpus")
    if corpus_root is None:
        print("error: no corpus configured "
              "(pass --corpus or set [global].corpus)", file=sys.stderr)
        return 2
    return Path(annotations_root), Path(corpus_root)


def _cmd_validate_or_repair(args: argparse.Namespace, *, write: bool) -> int:
    roots = _resolve_roots(args)
    if isinstance(roots, int):
        return roots
    annotations_root, corpus_root = roots
    if not annotations_root.is_dir():
        print(f"error: annotations root not found: {annotations_root}", file=sys.stderr)
        return 2
    if not corpus_root.is_dir():
        print(f"error: corpus root not found: {corpus_root}", file=sys.stderr)
        return 2

    def _emit_progress(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    summary = run_validate(
        annotations_root,
        corpus_root,
        text_id_filter=args.text_id,
        write=write,
        window=args.window,
        progress=None if args.quiet else _emit_progress,
    )

    if args.json:
        for f in summary.findings:
            if f.status in ("ok", "no_orth"):
                continue
            sys.stdout.write(json.dumps({
                "text_id": f.text_id,
                "juan_seq": f.juan_seq,
                "id": f.annotation_id,
                "marker_id": f.marker_id,
                "status": f.status,
                "bucket": f.bucket,
                "bucket_offset": f.bucket_offset,
                "anchor_offset": f.anchor_offset,
                "orth": f.orth,
                "found_at_offset": f.found_at_offset,
                "proposed_bucket_offset": f.proposed_bucket_offset,
                "delta": f.delta,
                "detail": f.detail,
            }, ensure_ascii=False))
            sys.stdout.write("\n")

    print(format_text_summary(summary, max_findings=args.max_findings))
    has_problems = any(
        k not in ("ok", "no_orth") for k in summary.by_status
    )
    return 1 if has_problems and not write else 0


def run(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "verbose", False):
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.subcommand == "harvest":
        return _cmd_harvest(args)
    if args.subcommand == "validate":
        return _cmd_validate_or_repair(args, write=False)
    if args.subcommand == "repair":
        return _cmd_validate_or_repair(args, write=getattr(args, "write", False))
    parser.error(f"unknown subcommand: {args.subcommand}")
    return 2


__all__ = ["run"]
