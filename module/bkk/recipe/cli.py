"""CLI for recipe rendering."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from bkk.config import load_rc

from .render import RecipeRenderError, render_recipe_file
from .punc_report import PuncReportError, make_punc_report_input


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bkk recipe")
    sub = p.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("render", help="render a recipe template")
    pr.add_argument("recipe", type=Path)
    pr.add_argument(
        "input", type=Path, nargs="?", default=None,
        help="optional input recipe consumed by the template "
             "(e.g. a generated punc-report input)",
    )
    pr.add_argument("--corpus", type=Path, default=None,
                    help="corpus root; defaults to [recipe].corpus or [global].corpus")
    pr.add_argument("--out", type=Path, default=None,
                    help="write rendered output to this path; defaults to stdout")
    pr.add_argument("--punctuation-root", type=Path, default=None,
                    help="external punctuation-sidecar root; defaults to "
                         "[llm].punctuation_root in .bkkrc")

    pm = sub.add_parser("make", help="generate an input recipe for a template")
    pmsub = pm.add_subparsers(dest="target", required=True)
    pp = pmsub.add_parser(
        "punc-report",
        help="list all punctuation sets for a text id and write a punc-report input",
    )
    pp.add_argument("--text-id", dest="text_id",
                    help="text id to inspect")
    pp.add_argument("--bundle", dest="text_id", help=argparse.SUPPRESS)
    pp.add_argument("--juan", type=int, action="append", default=None,
                    help="restrict to this juan sequence number; repeatable "
                         "(default: all juans)")
    pp.add_argument("--bucket", default="body",
                    help="bucket to compare: front | body | back (default: body)")
    pp.add_argument("--width", type=int, default=40,
                    help="fixed number of base characters per line (default: 40)")
    pp.add_argument("--out", type=Path, default=None,
                    help="output path (default: <TEXTID>.punc-report.input.yaml)")
    pp.add_argument("--corpus", type=Path, default=None,
                    help="corpus root; defaults to [recipe].corpus or [global].corpus")
    pp.add_argument("--punctuation-root", type=Path, default=None,
                    help="external punctuation-sidecar root; defaults to "
                         "[llm].punctuation_root in .bkkrc")
    return p


def _resolve_corpus(rc: dict, corpus: Path | None) -> Path | None:
    value = (
        corpus
        or rc.get("recipe", {}).get("corpus")
        or rc.get("global", {}).get("corpus")
    )
    return Path(value).resolve() if value is not None else None


def _resolve_punctuation_root(rc: dict, override: Path | None) -> str | Path | None:
    if override is not None:
        return override
    return rc.get("llm", {}).get("punctuation_root")


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    rc = load_rc()
    if args.cmd == "render":
        return _run_render(parser, args, rc)
    if args.cmd == "make":
        return _run_make(parser, args, rc)
    return 2


def _run_render(parser: argparse.ArgumentParser, args, rc: dict) -> int:
    corpus_root = _resolve_corpus(rc, args.corpus)
    if corpus_root is None:
        parser.error("corpus is required (pass --corpus or set global.corpus in .bkkrc)")
    punctuation_root = _resolve_punctuation_root(rc, args.punctuation_root)

    try:
        rendered = render_recipe_file(
            args.recipe,
            corpus_root=corpus_root,
            input_path=args.input,
            punctuation_root=punctuation_root,
        )
    except RecipeRenderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.out is None:
        print(rendered.text, end="" if rendered.text.endswith("\n") else "\n")
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered.text, encoding="utf-8")
    return 0


def _run_make(parser: argparse.ArgumentParser, args, rc: dict) -> int:
    if args.target != "punc-report":
        return 2
    if not args.text_id:
        parser.error("punc-report requires --text-id")
    corpus_root = _resolve_corpus(rc, args.corpus)
    if corpus_root is None:
        parser.error("corpus is required (pass --corpus or set global.corpus in .bkkrc)")
    punctuation_root = _resolve_punctuation_root(rc, args.punctuation_root)

    try:
        input_recipe = make_punc_report_input(
            corpus_root=corpus_root,
            textid=args.text_id,
            juans=args.juan,
            bucket=args.bucket,
            width=args.width,
            punctuation_root=punctuation_root,
        )
    except PuncReportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out_path = args.out or Path(f"{args.text_id}.punc-report.input.yaml")
    text = yaml.safe_dump(input_recipe, allow_unicode=True, sort_keys=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    n_sets = len(input_recipe.get("sets") or [])
    n_juans = len(input_recipe.get("target", {}).get("juans") or [])
    print(
        f"wrote {out_path} ({n_sets} punctuation set(s), {n_juans} juan(s))"
    )
    print(
        f"render with: bkk recipe render <template.yaml> {out_path} "
        f"--corpus {corpus_root}"
    )
    return 0


def main() -> None:
    raise SystemExit(run())
