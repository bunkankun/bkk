"""Command-line interface for the BKK importer.

TLS invocation::

    python -m bkk.importer --format tls --in <tls-root> --out <out-root> --text-id <id>

Expects the TLS repository layout:

    <tls-root>/
      tls-texts/data/.../<text-id>.xml      (searched recursively)
      tls-data/notes/swl/<text-id>-ann.xml
      tls-data/notes/doc/<text-id>-ann.xml

KRP invocation::

    python -m bkk.importer --format krp --recipe <recipe.yaml>

The recipe pins per-text knobs (branch → edition mapping, master witnesses,
imglist source). See :mod:`bkk.importer.recipe` for the schema.

Either path emits a BKK bundle under ``<out-root>/<text-id>/``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .diverge import diff_trees, render_report
from .read.tls import read_tls
from .recipe import load_recipe
from .write.bundle import write_bundle, write_krp_edition, write_krp_master


def _find_tls_text(in_root: Path, text_id: str) -> Path | None:
    """Locate ``<text-id>.xml`` under ``<in-root>/tls-texts/data/``.

    The TLS repo subdivides texts across classification subdirs (e.g. by
    Kanseki Repository category), so we search recursively. If multiple
    matches exist we prefer the shallowest path and report a warning to
    stderr.
    """
    base = in_root / "tls-texts" / "data"
    if not base.exists():
        return None
    matches = sorted(base.rglob(f"{text_id}.xml"), key=lambda p: (len(p.parts), str(p)))
    if not matches:
        return None
    if len(matches) > 1:
        print(
            f"warning: multiple matches for {text_id}.xml under {base}; "
            f"using {matches[0]}",
            file=sys.stderr,
        )
    return matches[0]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bkk.importer")
    p.add_argument("--format", required=True, choices=["tls", "krp"])
    p.add_argument("--recipe", type=Path, default=None,
                   help="recipe YAML pinning per-text knobs (krp); when given, "
                        "supplies --in/--out/--text-id defaults")
    p.add_argument("--in", dest="in_root", type=Path, default=None,
                   help="source repository root (tls) or krp git repo")
    p.add_argument("--out", dest="out_root", type=Path, default=None,
                   help="output directory (bundle written under <out>/<text-id>/)")
    p.add_argument("--text-id", default=None, help="text identifier (e.g. KR6q0053)")
    p.add_argument("--sample", type=Path, default=None,
                   help="optional sample tree to diff against; emits a "
                        "divergence-from-sample.md alongside the output")
    return p


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.format == "tls":
        return _run_tls(args)
    if args.format == "krp":
        return _run_krp(args)
    print(f"error: unknown format {args.format!r}", file=sys.stderr)
    return 2


def _run_tls(args) -> int:
    if args.in_root is None or args.out_root is None or args.text_id is None:
        print("error: --in, --out, --text-id are required for --format tls",
              file=sys.stderr)
        return 2
    text_xml = _find_tls_text(args.in_root, args.text_id)
    if text_xml is None:
        print(
            f"error: {args.text_id}.xml not found anywhere under "
            f"{args.in_root / 'tls-texts' / 'data'}",
            file=sys.stderr,
        )
        return 2
    swl_xml = args.in_root / "tls-data" / "notes" / "swl" / f"{args.text_id}-ann.xml"
    doc_xml = args.in_root / "tls-data" / "notes" / "doc" / f"{args.text_id}-ann.xml"
    bundle = read_tls(text_xml, swl_xml, doc_xml, args.text_id)

    summary = write_bundle(bundle, args.out_root)
    print(
        f"wrote {len(summary['juans'])} juan(s) for {summary['text_id']} "
        f"under {summary['out_root']}"
    )

    if args.sample is not None:
        _emit_divergence(args.sample, Path(summary["out_root"]), args.out_root)
    return 0


def _run_krp(args) -> int:
    if args.recipe is None:
        print("error: --recipe is required for --format krp", file=sys.stderr)
        return 2
    recipe = load_recipe(args.recipe)
    if recipe.format != "krp":
        print(f"error: recipe at {args.recipe} declares format "
              f"{recipe.format!r}, expected krp", file=sys.stderr)
        return 2

    out_root = args.out_root or recipe.output_bundle
    if out_root is None:
        print("error: no output directory: pass --out or set "
              "`output.bundle` in the recipe", file=sys.stderr)
        return 2

    # Lazy import so the lxml/TLS path stays decoupled from the krp path.
    from .read.krp import read_krp

    documentary, master = read_krp(recipe)

    text_id = recipe.text_id or ""
    juan_total = 0
    for bundle in documentary:
        s = write_krp_edition(bundle, out_root)
        juan_total = max(juan_total, len(s["juans"]))
        print(
            f"wrote {len(s['juans'])} juan(s) for {s['text_id']} "
            f"edition {s['edition']} under {s['out_root']}"
        )
    if master is not None:
        s = write_krp_master(master, out_root)
        print(
            f"wrote {len(s['juans'])} juan(s) for {s['text_id']} "
            f"master under {s['out_root']}"
            + (f" (+ {s['pua_map']})" if "pua_map" in s else "")
        )

    if args.sample is not None and text_id:
        ours_root = out_root / text_id
        _emit_divergence(args.sample, ours_root, out_root)
    return 0


def _emit_divergence(sample: Path, ours_root: Path, out_root: Path) -> None:
    divergences = diff_trees(sample, ours_root)
    report = render_report(divergences)
    report_path = out_root / "divergence-from-sample.md"
    report_path.write_text(report, encoding="utf-8")
    unexpected = sum(1 for d in divergences if d.status == "unexpected")
    expected = len(divergences) - unexpected
    print(
        f"divergence report at {report_path} "
        f"({expected} expected, {unexpected} unexpected)"
    )


def main() -> None:
    raise SystemExit(run())
