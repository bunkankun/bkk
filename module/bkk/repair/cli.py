"""Command-line entry point for ``bkk repair``.

Exposes repair operations for manifests and marker storage.

    python -m bkk repair manifest <out-root>/<text-id>/
    python -m bkk repair manifest <text-id>     # resolved via .bkkrc

For the bare-id form, the bundle root is resolved against (in order):
``repair.out``, ``import.out``, ``global.corpus`` from ``.bkkrc``. CLI
flags beat the rc file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bkk.short_refs import text_or_path_arg


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bkk repair")
    sub = p.add_subparsers(dest="op", required=True)

    pm = sub.add_parser(
        "manifest",
        help="rebuild the master and edition manifests from the juan "
             "files on disk (use after a multi-XML-file TLS bulk import)",
    )
    pm.add_argument(
        "bundle", type=text_or_path_arg,
        help="bundle directory, or a bare text-id resolved against "
             "repair.out / import.out / global.corpus from .bkkrc",
    )
    pm.add_argument(
        "--out", dest="out_root", type=Path, default=None,
        help="bundle output root used to resolve a bare text-id "
             "(overrides repair.out / import.out / global.corpus)",
    )

    px = sub.add_parser(
        "externalize-markers",
        help="move bulky inline juan markers into per-juan assets/*.markers.yaml files",
    )
    px.add_argument(
        "bundle", type=text_or_path_arg,
        help="bundle directory, or a bare text-id resolved against "
             "repair.out / import.out / global.corpus from .bkkrc",
    )
    px.add_argument(
        "--out", dest="out_root", type=Path, default=None,
        help="bundle output root used to resolve a bare text-id "
             "(overrides repair.out / import.out / global.corpus)",
    )
    px.add_argument(
        "--dry-run", action="store_true",
        help="report the migration without writing juans, marker assets, or manifests",
    )

    pi = sub.add_parser(
        "ids-from-krp-titles",
        help="populate metadata.identifiers.alt_id on master manifests "
             "from catalog/krp-titles.txt for the bundles in --section",
    )
    pi.add_argument(
        "--section", action="append", default=None, required=True,
        help="KRP prefix (e.g. KR5, KR6, KR5a); repeatable. A bundle is "
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
        "--section", action="append", default=None, required=True,
        help="KRP prefix (e.g. KR5, KR6, KR5a); repeatable. A bundle is "
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
    return p


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    out_root = args.out_root
    if out_root is None:
        # Defaults come from .bkkrc only when --out wasn't given.
        # `set_defaults` on the parent parser doesn't reach the subparser,
        # so we resolve the fallback after parsing instead.
        from bkk.config import load_rc
        rc = load_rc()
        out_root = (
            rc.get("repair", {}).get("out")
            or rc.get("import", {}).get("out")
            or rc.get("global", {}).get("corpus")
        )

    if args.op == "manifest":
        return _run_manifest(args.bundle, out_root)
    if args.op == "externalize-markers":
        return _run_externalize_markers(args.bundle, out_root, dry_run=args.dry_run)
    if args.op == "ids-from-krp-titles":
        return _run_ids_from_krp_titles(
            sections=args.section,
            titles_path=args.titles_path,
            out_root=out_root,
            dry_run=args.dry_run,
        )
    if args.op == "remove-ids":
        return _run_remove_ids(
            sections=args.section,
            out_root=out_root,
            dry_run=args.dry_run,
        )
    return 2


def _resolve_bundle_dir(bundle: str, out_root: Path | None) -> Path:
    """Treat ``bundle`` as a path if it points at an existing directory;
    otherwise resolve it as a text-id under ``out_root``."""
    p = Path(bundle).expanduser()
    if p.is_dir():
        return p.resolve()
    # Bare text-id (no path separators) → join against out_root.
    if out_root is not None and "/" not in bundle and "\\" not in bundle:
        candidate = (Path(out_root).expanduser() / bundle).resolve()
        if candidate.is_dir():
            return candidate
        raise FileNotFoundError(
            f"bundle directory not found: tried {p} and {candidate}"
        )
    raise FileNotFoundError(f"bundle directory not found: {p}")


def _run_manifest(bundle: str, out_root: Path | None) -> int:
    try:
        bundle_dir = _resolve_bundle_dir(bundle, out_root)
    except FileNotFoundError as exc:
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
    bundle: str, out_root: Path | None, *, dry_run: bool,
) -> int:
    try:
        bundle_dir = _resolve_bundle_dir(bundle, out_root)
    except FileNotFoundError as exc:
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


def main() -> None:
    raise SystemExit(run())
