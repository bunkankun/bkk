"""Command-line interface for the BKK index.

Subcommands::

    python -m bkk.index build <bundle_dir> [--out PATH] [--jobs N]
    python -m bkk.index catalog <corpus> [--csv PATH] [--out PATH]
                                         [--prefix KR3a]
    python -m bkk.index translations <corpus> [--out PATH]
    python -m bkk.index annotations [annotations_root] [--out PATH]
    python -m bkk.index merge <corpus> [--out PATH] [--prefix KR3a]
                                       [--section KR6 | --section KR6q]
                                       [--rebuild | --no-build] [--jobs N]
    python -m bkk.index core <core_root> [--out PATH]
    python -m bkk.index parallel <bkkx_path> <seed> [--out PATH]
                                                [--format jsonl|tsv]
    python -m bkk.index parallel [<bkkx_path>] --text-id KR1h0004[/1]
    python -m bkk.index parallel-scan <bkkx_path> [--out PATH]
                                                     [--work-dir DIR]
    python -m bkk.index duplications <bkkx_path> [--out PATH]
                                                 [--min-length N]
                                                 [--min-pair-chars N]
    python -m bkk.index search <bkkx_path> <query> [--context N]
                                                   [--witness LABEL]...
                                                   [--textid ID]
                                                   [--voice NAME]...
"""

from __future__ import annotations

import argparse
from pathlib import Path

from bkk.cli_common import warn_deprecated
from bkk.short_refs import text_id_arg, text_prefix_arg

from .annotations import build_annotation_index
from .build import build_index
from .catalog import build_catalog_index, default_catalog_csv
from .core import build_core_index
from .ir import Hit
from .merge import merge_bundles
from .parallel import discover_parallel_passages, write_parallel_report
from .parallel_scan import discover_parallel_passages_scan
from .query import Index
from .translation import build_translation_index, merge_translations


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bkk.index")
    sub = p.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("build", help="build a .bkkx index from a bundle directory")
    pb.add_argument("bundle_dir", type=Path)
    pb.add_argument("--out", type=Path, default=None,
                    help="output path (default: <bundle_dir>/<textid>.bkkx)")
    pb.add_argument("--jobs", type=int, default=1,
                    help="worker processes for parsing juan files (default: 1)")

    pm = sub.add_parser("merge", help="merge per-bundle indices under a corpus")
    pm.add_argument("corpus", type=Path, nargs="?", default=None,
                    help="corpus directory (or set global.corpus in .bkkrc)")
    pm.add_argument("--out", type=Path, default=None,
                    help="merged .bkkx output path "
                         "(default: index.out from .bkkrc, "
                         "else <corpus>/_corpus.bkkx)")
    pm.add_argument("--prefix", default=None,
                    help="restrict to bundles whose textid starts with PREFIX "
                         "(e.g. KR3a)")
    pm.add_argument("--text-prefix", dest="text_prefix", default=None,
                    type=text_prefix_arg,
                    help="restrict to bundles whose textid starts with PREFIX "
                         "(e.g. KR3a)")
    pm.add_argument("--section", default=None,
                    help="restrict to a (sub)section like KR6 or KR6q; "
                         "filters by textid prefix and writes the output to "
                         "_<section>.bkkx alongside the full index")
    grp = pm.add_mutually_exclusive_group()
    grp.add_argument("--rebuild", action="store_true",
                     help="rebuild every per-bundle .bkkx, ignoring mtimes")
    grp.add_argument("--no-build", action="store_true",
                     help="error if any per-bundle .bkkx is missing or stale")
    pm.add_argument("--jobs", type=int, default=1,
                    help="worker processes for per-bundle rebuilds (default: 1)")

    pc = sub.add_parser("catalog", help="build a .bkkc catalog index for a corpus")
    pc.add_argument("corpus", type=Path, nargs="?", default=None,
                    help="corpus directory (or set global.corpus in .bkkrc)")
    pc.add_argument("--csv", type=Path, default=None, dest="csv_path",
                    help="frontmatter CSV path "
                         "(default: nearest catalog/frontmatter.csv)")
    pc.add_argument("--out", type=Path, default=None,
                    help="catalog .bkkc output path "
                         "(default: <corpus>/_catalog.bkkc)")
    pc.add_argument("--prefix", default=None,
                    help="restrict to bundles whose textid starts with PREFIX "
                         "(e.g. KR3a)")
    pc.add_argument("--text-prefix", dest="text_prefix", default=None,
                    type=text_prefix_arg,
                    help="restrict to bundles whose textid starts with PREFIX "
                         "(e.g. KR3a)")
    pc.add_argument("--csv-stub", type=Path, default=None, dest="csv_stub",
                    help="append stub rows for bundles missing from the CSV "
                         "to this file (created with header if absent)")

    pt = sub.add_parser("translations", help="build/merge per-bundle .bkkt translation search indices")
    pt.add_argument("corpus", type=Path, nargs="?", default=None,
                    help="corpus directory (or set global.corpus in .bkkrc)")
    pt.add_argument("--out", type=Path, default=None,
                    help="merged output path (default: <corpus>/_translations.bkkt)")
    grp = pt.add_mutually_exclusive_group()
    grp.add_argument("--rebuild", action="store_true",
                     help="rebuild every per-bundle .bkkt, ignoring mtimes")
    grp.add_argument("--no-build", action="store_true",
                     help="error if any per-bundle .bkkt is missing or stale")

    pa = sub.add_parser("annotations", help="build a .bkka index over a bkk-annotations archive")
    pa.add_argument("annotations_root", type=Path, nargs="?", default=None,
                    help="annotations archive root "
                         "(or set annotations.annotations_root / serve.annotations_root in .bkkrc)")
    pa.add_argument("--out", type=Path, default=None,
                    help="annotation .bkka output path "
                         "(default: <annotations_root>/_annotations.bkka)")
    pa.add_argument("--core-index", type=Path, default=None,
                    help="path to the .bkki core index used to denormalise "
                         "syn/sem labels onto annotation rows "
                         "(defaults to core.index in .bkkrc; "
                         "missing/stale → NULL labels with a warning)")

    pco = sub.add_parser("core", help="build a .bkki index over the bkk-core knowledge layer")
    pco.add_argument("core_root", type=Path, nargs="?", default=None,
                     help="core root directory (or set core.root in .bkkrc)")
    pco.add_argument("--out", type=Path, default=None,
                     help="core .bkki output path "
                          "(default: core.index from .bkkrc, "
                          "else <core_root>/_core.bkki)")

    pp = sub.add_parser("parallel", help="discover exact repeated passages in a .bkkx index")
    pp.add_argument(
        "index_path", type=Path, nargs="?", default=None,
        help="index path (with --text-id: defaults to index.out or "
             "<configured corpus>/_corpus.bkkx)",
    )
    pp.add_argument("seed", nargs="?", default=None,
                    help="1-6 character seed term to extend around")
    pp.add_argument("--out", type=Path, default=None,
                    help="output path (default: stdout)")
    pp.add_argument("--bucket", choices=["front", "body", "back", "all"],
                    default="body",
                    help="bucket kind to scan (default: body)")
    pp.add_argument("--min-length", type=int, default=12,
                    help="minimum repeated passage length in characters (default: 12)")
    pp.add_argument("--min-occurrences", type=int, default=2,
                    help="minimum locations per cluster (default: 2)")
    pp.add_argument("--max-postings", type=int, default=500,
                    help="maximum seed postings, or gram postings under --full-scan "
                         "(default: 500)")
    pp.add_argument("--format", choices=["jsonl", "tsv"], default=None,
                    help="report format (default: jsonl)")
    pp.add_argument("--context", type=int, default=20,
                    help="snippet context around each occurrence (default: 20)")
    pp.add_argument("--include-contained", action="store_true",
                    help="include clusters wholly contained in longer clusters")
    pp.add_argument("--max-edits", type=int, default=0,
                    help="allow up to N character edits (sub/ins/del) per "
                         "occurrence vs. the cluster representative "
                         "(0-4, default: 0 = exact)")
    pp.add_argument("--full-scan", action="store_true",
                    help="scan all trigram anchors; expensive and intended only for small indices")
    pp.add_argument("--force-full-scan", action="store_true",
                    help="allow --full-scan on corpus indices")
    pp.add_argument("--text-id", dest="text_id", default=None,
                    help="scan a text or juan against the complete index "
                         "(KR1h0004, KR1h0004/1, or shortcut 1h4/1)")
    pp.add_argument("--name", default=None,
                    help="parallel source name in generated filenames "
                         "(default: index filename without extension/leading _)")

    pps = sub.add_parser(
        "parallel-scan",
        help="external-memory scan for exact repeated passages",
    )
    pps.add_argument("index_path", type=Path)
    pps.add_argument("--out", type=Path, default=None,
                     help="output path (default: stdout)")
    pps.add_argument("--work-dir", type=Path, default=None,
                     help="directory for temporary partition/work files "
                          "(default: next to the index)")
    pps.add_argument("--bucket", choices=["front", "body", "back", "all"],
                     default="body",
                     help="bucket kind to scan (default: body)")
    pps.add_argument("--min-length", type=int, default=24,
                     help="minimum repeated passage length in characters (default: 24)")
    pps.add_argument("--anchor-length", type=int, default=12,
                     help="fingerprint length in characters (default: 12)")
    pps.add_argument("--min-occurrences", type=int, default=2,
                     help="minimum locations per cluster (default: 2)")
    pps.add_argument("--max-anchor-occurrences", type=int, default=200,
                     help="skip an anchor hash above this occurrence count (default: 200)")
    pps.add_argument("--partitions", type=int, default=256,
                     help="number of hash partitions (default: 256)")
    pps.add_argument("--format", choices=["jsonl", "tsv"], default="jsonl",
                     help="report format (default: jsonl)")
    pps.add_argument("--context", type=int, default=20,
                     help="snippet context around each occurrence (default: 20)")
    pps.add_argument("--include-contained", action="store_true",
                     help="include clusters wholly contained in longer clusters")
    pps.add_argument("--quiet", action="store_true",
                     help="suppress progress logging")

    pd = sub.add_parser(
        "duplications",
        help="rank juan-pairs by long duplicated spans (aggregates parallel-scan)",
    )
    pd.add_argument("index_path", type=Path)
    pd.add_argument("--out", type=Path, default=None,
                    help="output path (default: stdout)")
    pd.add_argument("--work-dir", type=Path, default=None,
                    help="directory for temporary partition/work files "
                         "(default: next to the index)")
    pd.add_argument("--bucket", choices=["front", "body", "back", "all"],
                    default="body",
                    help="bucket kind to scan (default: body)")
    pd.add_argument("--min-length", type=int, default=200,
                    help="minimum repeated passage length in characters (default: 200)")
    pd.add_argument("--anchor-length", type=int, default=12,
                    help="fingerprint length in characters (default: 12)")
    pd.add_argument("--min-occurrences", type=int, default=2,
                    help="minimum locations per cluster (default: 2)")
    pd.add_argument("--max-anchor-occurrences", type=int, default=200,
                    help="skip an anchor hash above this occurrence count (default: 200)")
    pd.add_argument("--partitions", type=int, default=256,
                    help="number of hash partitions (default: 256)")
    pd.add_argument("--min-pair-chars", type=int, default=100,
                    help="drop juan-pairs whose smaller side has fewer "
                         "duplicated characters than this (default: 100)")
    pd.add_argument("--format", choices=["tsv", "jsonl"], default="tsv",
                    help="report format (default: tsv)")
    pd.add_argument("--quiet", action="store_true",
                    help="suppress progress logging")

    pck = sub.add_parser(
        "check",
        help="report drift between source YAML/JSONL and the .bkki/.bkka indices",
    )
    pck.add_argument("--core-root", type=Path, default=None,
                     help="core root directory (or set core.root in .bkkrc)")
    pck.add_argument("--core-index", type=Path, default=None,
                     help="path to .bkki (defaults to core.index in .bkkrc)")
    pck.add_argument("--annotations-root", type=Path, default=None,
                     help="annotations archive root "
                          "(or set annotations.annotations_root / serve.annotations_root in .bkkrc)")
    pck.add_argument("--annotations-index", type=Path, default=None,
                     help="path to .bkka (defaults to <annotations_root>/_annotations.bkka)")

    ps = sub.add_parser("search", help="run a KWIC query against a .bkkx index")
    ps.add_argument("index_path", type=Path)
    ps.add_argument("query")
    ps.add_argument("--context", type=int, default=20)
    ps.add_argument("--witness", action="append", default=None,
                    help="restrict witness-side matches (repeatable); "
                         "master matches are always returned")
    ps.add_argument("--text-id", dest="text_id", default=None, type=text_id_arg,
                    help="restrict to one bundle (corpus indices)")
    ps.add_argument("--textid", dest="legacy_textid", default=None,
                    type=text_id_arg, help=argparse.SUPPRESS)
    ps.add_argument("--voice", action="append", default=None,
                    help="restrict to hits fully contained in a voice range "
                         "of this name (repeatable; e.g. 'root', 'commentary'). "
                         "Hits nested inside multiple ranges qualify under any "
                         "of their names. Omit to return all hits.")
    return p


def run(argv: list[str] | None = None) -> int:
    from bkk.config import load_rc
    rc = load_rc()
    g = rc.get("global", {})
    idx = rc.get("index", {})
    core_rc = rc.get("core", {})
    ann_rc = rc.get("annotations", {})
    serve_rc = rc.get("serve", {})

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd in ("merge", "catalog", "translations"):
        if args.corpus is None:
            args.corpus = idx.get("corpus") or g.get("corpus")
        if args.corpus is None:
            parser.error("corpus is required (or set global.corpus in .bkkrc)")
    if args.cmd == "merge":
        selected_prefixes = [
            bool(args.section), bool(args.prefix), bool(args.text_prefix),
        ]
        if sum(selected_prefixes) > 1:
            parser.error("--text-prefix, --section, and --prefix are mutually exclusive")
        default_out = Path(idx.get("out") or args.corpus / "_corpus.bkkx")
        if args.prefix:
            warn_deprecated("--prefix", "--text-prefix")
            args.text_prefix = text_prefix_arg(args.prefix)
        if args.section:
            warn_deprecated("--section", "--text-prefix")
            args.prefix = text_prefix_arg(args.section)
            if args.out is None:
                args.out = default_out.parent / f"_{args.section}.bkkx"
        else:
            args.prefix = args.text_prefix
        if args.out is None:
            args.out = default_out
    if args.cmd == "translations":
        if args.out is None:
            args.out = args.corpus / "_translations.bkkt"
    if args.cmd == "catalog":
        if args.prefix and args.text_prefix:
            parser.error("--text-prefix and --prefix are mutually exclusive")
        if args.prefix:
            warn_deprecated("--prefix", "--text-prefix")
            args.text_prefix = text_prefix_arg(args.prefix)
        args.prefix = args.text_prefix
        if args.out is None:
            args.out = args.corpus / "_catalog.bkkc"
        if args.csv_path is None:
            args.csv_path = default_catalog_csv()
        if args.csv_path is None:
            parser.error(
                "--csv is required (could not find catalog/frontmatter.csv "
                "from the current directory or its parents)"
            )
    if args.cmd == "core":
        if args.core_root is None:
            args.core_root = core_rc.get("root")
        if args.core_root is None:
            parser.error("core_root is required (or set core.root in .bkkrc)")
        if args.out is None:
            args.out = Path(core_rc.get("index") or args.core_root / "_core.bkki")
    if args.cmd == "annotations":
        if args.annotations_root is None:
            args.annotations_root = ann_rc.get("annotations_root") or serve_rc.get("annotations_root")
        if args.annotations_root is None:
            parser.error(
                "annotations_root is required "
                "(or set annotations.annotations_root / serve.annotations_root in .bkkrc)"
            )
        args.annotations_root = Path(args.annotations_root)
        if args.out is None:
            args.out = args.annotations_root / "_annotations.bkka"
        if args.core_index is None:
            rc_core_index = core_rc.get("index")
            if rc_core_index:
                args.core_index = Path(rc_core_index)
            else:
                rc_core_root = core_rc.get("root")
                if rc_core_root:
                    args.core_index = Path(rc_core_root) / "_core.bkki"
    if args.cmd == "check":
        if args.core_root is None:
            rc_core_root = core_rc.get("root")
            args.core_root = Path(rc_core_root) if rc_core_root else None
        if args.core_index is None:
            rc_core_index = core_rc.get("index")
            if rc_core_index:
                args.core_index = Path(rc_core_index)
            elif args.core_root is not None:
                args.core_index = args.core_root / "_core.bkki"
        if args.annotations_root is None:
            rc_ann = ann_rc.get("annotations_root") or serve_rc.get("annotations_root")
            args.annotations_root = Path(rc_ann) if rc_ann else None
        if args.annotations_index is None and args.annotations_root is not None:
            args.annotations_index = args.annotations_root / "_annotations.bkka"
    if args.cmd == "parallel" and args.index_path is None:
        if args.text_id is None:
            parser.error("index_path is required")
        corpus = idx.get("corpus") or g.get("corpus")
        configured_index = idx.get("out")
        if configured_index is not None:
            args.index_path = Path(configured_index)
        elif corpus is not None:
            args.index_path = Path(corpus) / "_corpus.bkkx"
        else:
            parser.error(
                "index is required (pass INDEX, set index.out, or configure "
                "index.corpus/global.corpus)"
            )

    if args.cmd == "build":
        if args.jobs < 1:
            parser.error("--jobs must be >= 1")
        path = build_index(args.bundle_dir, args.out, jobs=args.jobs)
        print(f"wrote {path}")
        return 0
    if args.cmd == "merge":
        if args.jobs < 1:
            parser.error("--jobs must be >= 1")
        path = merge_bundles(
            args.corpus, args.out,
            prefix=args.prefix, rebuild=args.rebuild, no_build=args.no_build,
            jobs=args.jobs, progress=True,
        )
        print(f"wrote {path}")
        return 0
    if args.cmd == "translations":
        path = merge_translations(
            args.corpus, args.out,
            rebuild=args.rebuild, no_build=args.no_build,
            progress=True,
        )
        print(f"wrote {path}")
        return 0
    if args.cmd == "annotations":
        path = build_annotation_index(
            args.annotations_root, args.out,
            core_index_path=args.core_index,
        )
        print(f"wrote {path}")
        return 0
    if args.cmd == "catalog":
        path = build_catalog_index(
            args.corpus, args.csv_path, args.out,
            prefix=args.prefix, csv_stub=args.csv_stub,
        )
        print(f"wrote {path}")
        if args.csv_stub:
            print(f"stubs → {args.csv_stub}")
        return 0
    if args.cmd == "core":
        path = build_core_index(args.core_root, args.out)
        print(f"wrote {path}")
        return 0
    if args.cmd == "parallel":
        if not args.index_path.is_file():
            parser.error(f"index does not exist: {args.index_path}")
        if args.text_id is not None:
            incompatible = []
            if args.seed is not None:
                incompatible.append("seed")
            if args.full_scan:
                incompatible.append("--full-scan")
            if args.force_full_scan:
                incompatible.append("--force-full-scan")
            if args.out is not None:
                incompatible.append("--out")
            if args.format is not None:
                incompatible.append("--format")
            if incompatible:
                parser.error(
                    "--text-id is incompatible with " + ", ".join(incompatible)
                )
            from .merge import find_bundle
            from .parallel_assets import (
                assert_index_unchanged,
                capture_index_snapshot,
                derive_index_name,
                validate_name,
                validate_textid,
                write_target_parallel_assets,
            )
            from bkk.short_refs import parse_text_juan_selector

            try:
                name = args.name or derive_index_name(args.index_path)
                validate_name(name)
                target_textid, target_juan_seq = parse_text_juan_selector(
                    args.text_id
                )
                validate_textid(target_textid)
            except ValueError as exc:
                parser.error(str(exc))
            corpus = idx.get("corpus") or g.get("corpus")
            try:
                bundle_dir = _bundle_for_parallel_target(
                    args.index_path,
                    target_textid,
                    juan_seq=target_juan_seq,
                    corpus=Path(corpus) if corpus is not None else None,
                    find_bundle=find_bundle,
                )
            except (FileNotFoundError, ValueError) as exc:
                parser.error(str(exc))
            scan_settings = {
                "text_id": target_textid,
                "juan": target_juan_seq,
                "bucket": args.bucket,
                "min_length": args.min_length,
                "min_occurrences": args.min_occurrences,
                "max_postings": args.max_postings,
                "max_edits": args.max_edits,
                "include_contained": args.include_contained,
            }
            snapshot = capture_index_snapshot(
                args.index_path,
                command="bkk index parallel",
                algorithm="targeted-trigram-v1",
                scan=scan_settings,
            )
            clusters = discover_parallel_passages(
                args.index_path,
                target_textid=target_textid,
                target_juan_seq=target_juan_seq,
                bucket=args.bucket,
                min_length=args.min_length,
                min_occurrences=args.min_occurrences,
                max_postings=args.max_postings,
                include_contained=args.include_contained,
                context=args.context,
                max_edits=args.max_edits,
            )
            assert_index_unchanged(snapshot)
            cluster_count, marker_count, file_count = (
                write_target_parallel_assets(
                    clusters,
                    bundle_dir,
                    textid=target_textid,
                    target_juan_seq=target_juan_seq,
                    name=name,
                    provenance=snapshot.provenance,
                )
            )
            print(
                f"clusters: {cluster_count:,}; directed markers: "
                f"{marker_count:,}; files: {file_count:,} "
                f"→ {bundle_dir / 'parallels'}"
            )
            return 0
        if args.seed is None and not args.full_scan:
            parser.error(
                "parallel now requires a 1-6 character seed term "
                "(or pass --full-scan for small indices)"
            )
        if args.seed is not None and args.full_scan:
            parser.error("parallel accepts either a seed term or --full-scan, not both")
        if args.full_scan and _is_corpus_index(args.index_path) and not args.force_full_scan:
            parser.error(
                "--full-scan is disabled for corpus indices; use parallel-scan "
                "or pass --force-full-scan if you really mean it"
            )
        clusters = discover_parallel_passages(
            args.index_path,
            seed=args.seed,
            bucket=args.bucket,
            min_length=args.min_length,
            min_occurrences=args.min_occurrences,
            max_postings=args.max_postings,
            include_contained=args.include_contained,
            context=args.context,
            max_edits=args.max_edits,
        )
        if args.out is None:
            import sys
            write_parallel_report(
                clusters, sys.stdout, format=args.format or "jsonl",
            )
        else:
            write_parallel_report(
                clusters, args.out, format=args.format or "jsonl",
            )
            print(f"wrote {args.out}")
        return 0
    if args.cmd == "parallel-scan":
        import sys
        clusters, _stats = discover_parallel_passages_scan(
            args.index_path,
            bucket=args.bucket,
            min_length=args.min_length,
            anchor_length=args.anchor_length,
            min_occurrences=args.min_occurrences,
            max_anchor_occurrences=args.max_anchor_occurrences,
            partitions=args.partitions,
            work_dir=args.work_dir,
            include_contained=args.include_contained,
            context=args.context,
            progress=None if args.quiet else sys.stderr,
        )
        if args.out is None:
            write_parallel_report(clusters, sys.stdout, format=args.format)
        else:
            write_parallel_report(clusters, args.out, format=args.format)
            print(f"wrote {args.out}")
        return 0
    if args.cmd == "duplications":
        import sys
        from .duplications import find_duplicated_juan, write_duplications_report
        rows = find_duplicated_juan(
            args.index_path,
            bucket=args.bucket,
            min_length=args.min_length,
            anchor_length=args.anchor_length,
            min_occurrences=args.min_occurrences,
            max_anchor_occurrences=args.max_anchor_occurrences,
            partitions=args.partitions,
            work_dir=args.work_dir,
            min_pair_chars=args.min_pair_chars,
            progress=None if args.quiet else sys.stderr,
        )
        if args.out is None:
            write_duplications_report(rows, sys.stdout, format=args.format)
        else:
            write_duplications_report(rows, args.out, format=args.format)
            print(f"wrote {args.out}")
        return 0
    if args.cmd == "check":
        from .drift import check_drift
        return check_drift(
            core_root=args.core_root,
            core_index=args.core_index,
            annotations_root=args.annotations_root,
            annotations_index=args.annotations_index,
        )
    if args.cmd == "search":
        if args.text_id and args.legacy_textid:
            parser.error("provide only one of --text-id or --textid")
        if args.legacy_textid:
            warn_deprecated("--textid", "--text-id")
            args.text_id = args.legacy_textid
        with Index(args.index_path) as ix:
            wits = set(args.witness) if args.witness else None
            voices = set(args.voice) if args.voice else None
            if voices is not None:
                available = set(ix.available_voices())
                unknown = voices - available
                if unknown:
                    parser.error(
                        f"unknown voice name(s) {sorted(unknown)!r}; "
                        f"available in this index: {sorted(available)!r}"
                    )
            hits = ix.search(
                args.query, context=args.context,
                witnesses=wits, textid=args.text_id, voices=voices,
            )
            for hit in hits:
                _print_hit(hit)
        return 0
    return 2


def _is_corpus_index(path: Path) -> bool:
    import sqlite3
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'kind'"
            ).fetchone()
            return bool(row and row[0] == "corpus")
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return False


def _bundle_for_parallel_target(
    index_path: Path,
    text_id: str,
    *,
    juan_seq: int | None,
    corpus: Path | None,
    find_bundle,
) -> Path:
    """Resolve the writable bundle represented by ``text_id``."""
    import sqlite3

    conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
    try:
        if juan_seq is None:
            exists = conn.execute(
                "SELECT 1 FROM juan WHERE textid = ? LIMIT 1",
                (text_id,),
            ).fetchone()
        else:
            exists = conn.execute(
                "SELECT 1 FROM juan WHERE textid = ? AND seq = ? LIMIT 1",
                (text_id, juan_seq),
            ).fetchone()
        if exists is None:
            target = text_id if juan_seq is None else f"{text_id}/{juan_seq}"
            raise ValueError(f"target {target!r} is not present in the index")
        row = conn.execute(
            "SELECT source_path FROM bundle WHERE textid = ?",
            (text_id,),
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"cannot inspect index {index_path}: {exc}") from exc
    finally:
        conn.close()

    candidates: list[Path] = []
    if row is not None:
        candidates.append(Path(row[0]).expanduser().resolve().parent)
    candidates.append(index_path.resolve().parent)
    for candidate in candidates:
        if (candidate / f"{text_id}.manifest.yaml").is_file():
            return candidate
    if corpus is not None:
        found = find_bundle(corpus, text_id)
        if found is not None:
            return found
    raise FileNotFoundError(
        f"cannot locate bundle for {text_id!r}; configure index.corpus or "
        "global.corpus"
    )


def _print_hit(h: Hit) -> None:
    label = f"{h.textid}:{h.juan_seq:03d}/{h.bucket}@{h.master_offset}"
    if h.toc_label:
        label += f"  [{h.toc_label}]"
    if h.matched_via != "master":
        label += f"  via {h.matched_via}={h.matched_text!r}"
    label += f"  ({'>'.join(h.voice_stack) if h.voice_stack else h.voice})"
    print(label)
    print(f"  …{h.left}「{h.match}」{h.right}…")
    for o in h.overlays:
        in_match = h.master_offset <= o.master_offset < h.master_offset + h.master_length
        flag = "*" if in_match else " "
        print(
            f"  {flag} variant @{o.master_offset} len={o.length} "
            f"{o.content!r} → {o.witness}={o.witness_form!r}"
        )


def main() -> None:
    raise SystemExit(run())
