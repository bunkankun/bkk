"""Command-line interface for the BKK exporter.

Invocation::

    python -m bkk.exporter --recipe <path>

Reads the recipe, dispatches to the appropriate per-format exporter, writes
output XML files to ``recipe.output_dir``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .recipe import RecipeError, load_recipe


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bkk.exporter")
    p.add_argument("--recipe", required=True, type=Path,
                   help="path to the recipe YAML")
    return p


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        recipe = load_recipe(args.recipe)
    except RecipeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if recipe.format == "tls":
        from .tls import export_tls_from_recipe
        written = export_tls_from_recipe(recipe)
    elif recipe.format == "krp":
        from .krp import export_krp_from_recipe
        written = export_krp_from_recipe(recipe)
    else:
        # recipe loader already rejects unsupported formats; this is a guard.
        print(f"error: unsupported format {recipe.format!r}", file=sys.stderr)
        return 2

    print(f"wrote {len(written)} file(s) under {recipe.output_dir}")
    for p in written:
        print(f"  {p}")
    return 0


def main() -> None:
    raise SystemExit(run())
