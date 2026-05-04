"""Command-line entry point: ``python -m bkk.validator <bundle-dir>``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import validate_bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bkk.validator",
        description="Validate a BKK bundle directory.",
    )
    parser.add_argument("bundle", type=Path, help="path to the bundle directory")
    parser.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of text output",
    )
    args = parser.parse_args(argv)

    if not args.bundle.is_dir():
        print(f"error: not a directory: {args.bundle}", file=sys.stderr)
        return 2

    report = validate_bundle(args.bundle)
    if args.json:
        print(report.render_json())
    else:
        print(report.render_text())
    return 1 if report.has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
