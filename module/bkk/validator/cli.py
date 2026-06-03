"""Command-line entry point: ``python -m bkk.validator <bundle-dir>``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import validate_bundle
from .marker_ids import (
    freeze_marker_ids,
    validate_marker_ids,
)


def main(argv: list[str] | None = None) -> int:
    from bkk.config import load_rc
    rc = load_rc()
    g = rc.get("global", {})
    val = rc.get("validate", {})

    parser = argparse.ArgumentParser(
        prog="bkk.validator",
        description="Validate a BKK bundle directory.",
    )
    parser.add_argument("bundle", type=Path, help="path to the bundle directory")
    parser.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of text output",
    )
    parser.add_argument(
        "--tls-source", type=Path, default=None,
        help="TLS source root (parent of tls-chant/) for char-count parity check",
    )
    parser.add_argument(
        "--marker-ids", action="store_true",
        help="run only the marker-id drift check against the snapshot file",
    )
    parser.add_argument(
        "--freeze-marker-ids", action="store_true",
        help="write the marker-id snapshot file and exit",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="overwrite an existing snapshot when used with --freeze-marker-ids",
    )
    tls_source = val.get("tls_source") or g.get("tls_root")
    if tls_source is not None:
        parser.set_defaults(tls_source=tls_source)

    args = parser.parse_args(argv)

    if not args.bundle.is_dir():
        print(f"error: not a directory: {args.bundle}", file=sys.stderr)
        return 2
    if args.tls_source is not None and not args.tls_source.is_dir():
        print(f"error: not a directory: {args.tls_source}", file=sys.stderr)
        return 2

    if args.freeze_marker_ids:
        try:
            path = freeze_marker_ids(args.bundle, force=args.force)
        except FileExistsError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"wrote {path.relative_to(args.bundle.resolve())}")
        return 0

    if args.marker_ids:
        try:
            issues = validate_marker_ids(args.bundle)
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        for it in issues:
            print(
                f"{it.kind}: scope={it.scope} seq={it.seq} id={it.id}"
                + (f" — {it.detail}" if it.detail else "")
            )
        return 1 if any(it.kind in ("missing", "repurposed") for it in issues) else 0

    report = validate_bundle(args.bundle, tls_source_root=args.tls_source)
    if args.json:
        print(report.render_json())
    else:
        print(report.render_text())
    return 1 if report.has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
