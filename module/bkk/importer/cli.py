"""Command-line interface for the BKK importer.

TLS invocation::

    # single text
    python -m bkk.importer --format tls --in <tls-root> --out <out-root> --text-id <id>

    # every text discoverable under <tls-root> (prompts for confirmation)
    python -m bkk.importer --format tls --in <tls-root> --out <out-root>

Expects the TLS repository layout:

    <tls-root>/
      tls-texts/data/.../<text-id>.xml      (searched recursively)
      tls-data/notes/swl/<text-id>-ann.xml
      tls-data/notes/doc/<text-id>-ann.xml

KRP invocation::

    # single text from a local kanripo mirror
    python -m bkk.importer --format krp --in <root> --text-id KR3a0013 --out <out>

    # single text fetched on demand from github.com/kanripo/<id>
    python -m bkk.importer --format krp --text-id KR3a0013 --out <out>

    # every text under a corpus prefix (prompts for confirmation; --yes skips)
    python -m bkk.importer --format krp --in <root> --section KR3a --out <out>

    # every discoverable text under SOURCE (prompts for confirmation)
    python -m bkk.importer --format krp --in <root> --out <out>

    # legacy recipe-driven path (overrides everything else)
    python -m bkk.importer --format krp --recipe <recipe.yaml>

The recipe-less paths derive editions, master/imglist branches, witnesses,
title, and date from the source repo itself (branch list + ``Readme.org``).
See :mod:`bkk.importer.source` for the discovery + synthesis logic and
:mod:`bkk.importer.recipe` for the schema recipes still pin.

Either format emits a BKK bundle under ``<out-root>/<text-id>/``.

Cross-source co-existence (see ``docs/cross-source-merge.md``):

- TLS owns the surface (root) edition. If a TLS bundle already exists
  at the destination, a subsequent KRP import merges in: documentary
  editions land under ``editions/<short>/``, the synthesized KRP master
  is demoted to ``editions/master/`` (variant + witness page-break
  markers retained), and the TLS root manifest's ``editions:`` list is
  extended.
- TLS into an existing KRP bundle is rejected with a hard error. The
  operator removes the bundle and re-imports in TLS-then-KRP order.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .diverge import diff_trees, render_report
from .read.tls import read_tls
from .recipe import Recipe, load_recipe
from .write.bundle import (
    write_bundle, write_krp_edition, write_krp_master, write_pua_map,
)
from .write.merge import extend_master_editions, inspect_existing_bundle


class BundleConflictError(Exception):
    """Raised when an importer refuses to write because the existing bundle
    on disk was produced by an incompatible source. Caught by the bulk
    loop so other texts can continue."""


_DEFAULT_GITHUB_USER = "kanripo"
_DEFAULT_CACHE_DIR = Path.home() / ".cache" / "bkk" / "krp"


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
    p.add_argument("--format", choices=["tls", "krp"], default=None,
                   help="source format: tls or krp (required; or set import.format in .bkkrc)")
    p.add_argument("--recipe", type=Path, default=None,
                   help="recipe YAML pinning per-text knobs (krp); when given, "
                        "supplies --in/--out/--text-id defaults and overrides "
                        "the auto-discovery path")
    p.add_argument("--in", dest="in_root", type=Path, default=None,
                   help="source root: tls repo, or kanripo mirror "
                        "(parent of <prefix>/<text-id>/ clones)")
    p.add_argument("--out", dest="out_root", type=Path, default=None,
                   help="output directory (bundle written under <out>/<text-id>/)")
    p.add_argument("--text-id", default=None, help="single text id (e.g. KR6q0053)")
    p.add_argument("--section", default=None,
                   help="krp: import every text under a corpus prefix "
                        "(e.g. KR3a); requires confirmation")
    p.add_argument("--github", dest="github_user", default=None,
                   help=f"krp: github user/org to fetch from "
                        f"(default {_DEFAULT_GITHUB_USER!r} when --in is unset)")
    p.add_argument("--master-branch", default="master",
                   help="krp: branch carrying the curated master (default: master)")
    p.add_argument("--imglist-branch", default="_data",
                   help="krp: branch carrying imglist + imginfo (default: _data)")
    p.add_argument("--cache-dir", type=Path, default=_DEFAULT_CACHE_DIR,
                   help=f"krp: cache for github clones "
                        f"(default: {_DEFAULT_CACHE_DIR})")
    p.add_argument("--yes", action="store_true",
                   help="skip the bulk-import confirmation prompt")
    p.add_argument("--sample", type=Path, default=None,
                   help="optional sample tree to diff against; emits a "
                        "divergence-from-sample.md alongside the output")
    return p


def run(argv: list[str] | None = None) -> int:
    from bkk.config import load_rc
    rc = load_rc()
    g = rc.get("global", {})
    imp = rc.get("import", {})

    parser = build_parser()
    defaults: dict = {}
    if "in" in imp:
        defaults["in_root"] = imp["in"]
    for rc_key, dest in [
        ("out", "out_root"), ("format", "format"), ("cache_dir", "cache_dir"),
        ("github", "github_user"), ("master_branch", "master_branch"),
        ("imglist_branch", "imglist_branch"),
    ]:
        if rc_key in imp:
            defaults[dest] = imp[rc_key]
    if g.get("skip_confirm") or imp.get("skip_confirm"):
        defaults["yes"] = True
    if defaults:
        parser.set_defaults(**defaults)

    args = parser.parse_args(argv)

    # If --in wasn't supplied by rc or CLI, derive it from the resolved format.
    if args.in_root is None and "in" not in imp and args.format is not None:
        root_key = "tls_root" if args.format == "tls" else "krp_root"
        if root_key in g:
            args.in_root = g[root_key]

    if args.format is None:
        parser.error("--format is required (or set import.format in .bkkrc)")

    if args.format == "tls":
        return _run_tls(args)
    if args.format == "krp":
        return _run_krp(args)
    print(f"error: unknown format {args.format!r}", file=sys.stderr)
    return 2


def _run_tls(args) -> int:
    """Dispatch the TLS path.

    Two shapes:

    1. ``--text-id`` given → single text, resolved via :func:`_find_tls_text`.
    2. No ``--text-id`` → bulk: walk ``<in>/tls-texts/data/`` for every
       ``<id>.xml`` and import each. Prompts for confirmation unless
       ``--yes`` is set.
    """
    if args.in_root is None or args.out_root is None:
        print("error: --in and --out are required for --format tls",
              file=sys.stderr)
        return 2

    pairs = _resolve_tls_targets(args)
    if not pairs:
        print("error: no texts found to import", file=sys.stderr)
        return 2

    if len(pairs) > 1 and not args.yes:
        if not _confirm_bulk(pairs):
            print("aborted.", file=sys.stderr)
            return 1

    rc = 0
    for text_id, text_xml in pairs:
        try:
            _import_one_tls(args, text_id, text_xml,
                            sample=args.sample if len(pairs) == 1 else None)
        except Exception as exc:  # noqa: BLE001 — surface per-text failure, keep going
            print(f"error importing {text_id}: {exc}", file=sys.stderr)
            rc = 1
    return rc


def _resolve_tls_targets(args) -> list[tuple[str, Path]]:
    """Map TLS CLI flags → ``[(text_id, text_xml), ...]`` to import.

    With ``--text-id``: single-element list (or empty if the xml is missing,
    in which case we print the same error the old single-text path used).
    Without ``--text-id``: enumerate every ``<id>.xml`` under
    ``<in>/tls-texts/data/``; skip with a warning any id we can't resolve.
    """
    from . import source

    if args.text_id is not None:
        text_xml = _find_tls_text(args.in_root, args.text_id)
        if text_xml is None:
            print(
                f"error: {args.text_id}.xml not found anywhere under "
                f"{args.in_root / 'tls-texts' / 'data'}",
                file=sys.stderr,
            )
            return []
        return [(args.text_id, text_xml)]

    pairs: list[tuple[str, Path]] = []
    for tid in source.list_local_tls_text_ids(args.in_root):
        text_xml = _find_tls_text(args.in_root, tid)
        if text_xml is None:
            print(f"warning: skipping {tid}: xml not resolvable",
                  file=sys.stderr)
            continue
        pairs.append((tid, text_xml))
    return pairs


def _import_one_tls(args, text_id: str, text_xml: Path,
                    *, sample: Path | None) -> None:
    """Run read+write for one TLS text."""
    existing = inspect_existing_bundle(args.out_root, text_id)
    if existing.state == "krp":
        bundle_dir = existing.manifest_path.parent
        raise BundleConflictError(
            f"{text_id}: a KRP-sourced bundle already exists at "
            f"{bundle_dir}. TLS imports must precede KRP. "
            f"Remedy: remove {bundle_dir} and re-import TLS first, "
            f"then KRP."
        )
    if existing.state == "unknown":
        bundle_dir = existing.manifest_path.parent
        raise BundleConflictError(
            f"{text_id}: a bundle already exists at {bundle_dir} but its "
            f"source can't be classified. Inspect manually or run "
            f"`bkk repair manifest {bundle_dir}` and retry."
        )

    swl_xml = args.in_root / "tls-data" / "notes" / "swl" / f"{text_id}-ann.xml"
    doc_xml = args.in_root / "tls-data" / "notes" / "doc" / f"{text_id}-ann.xml"
    bundle = read_tls(text_xml, swl_xml, doc_xml, text_id)

    summary = write_bundle(bundle, args.out_root)
    print(
        f"wrote {len(summary['juans'])} juan(s) for {summary['text_id']} "
        f"under {summary['out_root']}"
    )

    if sample is not None:
        _emit_divergence(sample, Path(summary["out_root"]), args.out_root)


# ---------- KRP --------------------------------------------------------------


def _run_krp(args) -> int:
    """Dispatch the KRP path.

    Three shapes:

    1. ``--recipe`` given → legacy: load the recipe verbatim and run it.
       Preserves every existing test fixture.
    2. ``--text-id`` given → single text. Source comes from ``--in`` if set,
       otherwise github.com/<--github|kanripo>/<id>.
    3. No ``--text-id`` → bulk. ``--section`` narrows to one corpus prefix;
       omitting both walks the whole source. Both bulk modes prompt for
       confirmation unless ``--yes`` is set.
    """
    if args.recipe is not None:
        return _run_krp_recipe(args)

    if args.out_root is None:
        print("error: --out is required for --format krp (without --recipe)",
              file=sys.stderr)
        return 2
    if args.text_id is not None and args.section is not None:
        print("error: --text-id and --section are mutually exclusive",
              file=sys.stderr)
        return 2
    if args.in_root is not None and args.github_user is not None:
        print("error: --in and --github are mutually exclusive",
              file=sys.stderr)
        return 2

    # Resolve the (text_id, repo_path) pairs to import.
    try:
        pairs = _resolve_targets(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not pairs:
        print("error: no texts found to import", file=sys.stderr)
        return 2

    if len(pairs) > 1 and not args.yes:
        if not _confirm_bulk(pairs):
            print("aborted.", file=sys.stderr)
            return 1

    rc = 0
    for text_id, repo_path in pairs:
        try:
            recipe = _synthesize(args, text_id, repo_path)
            _import_one(recipe, args.out_root, args.sample if len(pairs) == 1 else None)
        except Exception as exc:  # noqa: BLE001 — surface per-text failure, keep going
            print(f"error importing {text_id}: {exc}", file=sys.stderr)
            rc = 1
    return rc


def _run_krp_recipe(args) -> int:
    """Legacy path: --recipe is authoritative. Preserves prior behaviour."""
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

    _import_one(recipe, out_root, args.sample)
    return 0


def _resolve_targets(args) -> list[tuple[str, Path]]:
    """Map CLI flags → ``[(text_id, repo_path), ...]`` to import.

    Resolution rules match the docstring on :func:`_run_krp`. Local sources
    are walked from ``--in``; github sources land in ``--cache-dir``.
    """
    # Lazy import: keeps the CLI startup snappy and lets the tls path run
    # without `requests` installed.
    from . import source

    use_github = args.in_root is None
    github_user = args.github_user or (_DEFAULT_GITHUB_USER if use_github else None)

    if args.text_id:
        if use_github:
            repo = source.resolve_github_repo(
                github_user, args.text_id, args.cache_dir,
            )
        else:
            repo = source.resolve_local_repo(args.in_root, args.text_id)
        return [(args.text_id, repo)]

    # Bulk: list ids first, then resolve each.
    if use_github:
        ids = source.list_github_text_ids(github_user, args.section)
    else:
        ids = source.list_local_text_ids(args.in_root, args.section)

    pairs: list[tuple[str, Path]] = []
    for tid in ids:
        if use_github:
            repo = source.resolve_github_repo(github_user, tid, args.cache_dir)
        else:
            repo = source.resolve_local_repo(args.in_root, tid)
        pairs.append((tid, repo))
    return pairs


def _synthesize(args, text_id: str, repo: Path) -> Recipe:
    from . import source
    return source.synthesize_recipe(
        repo, text_id,
        master_branch=args.master_branch,
        imglist_branch=args.imglist_branch,
    )


def _confirm_bulk(pairs: list[tuple[str, Path]]) -> bool:
    """Print the discovered ids and ask once. Returns True on yes."""
    print(f"about to import {len(pairs)} text(s):", file=sys.stderr)
    for tid, repo in pairs:
        print(f"  {tid}  ({repo})", file=sys.stderr)
    try:
        ans = input(f"Import {len(pairs)} texts? [y/N] ").strip().lower()
    except EOFError:
        return False
    return ans in {"y", "yes"}


def _import_one(recipe: Recipe, out_root: Path, sample: Path | None) -> None:
    """Run read+write for one synthesized or loaded recipe.

    If a TLS-sourced bundle already exists at ``<out_root>/<text-id>/``,
    the KRP master is demoted to a regular edition under
    ``editions/master/`` and the existing TLS surface is preserved. The
    TLS master manifest's ``editions:`` list is extended with the new
    KRP edition shorts.

    Per the plan, an ``unknown`` state at the destination is treated as
    a conflict (the user must investigate before any KRP write touches
    the directory).
    """
    # Lazy: keep the lxml/TLS path independent of the krp reader.
    from .read.krp import read_krp

    text_id = recipe.text_id or ""
    existing = inspect_existing_bundle(out_root, text_id) if text_id else None
    if existing is not None and existing.state == "unknown":
        bundle_dir = existing.manifest_path.parent
        raise BundleConflictError(
            f"{text_id}: a bundle already exists at {bundle_dir} but its "
            f"source can't be classified. Inspect manually or run "
            f"`bkk repair manifest {bundle_dir}` and retry."
        )
    merge_into_tls = bool(existing and existing.state == "tls")

    documentary, master = read_krp(recipe)

    protected: set[str] = (
        existing.tls_owned_editions if merge_into_tls else set()
    )
    if merge_into_tls:
        # Defensive: don't let a documentary edition collide with the
        # demoted KRP master's ``master`` short.
        for bundle in documentary:
            if bundle.edition_short == "master":
                raise BundleConflictError(
                    f"{text_id}: KRP recipe declares a documentary edition "
                    f"with short 'master', which would collide with the "
                    f"demoted KRP master in merge mode. Rename the witness."
                )

    new_edition_entries: list[dict] = []

    for bundle in documentary:
        if bundle.edition_short in protected:
            print(
                f"skipping KRP edition {bundle.edition_short} for "
                f"{bundle.text_id}: short already owned by the TLS surface "
                f"under editions/{bundle.edition_short}/",
                file=sys.stderr,
            )
            continue
        s = write_krp_edition(bundle, out_root)
        print(
            f"wrote {len(s['juans'])} juan(s) for {s['text_id']} "
            f"edition {s['edition']} under {s['out_root']}"
        )
        entry: dict = {"short": bundle.edition_short}
        label = bundle.metadata.get("edition_label")
        if label:
            entry["label"] = label
        new_edition_entries.append(entry)

    if master is not None:
        if merge_into_tls:
            # Demote: write the synthesized master as a regular edition
            # under ``editions/master/``, preserving variant + witness
            # page-break markers. PUA-map still belongs at the bundle root.
            if master.edition_short in protected:
                raise BundleConflictError(
                    f"{text_id}: TLS surface already owns "
                    f"editions/{master.edition_short}/, but the demoted "
                    f"KRP master would land there. Resolve manually."
                )
            s = write_krp_edition(master, out_root)
            pua_filename = write_pua_map(master, out_root)
            print(
                f"wrote {len(s['juans'])} juan(s) for {s['text_id']} "
                f"krp-master demoted to edition {s['edition']} under "
                f"{s['out_root']}"
                + (f" (+ {pua_filename})" if pua_filename else "")
            )
            new_edition_entries.append({"short": master.edition_short})
        else:
            s = write_krp_master(master, out_root)
            print(
                f"wrote {len(s['juans'])} juan(s) for {s['text_id']} "
                f"master under {s['out_root']}"
                + (f" (+ {s['pua_map']})" if "pua_map" in s else "")
            )

    if merge_into_tls and new_edition_entries:
        final = extend_master_editions(
            existing.manifest_path, new_edition_entries,
        )
        print(
            f"updated {existing.manifest_path.name}: editions list now "
            f"{[e.get('short') for e in final if isinstance(e, dict)]}"
        )

    if sample is not None and text_id:
        ours_root = out_root / text_id
        _emit_divergence(sample, ours_root, out_root)


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
