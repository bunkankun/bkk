"""Command-line entry point for ``bkk repair``.

Exposes repair operations for manifests and marker storage.

    python -m bkk repair manifest <out-root>/<text-id>/
    python -m bkk repair manifest <text-id>     # resolved via .bkkrc

For the bare-id form, the bundle root is resolved against (in order):
``repair.out``, ``import.out``, ``global.corpus`` from ``.bkkrc``. CLI
flags beat the rc file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bkk repair")
    sub = p.add_subparsers(dest="op", required=True)

    pm = sub.add_parser(
        "manifest",
        help="rebuild the master and edition manifests from the juan "
             "files on disk (use after a multi-XML-file TLS bulk import)",
    )
    pm.add_argument(
        "bundle", type=str,
        help="bundle directory, or a bare text-id resolved against "
             "repair.out / import.out / global.corpus from .bkkrc",
    )
    pm.add_argument(
        "--out", dest="out_root", type=Path, default=None,
        help="bundle output root used to resolve a bare text-id "
             "(overrides repair.out / import.out / global.corpus)",
    )

    px = sub.add_parser(
        "externalize-markers",
        help="move bulky inline juan markers into per-juan assets/*.markers.yaml files",
    )
    px.add_argument(
        "bundle", type=str,
        help="bundle directory, or a bare text-id resolved against "
             "repair.out / import.out / global.corpus from .bkkrc",
    )
    px.add_argument(
        "--out", dest="out_root", type=Path, default=None,
        help="bundle output root used to resolve a bare text-id "
             "(overrides repair.out / import.out / global.corpus)",
    )
    px.add_argument(
        "--dry-run", action="store_true",
        help="report the migration without writing juans, marker assets, or manifests",
    )
    return p


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    out_root = args.out_root
    if out_root is None:
        # Defaults come from .bkkrc only when --out wasn't given.
        # `set_defaults` on the parent parser doesn't reach the subparser,
        # so we resolve the fallback after parsing instead.
        from bkk.config import load_rc
        rc = load_rc()
        out_root = (
            rc.get("repair", {}).get("out")
            or rc.get("import", {}).get("out")
            or rc.get("global", {}).get("corpus")
        )

    if args.op == "manifest":
        return _run_manifest(args.bundle, out_root)
    if args.op == "externalize-markers":
        return _run_externalize_markers(args.bundle, out_root, dry_run=args.dry_run)
    return 2


def _resolve_bundle_dir(bundle: str, out_root: Path | None) -> Path:
    """Treat ``bundle`` as a path if it points at an existing directory;
    otherwise resolve it as a text-id under ``out_root``."""
    p = Path(bundle).expanduser()
    if p.is_dir():
        return p.resolve()
    # Bare text-id (no path separators) → join against out_root.
    if out_root is not None and "/" not in bundle and "\\" not in bundle:
        candidate = (Path(out_root).expanduser() / bundle).resolve()
        if candidate.is_dir():
            return candidate
        raise FileNotFoundError(
            f"bundle directory not found: tried {p} and {candidate}"
        )
    raise FileNotFoundError(f"bundle directory not found: {p}")


def _run_manifest(bundle: str, out_root: Path | None) -> int:
    try:
        bundle_dir = _resolve_bundle_dir(bundle, out_root)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    from .manifest import rebuild_manifests
    summary = rebuild_manifests(bundle_dir)

    master = summary["master"]
    print(
        f"rebuilt {master['manifest']}: "
        f"{master['parts']} parts, "
        f"{master['annotations']} annotations, "
        f"{master['toc']} TOC entries"
    )
    for ed in summary["editions"]:
        print(
            f"rebuilt editions/{ed['edition']}/{ed['manifest']}: "
            f"{ed['parts']} parts, {ed['toc']} TOC entries"
        )
    return 0


def _run_externalize_markers(
    bundle: str, out_root: Path | None, *, dry_run: bool,
) -> int:
    try:
        bundle_dir = _resolve_bundle_dir(bundle, out_root)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    from .markers import externalize_markers
    summary = externalize_markers(bundle_dir, dry_run=dry_run)
    prefix = "would externalize" if dry_run else "externalized"
    for scope in summary["scopes"]:
        print(
            f"{prefix} {scope['manifest']}: "
            f"moved {scope['moved']} markers, kept {scope['kept']} inline"
        )
        for line in scope["lines"]:
            print(f"  {line}")
    return 0


def main() -> None:
    raise SystemExit(run())
