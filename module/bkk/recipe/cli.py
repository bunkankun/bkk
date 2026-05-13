"""CLI for recipe rendering."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bkk.config import load_rc

from .render import RecipeRenderError, render_recipe_file


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bkk recipe")
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("render", help="render a recipe template")
    pr.add_argument("recipe", type=Path)
    pr.add_argument("--corpus", type=Path, default=None,
                    help="corpus root; defaults to [recipe].corpus or [global].corpus")
    pr.add_argument("--out", type=Path, default=None,
                    help="write rendered output to this path; defaults to stdout")
    return p


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd != "render":
        return 2

    rc = load_rc()
    corpus = args.corpus or rc.get("recipe", {}).get("corpus") or rc.get("global", {}).get("corpus")
    if corpus is None:
        parser.error("corpus is required (pass --corpus or set global.corpus in .bkkrc)")
    corpus_root = Path(corpus).resolve()

    try:
        rendered = render_recipe_file(args.recipe, corpus_root=corpus_root)
    except RecipeRenderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.out is None:
        print(rendered.text, end="" if rendered.text.endswith("\n") else "\n")
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered.text, encoding="utf-8")
    return 0


def main() -> None:
    raise SystemExit(run())
