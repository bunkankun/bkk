"""Command-line interface for the BKK exporter.

Three invocation shapes:

1. **Recipe-only (legacy)** — recipe pins everything::

       python -m bkk.exporter --recipe <path>

2. **Single bundle with overrides** — recipe (optional) + CLI flags. The
   recipe acts as a template; ``--bundle`` and ``--output-dir`` (and any
   other knobs) override or supply::

       python -m bkk.exporter --recipe <generic.yaml> \\
           --bundle <bundle-dir> --output-dir <out-dir>

       # entirely from flags, no recipe needed:
       python -m bkk.exporter --format krp --shape single --edition WYG \\
           --bundle <bundle-dir> --output-dir <out-dir>

3. **Corpus walk** — iterate every bundle under a corpus root. The recipe
   (if any) is the template; per-bundle ``bundle`` and ``output_dir`` are
   derived automatically. ``--text-id`` / ``--section`` narrow the slice;
   ``--yes`` skips the confirmation prompt::

       python -m bkk.exporter --recipe <generic.yaml> \\
           --corpus <corpus-root> --output-dir <out-parent> [--section KR3a]
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from pathlib import Path

from .recipe import Recipe, RecipeError, apply_overrides, load_recipe


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bkk.exporter")
    p.add_argument("--recipe", type=Path, default=None,
                   help="path to a (possibly generic) recipe YAML; optional "
                        "when all required fields are supplied as flags")
    p.add_argument("--format", choices=sorted({"krp", "tls"}), default=None,
                   help="output format (overrides recipe.format)")
    p.add_argument("--bundle", type=Path, default=None,
                   help="single bundle source dir (overrides recipe.bundle)")
    p.add_argument("--output-dir", dest="output_dir", type=Path, default=None,
                   help="output dir for a single bundle, or output **parent** "
                        "for --corpus (one subdir per text id)")
    p.add_argument("--shape", choices=sorted({"dirs", "git", "single"}),
                   default=None,
                   help="krp: dirs | git | single (overrides recipe.shape)")
    p.add_argument("--edition", default=None,
                   help="krp: short id of the edition for shape: single")
    p.add_argument("--mode", choices=sorted({"split", "concat"}),
                   default=None,
                   help="krp: split | concat (overrides recipe.mode)")
    p.add_argument("--corpus", type=Path, default=None,
                   help="corpus root containing bundle subdirs; iterate every "
                        "bundle (filtered by --text-id / --section)")
    p.add_argument("--text-id", dest="text_id", default=None,
                   help="with --corpus: restrict to a single text id")
    p.add_argument("--section", default=None,
                   help="with --corpus: restrict to text ids starting with "
                        "this prefix (e.g. KR3a)")
    p.add_argument("--yes", action="store_true",
                   help="skip the bulk-export confirmation prompt")
    return p


def run(argv: list[str] | None = None) -> int:
    from bkk.config import load_rc
    rc = load_rc()
    g = rc.get("global", {})
    exp = rc.get("export", {})

    parser = build_parser()
    defaults: dict = {}
    for rc_key, dest in [
        ("format", "format"), ("shape", "shape"), ("mode", "mode"),
        ("output_dir", "output_dir"),
    ]:
        if rc_key in exp:
            defaults[dest] = exp[rc_key]
    corpus = exp.get("corpus") or g.get("corpus")
    if corpus is not None:
        defaults["corpus"] = corpus
    if g.get("skip_confirm") or exp.get("skip_confirm"):
        defaults["yes"] = True
    if defaults:
        parser.set_defaults(**defaults)

    args = parser.parse_args(argv)

    if args.corpus is not None and args.bundle is not None:
        print("error: --corpus and --bundle are mutually exclusive",
              file=sys.stderr)
        return 2

    try:
        template = load_recipe(args.recipe) if args.recipe is not None else None
    except RecipeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.corpus is not None:
        return _run_corpus(args, template)
    return _run_single(args, template)


def _run_single(args, template: Recipe | None) -> int:
    """Single export: either ``--bundle`` + flags, or recipe-only."""
    try:
        recipe = apply_overrides(
            template,
            format=args.format, bundle=args.bundle, output_dir=args.output_dir,
            shape=args.shape, edition=args.edition, mode=args.mode,
        )
    except RecipeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    try:
        written = _dispatch(recipe)
    except RecipeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(f"wrote {len(written)} file(s) under {recipe.output_dir}")
    for p in written:
        print(f"  {p}")
    return 0


def _run_corpus(args, template: Recipe | None) -> int:
    """Iterate every bundle under ``--corpus`` (optionally filtered) and
    apply the template + per-bundle (bundle, output_dir) overrides."""
    if args.output_dir is None:
        print("error: --output-dir is required with --corpus",
              file=sys.stderr)
        return 2
    if not args.corpus.is_dir():
        print(f"error: --corpus is not a directory: {args.corpus}",
              file=sys.stderr)
        return 2

    bundles = list(_iter_bundle_dirs(
        args.corpus, text_id=args.text_id, section=args.section,
    ))
    if not bundles:
        print("error: no bundles found to export", file=sys.stderr)
        return 2

    if len(bundles) > 1 and not args.yes:
        if not _confirm_bulk(bundles):
            print("aborted.", file=sys.stderr)
            return 1

    out_root = args.output_dir.resolve()
    rc = 0
    for bundle_dir in bundles:
        text_id = bundle_dir.name
        try:
            recipe = apply_overrides(
                template,
                format=args.format,
                bundle=bundle_dir,
                output_dir=out_root / text_id,
                shape=args.shape, edition=args.edition, mode=args.mode,
            )
            written = _dispatch(recipe)
            print(
                f"wrote {len(written)} file(s) for {text_id} under "
                f"{recipe.output_dir}"
            )
        except Exception as exc:  # noqa: BLE001 — surface per-bundle failures, keep going
            print(f"error exporting {text_id}: {exc}", file=sys.stderr)
            rc = 1
    return rc


def _dispatch(recipe: Recipe) -> list[Path]:
    if recipe.format == "tls":
        from .tls import export_tls_from_recipe
        return export_tls_from_recipe(recipe)
    if recipe.format == "krp":
        from .krp import export_krp_from_recipe
        return export_krp_from_recipe(recipe)
    # apply_overrides should have caught this already; defensive guard.
    raise RecipeError(f"unsupported format {recipe.format!r}")


def _iter_bundle_dirs(corpus: Path, *, text_id: str | None,
                      section: str | None) -> Iterator[Path]:
    """Yield each bundle subdir of ``corpus``, optionally filtered.

    A directory is a bundle iff it contains ``<dirname>.manifest.yaml``
    (the same predicate :mod:`tools.validate_corpus` uses).
    """
    for child in sorted(corpus.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if text_id is not None and name != text_id:
            continue
        if section is not None and not name.startswith(section):
            continue
        manifest = child / f"{name}.manifest.yaml"
        if manifest.exists():
            yield child


def _confirm_bulk(bundles: list[Path]) -> bool:
    """Print the discovered bundles and ask once. Returns True on yes."""
    print(f"about to export {len(bundles)} bundle(s):", file=sys.stderr)
    for b in bundles:
        print(f"  {b.name}  ({b})", file=sys.stderr)
    try:
        ans = input(f"Export {len(bundles)} bundles? [y/N] ").strip().lower()
    except EOFError:
        return False
    return ans in {"y", "yes"}


def main() -> None:
    raise SystemExit(run())
