"""Command-line entry point: ``python -m bkk.validator <bundle-dir>``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import validate_bundle


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

    report = validate_bundle(args.bundle, tls_source_root=args.tls_source)
    if args.json:
        print(report.render_json())
    else:
        print(report.render_text())
    return 1 if report.has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
