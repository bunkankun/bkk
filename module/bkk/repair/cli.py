"""Command-line entry point for ``bkk repair``.

Exposes repair operations for manifests and marker storage.

    python -m bkk repair manifest <out-root>/<text-id>/
    python -m bkk repair manifest --text-id <text-id>     # resolved via .bkkrc

For the bare-id form, the bundle root is resolved against (in order):
``repair.out``, ``import.out``, ``global.corpus`` from ``.bkkrc``. CLI
flags beat the rc file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bkk.cli_common import resolve_bundle_dir, resolve_rc_path, warn_deprecated
from bkk.short_refs import text_id_arg, text_or_path_arg, text_prefix_arg


def _add_bundle_selector(sp: argparse.ArgumentParser, *, dry_run: bool = False) -> None:
    sp.add_argument(
        "legacy_bundle", nargs="?", type=text_or_path_arg,
        help=argparse.SUPPRESS,
    )
    sp.add_argument(
        "--bundle", dest="bundle", type=Path, default=None,
        help="bundle directory",
    )
    sp.add_argument(
        "--text-id", dest="text_id", type=text_id_arg, default=None,
        help="text id to resolve against repair.out / import.out / global.corpus",
    )
    sp.add_argument(
        "--out", dest="out_root", type=Path, default=None,
        help="bundle output root used to resolve --text-id "
             "(overrides repair.out / import.out / global.corpus)",
    )
    if dry_run:
        sp.add_argument(
            "--dry-run", action="store_true",
            help="report the migration without writing juans, marker assets, or manifests",
        )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bkk repair")
    sub = p.add_subparsers(dest="op", required=True)

    pm = sub.add_parser(
        "manifest",
        help="rebuild the master and edition manifests from the juan "
             "files on disk (use after a multi-XML-file TLS bulk import)",
    )
    _add_bundle_selector(pm)

    px = sub.add_parser(
        "externalize-markers",
        help="move bulky inline juan markers into per-juan assets/*.markers.yaml files",
    )
    _add_bundle_selector(px, dry_run=True)

    pi = sub.add_parser(
        "ids-from-krp-titles",
        help="populate metadata.identifiers.alt_id on master manifests "
             "from catalog/krp-titles.txt for the bundles in --text-prefix",
    )
    pi.add_argument(
        "--section", action="append", default=None,
        help="deprecated; use --text-prefix. KRP prefix (e.g. KR5, KR6, "
             "KR5a); repeatable. A bundle is in scope iff its text-id "
             "starts with one of these prefixes.",
    )
    pi.add_argument(
        "--text-prefix", action="append", default=None, dest="text_prefixes",
        type=text_prefix_arg,
        help="text-id prefix (e.g. KR5, KR6, KR5a); repeatable. A bundle is "
             "in scope iff its text-id starts with one of these prefixes.",
    )
    pi.add_argument(
        "--titles", dest="titles_path", type=Path, default=None,
        help="path to krp-titles.txt (default: <repo-root>/catalog/krp-titles.txt)",
    )
    pi.add_argument(
        "--out", dest="out_root", type=Path, default=None,
        help="bundle output root (overrides repair.out / import.out / global.corpus)",
    )
    pi.add_argument(
        "--dry-run", action="store_true",
        help="report planned changes without writing manifests",
    )

    pr = sub.add_parser(
        "remove-ids",
        help="strip every key under metadata.identifiers on master "
             "manifests except 'alt_id'",
    )
    pr.add_argument(
        "--section", action="append", default=None,
        help="deprecated; use --text-prefix. KRP prefix (e.g. KR5, KR6, "
             "KR5a); repeatable. A bundle is in scope iff its text-id "
             "starts with one of these prefixes.",
    )
    pr.add_argument(
        "--text-prefix", action="append", default=None, dest="text_prefixes",
        type=text_prefix_arg,
        help="text-id prefix (e.g. KR5, KR6, KR5a); repeatable. A bundle is "
             "in scope iff its text-id starts with one of these prefixes.",
    )
    pr.add_argument(
        "--out", dest="out_root", type=Path, default=None,
        help="bundle output root (overrides repair.out / import.out / global.corpus)",
    )
    pr.add_argument(
        "--dry-run", action="store_true",
        help="report planned changes without writing manifests",
    )

    ppi = sub.add_parser(
        "parallel-index",
        help="build a SQLite index over stored parallel-passage assets",
    )
    ppi.add_argument(
        "--parallels-root", type=Path, default=None,
        help="root containing <textid>/<textid>_NNN.<name>.parallels.yaml files",
    )
    ppi.add_argument(
        "--corpus", type=Path, default=None,
        help="corpus root whose bundle-local parallels/ directories should also be indexed",
    )

    ppr = sub.add_parser(
        "parallels",
        help="repair pending stale parallel-passage assets",
    )
    ppr.add_argument(
        "--parallels-root", type=Path, default=None,
        help="root containing the parallel stale ledger and optional shared assets",
    )
    ppr.add_argument(
        "--corpus", type=Path, default=None,
        help="corpus root for bundle-local parallel assets",
    )
    ppr.add_argument(
        "--rebuild-index", action="store_true",
        help="rebuild the parallel asset index before repairing pending stale records",
    )
    return p


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    out_root = getattr(args, "out_root", None)
    if out_root is None:
        # Defaults come from .bkkrc only when --out wasn't given.
        # `set_defaults` on the parent parser doesn't reach the subparser,
        # so we resolve the fallback after parsing instead.
        from bkk.config import load_rc
        rc = load_rc()
        out_root = resolve_rc_path(
            None, rc,
            (("repair", "out"), ("import", "out"), ("global", "corpus")),
        )

    if args.op == "manifest":
        try:
            bundle, text_id = _selected_bundle_args(args)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return _run_manifest(bundle, out_root, text_id=text_id)
    if args.op == "externalize-markers":
        try:
            bundle, text_id = _selected_bundle_args(args)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return _run_externalize_markers(
            bundle, out_root, text_id=text_id, dry_run=args.dry_run,
        )
    if args.op == "ids-from-krp-titles":
        try:
            sections = _selected_prefixes(args)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if not sections:
            print("error: provide at least one --text-prefix", file=sys.stderr)
            return 2
        return _run_ids_from_krp_titles(
            sections=sections,
            titles_path=args.titles_path,
            out_root=out_root,
            dry_run=args.dry_run,
        )
    if args.op == "remove-ids":
        try:
            sections = _selected_prefixes(args)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if not sections:
            print("error: provide at least one --text-prefix", file=sys.stderr)
            return 2
        return _run_remove_ids(
            sections=sections,
            out_root=out_root,
            dry_run=args.dry_run,
        )
    if args.op == "parallel-index":
        return _run_parallel_index(
            parallels_root=args.parallels_root,
            corpus_root=args.corpus,
        )
    if args.op == "parallels":
        return _run_parallel_repair(
            parallels_root=args.parallels_root,
            corpus_root=args.corpus,
            rebuild_index=args.rebuild_index,
        )
    return 2


def _selected_bundle_args(args: argparse.Namespace) -> tuple[str | Path | None, str | None]:
    supplied = [
        bool(getattr(args, "legacy_bundle", None)),
        bool(getattr(args, "bundle", None)),
        bool(getattr(args, "text_id", None)),
    ]
    if sum(supplied) != 1:
        raise ValueError("provide exactly one of --bundle or --text-id")
    if getattr(args, "legacy_bundle", None):
        legacy = args.legacy_bundle
        if "/" in legacy or "\\" in legacy or Path(legacy).is_dir():
            warn_deprecated("positional <bundle>", "--bundle <dir>")
            return legacy, None
        warn_deprecated("positional <text-id>", "--text-id <text-id>")
        return None, legacy
    return args.bundle, args.text_id


def _selected_prefixes(args: argparse.Namespace) -> list[str]:
    legacy = getattr(args, "section", None) or []
    current = getattr(args, "text_prefixes", None) or []
    if legacy and current:
        raise ValueError("provide only one of --text-prefix or --section")
    if legacy:
        warn_deprecated("--section", "--text-prefix")
        return [text_prefix_arg(item) for item in legacy]
    return current


def _resolve_bundle_dir(
    bundle: str | Path | None,
    out_root: Path | None,
    *,
    text_id: str | None = None,
) -> Path:
    return resolve_bundle_dir(bundle=bundle, text_id=text_id, root=out_root)


def _run_manifest(
    bundle: str | Path | None,
    out_root: Path | None,
    *,
    text_id: str | None = None,
) -> int:
    try:
        bundle_dir = _resolve_bundle_dir(bundle, out_root, text_id=text_id)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    from .manifest import rebuild_manifests
    summary = rebuild_manifests(bundle_dir)

    master = summary["master"]
    print(
        f"rebuilt {master['manifest']}: "
        f"{master['parts']} parts, "
        f"{master['toc']} TOC entries"
    )
    for ed in summary["editions"]:
        print(
            f"rebuilt editions/{ed['edition']}/{ed['manifest']}: "
            f"{ed['parts']} parts, {ed['toc']} TOC entries"
        )
    return 0


def _run_externalize_markers(
    bundle: str | Path | None,
    out_root: Path | None,
    *,
    text_id: str | None = None,
    dry_run: bool,
) -> int:
    try:
        bundle_dir = _resolve_bundle_dir(bundle, out_root, text_id=text_id)
    except (FileNotFoundError, ValueError) as exc:
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


def _iter_bundles_in_sections(out_root: Path, prefixes: tuple[str, ...]):
    """Yield bundle directories under ``out_root`` whose text-id starts
    with one of ``prefixes``. Handles both the flat layout
    (``<out>/<text_id>/``) and the ``--by-section`` layout
    (``<out>/<section>/<text_id>/``)."""
    for child in sorted(out_root.iterdir()):
        if not child.is_dir():
            continue
        # Flat: child is a text-id directory.
        if (child / f"{child.name}.manifest.yaml").is_file():
            if child.name.startswith(prefixes):
                yield child
            continue
        # By-section: descend if the section dir itself is in scope, or if
        # any requested prefix could land inside it (e.g. --section KR5a
        # under <out>/KR5a/).
        if not any(
            child.name.startswith(pfx) or pfx.startswith(child.name)
            for pfx in prefixes
        ):
            continue
        for sub in sorted(child.iterdir()):
            if not sub.is_dir():
                continue
            if not sub.name.startswith(prefixes):
                continue
            if (sub / f"{sub.name}.manifest.yaml").is_file():
                yield sub


def _default_titles_path() -> Path:
    """Resolve ``catalog/krp-titles.txt`` next to the repo root.

    The module lives at ``module/bkk/repair/cli.py``; the catalog sits at
    ``catalog/krp-titles.txt`` two levels above ``module/``.
    """
    return Path(__file__).resolve().parents[3] / "catalog" / "krp-titles.txt"


def _run_ids_from_krp_titles(
    *,
    sections: list[str],
    titles_path: Path | None,
    out_root: Path | None,
    dry_run: bool,
) -> int:
    if out_root is None:
        print(
            "error: bundle root not given (--out) and not configured in "
            ".bkkrc (repair.out / import.out / global.corpus)",
            file=sys.stderr,
        )
        return 2
    out_root = Path(out_root).expanduser().resolve()
    if not out_root.is_dir():
        print(f"error: bundle root is not a directory: {out_root}", file=sys.stderr)
        return 2

    titles_path = (titles_path or _default_titles_path()).expanduser().resolve()
    if not titles_path.is_file():
        print(f"error: krp-titles file not found: {titles_path}", file=sys.stderr)
        return 2

    from .krp_titles import parse_alt_ids
    from .identifiers import apply_alt_ids

    catalog = parse_alt_ids(titles_path)

    prefixes = tuple(sections)
    bundles = sorted(_iter_bundles_in_sections(out_root, prefixes))

    n_changed = 0
    n_unchanged = 0
    n_no_catalog = 0
    for bundle_dir in bundles:
        alts = catalog.get(bundle_dir.name)
        if not alts:
            n_no_catalog += 1
            continue
        result = apply_alt_ids(bundle_dir, alts, dry_run=dry_run)
        verb = "would set" if dry_run else "set"
        if result["changed"]:
            n_changed += 1
            before = result["before"] or "(none)"
            print(f"{verb} {bundle_dir.name}.alt_id: {before} -> {result['after']}")
        else:
            n_unchanged += 1

    prefix = "dry-run: " if dry_run else ""
    print(
        f"{prefix}{n_changed} changed, {n_unchanged} unchanged, "
        f"{n_no_catalog} not in catalog "
        f"(scanned {len(bundles)} bundles in sections {list(sections)})"
    )
    return 0


def _run_remove_ids(
    *,
    sections: list[str],
    out_root: Path | None,
    dry_run: bool,
) -> int:
    if out_root is None:
        print(
            "error: bundle root not given (--out) and not configured in "
            ".bkkrc (repair.out / import.out / global.corpus)",
            file=sys.stderr,
        )
        return 2
    out_root = Path(out_root).expanduser().resolve()
    if not out_root.is_dir():
        print(f"error: bundle root is not a directory: {out_root}", file=sys.stderr)
        return 2

    from .identifiers import purge_non_alt_ids

    prefixes = tuple(sections)
    bundles = sorted(_iter_bundles_in_sections(out_root, prefixes))

    n_changed = 0
    n_unchanged = 0
    for bundle_dir in bundles:
        result = purge_non_alt_ids(bundle_dir, dry_run=dry_run)
        verb = "would drop" if dry_run else "dropped"
        if result["changed"]:
            n_changed += 1
            print(f"{verb} {bundle_dir.name}: {result['removed']}")
        else:
            n_unchanged += 1

    prefix = "dry-run: " if dry_run else ""
    print(
        f"{prefix}{n_changed} changed, {n_unchanged} unchanged "
        f"(scanned {len(bundles)} bundles in sections {list(sections)})"
    )
    return 0


def _configured_parallel_roots(
    *,
    parallels_root: Path | None,
    corpus_root: Path | None,
) -> tuple[Path | None, Path | None, Path]:
    from bkk.config import load_rc
    from .parallels import default_state_root

    rc = load_rc()
    if parallels_root is None:
        raw = (rc.get("serve") or {}).get("parallels_root") if isinstance(rc.get("serve"), dict) else None
        parallels_root = Path(raw).expanduser().resolve() if isinstance(raw, (str, Path)) else None
    if corpus_root is None:
        raw = (rc.get("global") or {}).get("corpus") if isinstance(rc.get("global"), dict) else None
        corpus_root = Path(raw).expanduser().resolve() if isinstance(raw, (str, Path)) else None
    if parallels_root is not None:
        parallels_root = parallels_root.expanduser().resolve()
    if corpus_root is not None:
        corpus_root = corpus_root.expanduser().resolve()
    state_root = default_state_root(parallels_root, corpus_root)
    return parallels_root, corpus_root, state_root


def _run_parallel_index(
    *,
    parallels_root: Path | None,
    corpus_root: Path | None,
) -> int:
    try:
        parallels_root, corpus_root, state_root = _configured_parallel_roots(
            parallels_root=parallels_root,
            corpus_root=corpus_root,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    from .parallels import build_parallel_asset_index

    summary = build_parallel_asset_index(
        state_root,
        parallels_root=parallels_root,
        corpus_root=corpus_root,
    )
    print(
        f"indexed {summary['markers']} parallel markers from "
        f"{summary['assets']} assets into {summary['index_path']}"
    )
    return 0


def _run_parallel_repair(
    *,
    parallels_root: Path | None,
    corpus_root: Path | None,
    rebuild_index: bool,
) -> int:
    try:
        parallels_root, corpus_root, state_root = _configured_parallel_roots(
            parallels_root=parallels_root,
            corpus_root=corpus_root,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    from .parallels import repair_pending_parallel_stale

    summary = repair_pending_parallel_stale(
        state_root,
        parallels_root=parallels_root,
        corpus_root=corpus_root,
        rebuild_index=rebuild_index,
    )
    print(
        f"repaired {summary['records_repaired']} stale records; "
        f"changed {summary['files_changed']} files; "
        f"shifted {summary['links_shifted']} links; "
        f"dropped {summary['links_dropped']} overlapping links"
    )
    return 0


def main() -> None:
    raise SystemExit(run())
