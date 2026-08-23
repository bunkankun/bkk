"""Command-line entry point for ``bkk repair``.

Exposes repair operations for manifests and marker storage.

    python -m bkk repair manifest <out-root>/<text-id>/
    python -m bkk repair manifest --text-id <text-id>     # resolved via .bkkrc

For the bare-id form, the bundle root is resolved against (in order):
``repair.out``, ``global.corpus``, ``import.out`` from ``.bkkrc``. CLI
flags beat the rc file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bkk.cli_common import (
    add_text_prefix,
    resolve_bundle_dir,
    resolve_rc_path,
    warn_deprecated,
)
from bkk.repair.overlong_front import DEFAULT_MIN_CHARS
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
        help="text id to resolve against repair.out / global.corpus / import.out",
    )
    sp.add_argument(
        "--out", dest="out_root", type=Path, default=None,
        help="bundle output root used to resolve --text-id "
             "(overrides repair.out / global.corpus / import.out)",
    )
    if dry_run:
        sp.add_argument(
            "--dry-run", action="store_true",
            help="report the migration without writing juans, marker assets, or manifests",
        )


def _add_ctf_bundle_selector(sp: argparse.ArgumentParser) -> None:
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
        help="text id to resolve against repair.out / global.corpus / import.out",
    )
    add_text_prefix(
        sp,
        help="restrict to text ids starting with this prefix",
    )
    sp.add_argument(
        "--out", dest="out_root", type=Path, default=None,
        help=(
            "with --tsv, root for <section>/<text-id>.ctf.tsv "
            "(overrides global.ctf_root); otherwise bundle output root "
            "used to resolve --text-id/--text-prefix"
        ),
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

    pc = sub.add_parser(
        "ctf",
        help="write citation tree fragment sidecars from manifest TOC entries",
    )
    _add_ctf_bundle_selector(pc)
    pc.add_argument(
        "--juan",
        dest="juan_selectors",
        action="append",
        default=None,
        help=(
            "restrict CTF export to one complete juan; repeatable. Accepts "
            "KR refs like KR3k0059/147 as a standalone selector, or a "
            "bare seq like 147 with --bundle/--text-id/--text-prefix."
        ),
    )
    pc.add_argument(
        "--out-dir",
        dest="out_dir",
        type=Path,
        default=None,
        help="directory for CTF files (defaults to the bundle assets directory)",
    )
    pc.add_argument(
        "--heading-source",
        dest="heading_source",
        choices=("source-xml", "manifest", "auto", "voices", "derive"),
        default="source-xml",
        help="heading source: original source XML (default), manifest TOC, "
             "existing voices, fresh derivation, or auto existing-first",
    )
    pc.add_argument(
        "--short",
        dest="short_refs",
        action="store_true",
        help="emit compact refs such as 4c22/1/@8+37 instead of canonical refs",
    )
    pc.add_argument(
        "--tsv",
        dest="tsv",
        action="store_true",
        help="write one whole-text TSV containing id, parent_id, label, and end",
    )
    pc.add_argument(
        "--force", action="store_true",
        help="overwrite existing CTF files",
    )
    pc.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="report what would be written without modifying files",
    )

    ped = sub.add_parser(
        "remove-edition",
        help="delete editions/<short>, purge it from the master manifest, "
             "and remove that short from variant markers",
    )
    _add_bundle_selector(ped)
    ped.add_argument(
        "edition",
        help="edition short name to remove from editions/",
    )
    ped.add_argument(
        "--dry-run", action="store_true",
        help="report the deletion and marker cleanup without writing files",
    )

    pf = sub.add_parser(
        "front-to-body",
        help="move front-bucket content into body when the body bucket is empty "
             "(defaults to the whole corpus)",
        description="Defaults to scanning the whole configured corpus. "
                    "Use --bundle, --text-id, or --text-prefix to narrow the target.",
    )
    _add_bundle_selector(pf)
    pf.add_argument(
        "--text-prefix", action="append", default=None, dest="text_prefixes",
        type=text_prefix_arg,
        help="scan bundle directories under --out whose text ids start with this prefix; repeatable",
    )
    pf.add_argument(
        "--write", action="store_true",
        help="write juans, marker assets, and manifests; default is dry-run",
    )

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
        help="bundle output root (overrides repair.out / global.corpus / import.out)",
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
        help="bundle output root (overrides repair.out / global.corpus / import.out)",
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

    pno = sub.add_parser(
        "negative-offsets",
        help="report markers whose offset is negative (defaults to the whole corpus)",
        description="Scan inline juan markers and external marker assets for "
                    "negative marker offsets. Use --bundle, --text-id, or "
                    "--text-prefix to narrow the target.",
    )
    pno.add_argument(
        "legacy_bundle", nargs="?", type=text_or_path_arg,
        help=argparse.SUPPRESS,
    )
    pno.add_argument(
        "--bundle", dest="bundle", type=Path, default=None,
        help="bundle directory",
    )
    pno.add_argument(
        "--text-id", dest="text_id", type=text_id_arg, default=None,
        help="text id to resolve against repair.out / global.corpus / import.out",
    )
    pno.add_argument(
        "--text-prefix", action="append", default=None, dest="text_prefixes",
        type=text_prefix_arg,
        help="scan bundle directories under --out whose text ids start with this prefix; repeatable",
    )
    pno.add_argument(
        "--out", dest="out_root", type=Path, default=None,
        help="bundle output root (overrides repair.out / global.corpus / import.out)",
    )
    pno.add_argument(
        "--report", dest="report_path", type=Path, default=None,
        help="write JSONL report to this path; default is stdout",
    )

    pof = sub.add_parser(
        "overlong-front",
        help="move misplaced long front buckets into body",
        description="Scan juan front buckets and move the whole front bucket "
                    "to the beginning of body when front is longer than body, "
                    "or when a non-first part has an unusually long front. "
                    "Defaults to dry-run.",
    )
    pof.add_argument(
        "legacy_bundle", nargs="?", type=text_or_path_arg,
        help=argparse.SUPPRESS,
    )
    pof.add_argument(
        "--bundle", dest="bundle", type=Path, default=None,
        help="bundle directory",
    )
    pof.add_argument(
        "--text-id", dest="text_id", type=text_id_arg, default=None,
        help="text id to resolve against repair.out / global.corpus / import.out",
    )
    pof.add_argument(
        "--text-prefix", action="append", default=None, dest="text_prefixes",
        type=text_prefix_arg,
        help="scan bundle directories under --out whose text ids start with this prefix; repeatable",
    )
    pof.add_argument(
        "--out", dest="out_root", type=Path, default=None,
        help="bundle output root (overrides repair.out / global.corpus / import.out)",
    )
    pof.add_argument(
        "--min-chars", type=int, default=DEFAULT_MIN_CHARS,
        help=f"report front.text longer than this many characters (default: {DEFAULT_MIN_CHARS})",
    )
    pof.add_argument(
        "--include-first", action="store_true",
        help="also move an overlong first-part front when it is not longer than body",
    )
    pof.add_argument(
        "--dry-run", action="store_true",
        help="report planned changes without writing files (default)",
    )
    pof.add_argument(
        "--write", action="store_true",
        help="write juans, marker assets, and manifests; default is dry-run",
    )
    pof.add_argument(
        "--report", dest="report_path", type=Path, default=None,
        help="also write a JSONL report of discovered targets to this path",
    )

    ppb = sub.add_parser(
        "page-break",
        help="synthesize missing page-break markers before first-line line-break markers",
        description="Patch marker assets where a KRP first line-break such as "
                    "<page-id>01 appears but the matching page-break is absent. "
                    "Defaults to scanning the whole configured corpus.",
    )
    _add_bundle_selector(ppb)
    ppb.add_argument(
        "--text-prefix", action="append", default=None, dest="text_prefixes",
        type=text_prefix_arg,
        help="scan bundle directories under --out whose text ids start with this prefix; repeatable",
    )
    ppb.add_argument(
        "--write", action="store_true",
        help="write marker assets and manifests; default is dry-run",
    )

    pts = sub.add_parser(
        "tls-seg-start-ids",
        help="rename duplicated TLS typed-segment run start marker ids",
        description="Patch old TLS imports where a synthetic tls:seg-start "
                    "marker reused the first member tls:seg id. The repaired "
                    "id uses the current importer convention "
                    "{first_member_id}_start. Defaults to dry-run.",
    )
    _add_bundle_selector(pts)
    pts.add_argument(
        "--text-prefix", action="append", default=None, dest="text_prefixes",
        type=text_prefix_arg,
        help="scan bundle directories under --out whose text ids start with this prefix; repeatable",
    )
    pts.add_argument(
        "--write", action="store_true",
        help="write juans, marker assets, and manifests; default is dry-run",
    )

    pvp = sub.add_parser(
        "voice-paren-boundary",
        help="move body offset-0 ')' punctuation markers to the end of front "
             "when the voice-problems report shows the paired front/body "
             "paren-boundary error",
        description="Uses the voice-problems JSONL report as a targeting "
                    "guide, then moves the actual source punctuation marker. "
                    "Defaults to the configured corpus/report and dry-run.",
    )
    pvp.add_argument(
        "--out", dest="out_root", type=Path, default=None,
        help="corpus root (overrides repair.out / global.corpus / import.out)",
    )
    pvp.add_argument(
        "--report", dest="report_path", type=Path, default=None,
        help="voice-problems JSONL report path (defaults to [voice].report "
             "or BKK_VOICE_PROBLEMS_REPORT)",
    )
    pvp.add_argument(
        "--text-id", dest="text_id", type=text_id_arg, default=None,
        help="restrict to one text id in the report",
    )
    pvp.add_argument(
        "--text-prefix", action="append", default=None, dest="text_prefixes",
        type=text_prefix_arg,
        help="restrict to text ids starting with this prefix; repeatable",
    )
    pvp.add_argument(
        "--write", action="store_true",
        help="write marker assets and manifests; default is dry-run",
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
            (("repair", "out"), ("global", "corpus"), ("import", "out")),
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
    if args.op == "ctf":
        return _run_ctf_repair(args=args, out_root=out_root)
    if args.op == "remove-edition":
        try:
            bundle, text_id = _selected_bundle_args(args)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return _run_remove_edition(
            bundle,
            out_root,
            text_id=text_id,
            edition_short=args.edition,
            dry_run=args.dry_run,
        )
    if args.op == "front-to-body":
        prefixes = getattr(args, "text_prefixes", None) or []
        if prefixes:
            if any((
                getattr(args, "legacy_bundle", None),
                getattr(args, "bundle", None),
                getattr(args, "text_id", None),
            )):
                print(
                    "error: provide either --text-prefix or a single bundle/text id",
                    file=sys.stderr,
                )
                return 2
            return _run_front_to_body_prefixes(
                sections=prefixes,
                out_root=out_root,
                dry_run=not args.write,
            )
        if not any((
            getattr(args, "legacy_bundle", None),
            getattr(args, "bundle", None),
            getattr(args, "text_id", None),
        )):
            return _run_front_to_body_prefixes(
                sections=[],
                out_root=out_root,
                dry_run=not args.write,
            )
        try:
            bundle, text_id = _selected_bundle_args(args)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return _run_front_to_body(
            bundle, out_root, text_id=text_id, dry_run=not args.write,
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
    if args.op == "negative-offsets":
        return _run_negative_offsets(
            args=args,
            out_root=out_root,
        )
    if args.op == "overlong-front":
        return _run_overlong_front(
            args=args,
            out_root=out_root,
        )
    if args.op == "page-break":
        return _run_page_break(
            args=args,
            out_root=out_root,
        )
    if args.op == "tls-seg-start-ids":
        return _run_tls_seg_start_ids(
            args=args,
            out_root=out_root,
        )
    if args.op == "voice-paren-boundary":
        return _run_voice_paren_boundary(
            args=args,
            out_root=out_root,
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


def _run_ctf_repair(
    *,
    args: argparse.Namespace,
    out_root: Path | None,
) -> int:
    from bkk.voice.cli import _run_ctf, _selected_add_args

    ctf_out_root = None
    ctf_bundle_root = out_root
    if args.tsv:
        ctf_out_root = args.out_root
        if args.out_root is not None:
            from bkk.config import load_rc
            rc = load_rc()
            ctf_bundle_root = resolve_rc_path(
                None,
                rc,
                (("repair", "out"), ("global", "corpus"), ("import", "out")),
            )

    try:
        bundle, text_id, text_prefix, selected_juans = _selected_add_args(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if text_prefix is not None:
        return _run_ctf(
            bundle,
            ctf_bundle_root,
            text_id=text_id,
            text_prefix=text_prefix,
            selected_juans=selected_juans,
            out_dir=args.out_dir,
            tsv=args.tsv,
            tsv_out_root=ctf_out_root,
            heading_source=args.heading_source,
            short_refs=args.short_refs,
            force=args.force,
            dry_run=args.dry_run,
        )
    return _run_ctf(
        bundle,
        ctf_bundle_root,
        text_id=text_id,
        selected_juans=selected_juans,
        out_dir=args.out_dir,
        tsv=args.tsv,
        tsv_out_root=ctf_out_root,
        heading_source=args.heading_source,
        short_refs=args.short_refs,
        force=args.force,
        dry_run=args.dry_run,
    )


def _run_remove_edition(
    bundle: str | Path | None,
    out_root: Path | None,
    *,
    text_id: str | None = None,
    edition_short: str,
    dry_run: bool,
) -> int:
    try:
        bundle_dir = _resolve_bundle_dir(bundle, out_root, text_id=text_id)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    from .remove_edition import remove_edition
    try:
        summary = remove_edition(
            bundle_dir,
            edition_short,
            dry_run=dry_run,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    prefix = "would remove" if dry_run else "removed"
    print(f"{prefix} edition {edition_short}: {summary['edition_dir']}")
    for scope in summary["scopes"]:
        changed = []
        if scope["manifest_changed"]:
            changed.append("manifest")
        if scope["juans_changed"]:
            changed.append(f"{len(scope['juans_changed'])} juan(s)")
        marker_count = scope["marker_assets_changed"] + scope["marker_assets_deleted"]
        if marker_count:
            changed.append(f"{marker_count} marker asset(s)")
        if not changed:
            continue
        print(
            f"  updated {scope['manifest']}: "
            f"{scope['variant_witnesses_removed']} variant witness(es) removed, "
            f"{scope['variant_markers_dropped']} variant marker(s) dropped; "
            + ", ".join(changed)
        )
    if dry_run:
        print("dry-run only; rerun without --dry-run to update files")
    return 0


def _run_front_to_body(
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

    from .front_body import move_front_to_empty_body
    summary = move_front_to_empty_body(bundle_dir, dry_run=dry_run)
    prefix = "would move" if dry_run else "moved"
    errors = 0
    for scope in summary["scopes"]:
        errors += len(scope.get("errors") or [])
        print(
            f"{prefix} {scope['manifest']}: "
            f"{scope['moved']} juans, {scope['chars']} chars"
            + (f", skipped {len(scope.get('errors') or [])}" if scope.get("errors") else "")
        )
        for line in scope["lines"]:
            print(f"  {line}")
    if dry_run:
        print("dry-run only; pass --write to update files")
    return 1 if errors else 0


def _run_front_to_body_prefixes(
    *,
    sections: list[str],
    out_root: Path | None,
    dry_run: bool,
) -> int:
    if out_root is None:
        print(
            "error: bundle root not given (--out) and not configured in "
            ".bkkrc (repair.out / global.corpus / import.out)",
            file=sys.stderr,
        )
        return 2
    out_root = Path(out_root).expanduser().resolve()
    if not out_root.is_dir():
        print(f"error: bundle root is not a directory: {out_root}", file=sys.stderr)
        return 2

    from .front_body import move_front_to_empty_body

    prefixes = tuple(sections) if sections else ("",)
    bundles = sorted(_iter_bundles_in_sections(out_root, prefixes))
    changed = 0
    chars = 0
    errors = 0
    prefix = "would move" if dry_run else "moved"
    for bundle_dir in bundles:
        summary = move_front_to_empty_body(bundle_dir, dry_run=dry_run)
        bundle_juans = sum(scope["moved"] for scope in summary["scopes"])
        bundle_chars = sum(scope["chars"] for scope in summary["scopes"])
        bundle_errors = sum(len(scope.get("errors") or []) for scope in summary["scopes"])
        if not bundle_juans and not bundle_errors:
            continue
        changed += bundle_juans
        chars += bundle_chars
        errors += bundle_errors
        print(f"{bundle_dir.name}:")
        for scope in summary["scopes"]:
            if not scope["moved"] and not scope.get("errors"):
                continue
            print(
                f"  {prefix} {scope['manifest']}: "
                f"{scope['moved']} juans, {scope['chars']} chars"
                + (f", skipped {len(scope.get('errors') or [])}" if scope.get("errors") else "")
            )
            for line in scope["lines"]:
                print(f"    {line}")
    mode = "dry-run: " if dry_run else ""
    target = f"sections {list(sections)}" if sections else "corpus"
    skipped = f"{errors} skipped; " if errors else ""
    print(
        f"{mode}{changed} juans, {chars} chars "
        f"({skipped}scanned {len(bundles)} bundles in {target})"
    )
    if dry_run:
        print("dry-run only; pass --write to update files")
    return 1 if errors else 0


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
            ".bkkrc (repair.out / global.corpus / import.out)",
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
            ".bkkrc (repair.out / global.corpus / import.out)",
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


def _run_negative_offsets(
    *,
    args: argparse.Namespace,
    out_root: Path | None,
) -> int:
    prefixes = getattr(args, "text_prefixes", None) or []
    has_single = any((
        getattr(args, "legacy_bundle", None),
        getattr(args, "bundle", None),
        getattr(args, "text_id", None),
    ))
    if prefixes and has_single:
        print(
            "error: provide either --text-prefix or a single bundle/text id",
            file=sys.stderr,
        )
        return 2

    from .negative_offsets import (
        find_negative_offset_markers,
        find_negative_offset_markers_in_bundle,
        write_negative_offset_report,
    )

    try:
        if has_single:
            bundle, text_id = _selected_bundle_args(args)
            bundle_dir = _resolve_bundle_dir(bundle, out_root, text_id=text_id)
            summary = find_negative_offset_markers_in_bundle(bundle_dir)
            bundles_scanned = 1
        else:
            if out_root is None:
                print(
                    "error: bundle root not given (--out) and not configured in "
                    ".bkkrc (repair.out / global.corpus / import.out)",
                    file=sys.stderr,
                )
                return 2
            root = Path(out_root).expanduser().resolve()
            if not root.is_dir():
                print(f"error: bundle root is not a directory: {root}", file=sys.stderr)
                return 2
            summary = find_negative_offset_markers(
                root,
                text_prefixes=prefixes,
            )
            bundles_scanned = summary["bundles_scanned"]
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    rows = summary["rows"]
    report_path = getattr(args, "report_path", None)
    if report_path is None:
        write_negative_offset_report(rows, sys.stdout)
        summary_stream = sys.stderr
    else:
        try:
            write_negative_offset_report(rows, report_path)
        except OSError as exc:
            print(f"error: could not write report {report_path}: {exc}", file=sys.stderr)
            return 1
        print(
            f"wrote {len(rows)} negative-offset marker(s) to {report_path} "
            f"(scanned {bundles_scanned} bundles)"
        )
        summary_stream = sys.stderr

    errors = summary.get("errors") or []
    for error in errors[:20]:
        print(
            f"warning: skipped {error.get('path')}: {error.get('message')}",
            file=sys.stderr,
        )
    if len(errors) > 20:
        print(
            f"warning: suppressed {len(errors) - 20} more scan error(s)",
            file=sys.stderr,
        )
    if report_path is None:
        print(
            f"found {len(rows)} negative-offset marker(s) "
            f"(scanned {bundles_scanned} bundles)",
            file=summary_stream,
        )
    return 1 if errors else 0


def _run_overlong_front(
    *,
    args: argparse.Namespace,
    out_root: Path | None,
) -> int:
    if args.write and args.dry_run:
        print("error: provide only one of --dry-run or --write", file=sys.stderr)
        return 2
    dry_run = not args.write
    prefixes = getattr(args, "text_prefixes", None) or []
    has_single = any((
        getattr(args, "legacy_bundle", None),
        getattr(args, "bundle", None),
        getattr(args, "text_id", None),
    ))
    if prefixes and has_single:
        print(
            "error: provide either --text-prefix or a single bundle/text id",
            file=sys.stderr,
        )
        return 2

    from .overlong_front import (
        find_overlong_front_buckets,
        find_overlong_front_buckets_in_bundle,
        repair_overlong_front_buckets,
        repair_overlong_front_buckets_in_bundle,
        write_overlong_front_report,
    )

    report_summary = None
    try:
        if has_single:
            bundle, text_id = _selected_bundle_args(args)
            bundle_dir = _resolve_bundle_dir(bundle, out_root, text_id=text_id)
            if args.report_path is not None:
                report_summary = find_overlong_front_buckets_in_bundle(
                    bundle_dir,
                    min_chars=args.min_chars,
                    include_first=args.include_first,
                )
            summary = repair_overlong_front_buckets_in_bundle(
                bundle_dir,
                min_chars=args.min_chars,
                include_first=args.include_first,
                dry_run=dry_run,
            )
            bundles_scanned = 1
        else:
            if out_root is None:
                print(
                    "error: bundle root not given (--out) and not configured in "
                    ".bkkrc (repair.out / global.corpus / import.out)",
                    file=sys.stderr,
                )
                return 2
            root = Path(out_root).expanduser().resolve()
            if not root.is_dir():
                print(f"error: bundle root is not a directory: {root}", file=sys.stderr)
                return 2
            if args.report_path is not None:
                report_summary = find_overlong_front_buckets(
                    root,
                    text_prefixes=prefixes,
                    min_chars=args.min_chars,
                    include_first=args.include_first,
                )
            summary = repair_overlong_front_buckets(
                root,
                text_prefixes=prefixes,
                min_chars=args.min_chars,
                include_first=args.include_first,
                dry_run=dry_run,
            )
            bundles_scanned = summary["bundles_scanned"]
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    report_path = getattr(args, "report_path", None)
    if report_path is not None and report_summary is not None:
        rows = report_summary["rows"]
        try:
            write_overlong_front_report(rows, report_path)
        except OSError as exc:
            print(f"error: could not write report {report_path}: {exc}", file=sys.stderr)
            return 1
        print(
            f"wrote {len(rows)} overlong front target(s) to {report_path} "
            f"(scanned {bundles_scanned} bundles)"
        )

    bundle_summaries = summary.get("bundles") or [summary]
    prefix = "would move" if dry_run else "moved"
    changed = 0
    chars = 0
    errors = 0
    for bundle_summary in bundle_summaries:
        bundle_juans = sum(scope["moved"] for scope in bundle_summary["scopes"])
        bundle_chars = sum(scope["chars"] for scope in bundle_summary["scopes"])
        bundle_errors = sum(
            len(scope.get("errors") or []) for scope in bundle_summary["scopes"]
        )
        if len(bundle_summaries) > 1 and not bundle_juans and not bundle_errors:
            continue
        changed += bundle_juans
        chars += bundle_chars
        errors += bundle_errors
        if len(bundle_summaries) > 1:
            print(f"{Path(bundle_summary['bundle_dir']).name}:")
            indent = "  "
            line_indent = "    "
        else:
            indent = ""
            line_indent = "  "
        for scope in bundle_summary["scopes"]:
            if len(bundle_summaries) > 1 and not scope["moved"] and not scope.get("errors"):
                continue
            print(
                f"{indent}{prefix} {scope['manifest']}: "
                f"{scope['moved']} juans, {scope['chars']} chars"
                + (f", skipped {len(scope.get('errors') or [])}" if scope.get("errors") else "")
            )
            for line in scope["lines"]:
                print(f"{line_indent}{line}")

    mode = "dry-run: " if dry_run else ""
    target = f"prefixes {list(prefixes)}" if prefixes else "corpus"
    if has_single:
        target = "bundle"
    skipped = f"{errors} skipped; " if errors else ""
    print(
        f"{mode}{changed} juans, {chars} chars "
        f"({skipped}scanned {bundles_scanned} bundles in {target})"
    )
    if dry_run:
        print("dry-run only; pass --write to update files")

    if report_summary is not None:
        report_errors = report_summary.get("errors") or []
        for error in report_errors[:20]:
            print(
                f"warning: skipped {error.get('path')}: {error.get('message')}",
                file=sys.stderr,
            )
        if len(report_errors) > 20:
            print(
                f"warning: suppressed {len(report_errors) - 20} more scan error(s)",
                file=sys.stderr,
            )
        errors += len(report_errors)
    return 1 if errors else 0


def _run_page_break(
    *,
    args: argparse.Namespace,
    out_root: Path | None,
) -> int:
    prefixes = getattr(args, "text_prefixes", None) or []
    has_single = any((
        getattr(args, "legacy_bundle", None),
        getattr(args, "bundle", None),
        getattr(args, "text_id", None),
    ))
    if prefixes and has_single:
        print(
            "error: provide either --text-prefix or a single bundle/text id",
            file=sys.stderr,
        )
        return 2

    from .page_break import synthesize_missing_page_breaks

    try:
        if has_single:
            bundle, text_id = _selected_bundle_args(args)
            bundles = [_resolve_bundle_dir(bundle, out_root, text_id=text_id)]
            target = "bundle"
        else:
            if out_root is None:
                print(
                    "error: bundle root not given (--out) and not configured in "
                    ".bkkrc (repair.out / global.corpus / import.out)",
                    file=sys.stderr,
                )
                return 2
            root = Path(out_root).expanduser().resolve()
            if not root.is_dir():
                print(f"error: bundle root is not a directory: {root}", file=sys.stderr)
                return 2
            prefix_tuple = tuple(prefixes) if prefixes else ("",)
            bundles = sorted(_iter_bundles_in_sections(root, prefix_tuple))
            target = f"prefixes {list(prefixes)}" if prefixes else "corpus"
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    dry_run = not args.write
    prefix = "would insert" if dry_run else "inserted"
    total = 0
    scopes_changed = 0
    for bundle_dir in bundles:
        summary = synthesize_missing_page_breaks(bundle_dir, dry_run=dry_run)
        bundle_inserted = sum(scope["inserted"] for scope in summary["scopes"])
        if not bundle_inserted:
            continue
        total += bundle_inserted
        print(f"{bundle_dir.name}:")
        for scope in summary["scopes"]:
            if not scope["inserted"]:
                continue
            scopes_changed += 1
            print(
                f"  {prefix} {scope['manifest']}: "
                f"{scope['inserted']} page-break marker(s)"
            )
            for line in scope["lines"]:
                print(f"    {line}")

    mode = "dry-run: " if dry_run else ""
    print(
        f"{mode}{total} page-break marker(s), "
        f"{scopes_changed} manifest scope(s) changed "
        f"(scanned {len(bundles)} bundles in {target})"
    )
    if dry_run:
        print("dry-run only; pass --write to update files")
    return 0


def _run_tls_seg_start_ids(
    *,
    args: argparse.Namespace,
    out_root: Path | None,
) -> int:
    prefixes = getattr(args, "text_prefixes", None) or []
    has_single = any((
        getattr(args, "legacy_bundle", None),
        getattr(args, "bundle", None),
        getattr(args, "text_id", None),
    ))
    if prefixes and has_single:
        print(
            "error: provide either --text-prefix or a single bundle/text id",
            file=sys.stderr,
        )
        return 2

    from .tls_seg_start_ids import repair_tls_seg_start_ids

    try:
        if has_single:
            bundle, text_id = _selected_bundle_args(args)
            bundles = [_resolve_bundle_dir(bundle, out_root, text_id=text_id)]
            target = "bundle"
        else:
            if out_root is None:
                print(
                    "error: bundle root not given (--out) and not configured in "
                    ".bkkrc (repair.out / global.corpus / import.out)",
                    file=sys.stderr,
                )
                return 2
            root = Path(out_root).expanduser().resolve()
            if not root.is_dir():
                print(f"error: bundle root is not a directory: {root}", file=sys.stderr)
                return 2
            prefix_tuple = tuple(prefixes) if prefixes else ("",)
            bundles = sorted(_iter_bundles_in_sections(root, prefix_tuple))
            target = f"prefixes {list(prefixes)}" if prefixes else "corpus"
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    dry_run = not args.write
    prefix = "would rename" if dry_run else "renamed"
    total = 0
    scopes_changed = 0
    for bundle_dir in bundles:
        summary = repair_tls_seg_start_ids(bundle_dir, dry_run=dry_run)
        bundle_renamed = sum(scope["renamed"] for scope in summary["scopes"])
        if not bundle_renamed:
            continue
        total += bundle_renamed
        print(f"{bundle_dir.name}:")
        for scope in summary["scopes"]:
            if not scope["renamed"]:
                continue
            scopes_changed += 1
            print(
                f"  {prefix} {scope['manifest']}: "
                f"{scope['renamed']} tls:seg-start marker id(s)"
            )
            for line in scope["lines"]:
                print(f"    {line}")

    mode = "dry-run: " if dry_run else ""
    print(
        f"{mode}{total} tls:seg-start marker id(s), "
        f"{scopes_changed} manifest scope(s) changed "
        f"(scanned {len(bundles)} bundles in {target})"
    )
    if dry_run:
        print("dry-run only; pass --write to update files")
    return 0


def _run_voice_paren_boundary(
    *,
    args: argparse.Namespace,
    out_root: Path | None,
) -> int:
    if args.text_id and args.text_prefixes:
        print("error: provide either --text-id or --text-prefix", file=sys.stderr)
        return 2
    if out_root is None:
        print(
            "error: corpus root not given (--out) and not configured in "
            ".bkkrc (repair.out / global.corpus / import.out)",
            file=sys.stderr,
        )
        return 2
    root = Path(out_root).expanduser().resolve()
    if not root.is_dir():
        print(f"error: corpus root is not a directory: {root}", file=sys.stderr)
        return 2

    report_path = args.report_path or _configured_voice_report_path()
    if report_path is None:
        print(
            "error: report path is required: pass --report, set [voice].report, "
            "or set BKK_VOICE_PROBLEMS_REPORT",
            file=sys.stderr,
        )
        return 2

    from .voice_parens import move_body_initial_close_parens_from_report

    dry_run = not args.write
    try:
        summary = move_body_initial_close_parens_from_report(
            root,
            report_path,
            dry_run=dry_run,
            text_id=args.text_id,
            text_prefixes=args.text_prefixes or [],
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    prefix = "would move" if dry_run else "moved"
    for bundle in summary["bundles"]:
        if not bundle["moved"] and not bundle["skipped"]:
            continue
        print(f"{bundle['textid']}:")
        for scope in bundle["scopes"]:
            if not scope["moved"] and not scope["skipped"]:
                continue
            print(
                f"  {prefix} {scope['manifest']}: "
                f"{scope['moved']} close-paren marker(s)"
                + (f", skipped {scope['skipped']}" if scope["skipped"] else "")
            )
            for move in scope["moves"][:10]:
                print(
                    f"    juan {move['seq']:03d}: {move['marker_id']} "
                    f"body@0 -> front@{move['to'][1]}"
                )
            if len(scope["moves"]) > 10:
                print(f"    ... {len(scope['moves']) - 10} more move(s)")
            for skip in scope["skips"][:10]:
                print(f"    juan {skip['seq']:03d}: skipped: {skip['reason']}")
            if len(scope["skips"]) > 10:
                print(f"    ... {len(scope['skips']) - 10} more skip(s)")

    for error in summary["errors"][:20]:
        print(
            f"warning: skipped {error.get('textid')}: {error.get('message')}",
            file=sys.stderr,
        )
    if len(summary["errors"]) > 20:
        print(
            f"warning: suppressed {len(summary['errors']) - 20} more error(s)",
            file=sys.stderr,
        )

    mode = "dry-run: " if dry_run else ""
    target = args.text_id or (
        f"prefixes {args.text_prefixes}" if args.text_prefixes else "report"
    )
    print(
        f"{mode}{summary['moved']} close-paren marker(s) moved, "
        f"{summary['skipped']} skipped "
        f"from {summary['targets']} targeted juan scope(s) ({target})"
    )
    if dry_run:
        print("dry-run only; pass --write to update files")
    return 1 if summary["errors"] else 0


def _configured_voice_report_path() -> Path | None:
    import os

    env = os.environ.get("BKK_VOICE_PROBLEMS_REPORT")
    if env:
        return Path(env).resolve()
    from bkk.config import load_rc
    rc = load_rc()
    report = (rc.get("voice") or {}).get("report")
    if report:
        return Path(report).resolve()
    return None


def main() -> None:
    raise SystemExit(run())
