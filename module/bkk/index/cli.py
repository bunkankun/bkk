"""Command-line interface for the BKK index.

Subcommands::

    python -m bkk.index build <bundle_dir> [--out PATH]
    python -m bkk.index catalog <corpus> [--csv PATH] [--out PATH]
                                         [--prefix KR3a]
    python -m bkk.index translations <corpus> [--out PATH]
    python -m bkk.index annotations [annotations_root] [--out PATH]
    python -m bkk.index merge <corpus> [--out PATH] [--prefix KR3a]
                                       [--rebuild | --no-build]
    python -m bkk.index core <core_root> [--out PATH]
    python -m bkk.index search <bkkx_path> <query> [--context N]
                                                   [--witness LABEL]...
                                                   [--textid ID]
                                                   [--voice NAME]...
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .annotations import build_annotation_index
from .build import build_index
from .catalog import build_catalog_index, default_catalog_csv
from .core import build_core_index
from .ir import Hit
from .merge import merge_bundles
from .query import Index
from .translation import build_translation_index, merge_translations


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bkk.index")
    sub = p.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("build", help="build a .bkkx index from a bundle directory")
    pb.add_argument("bundle_dir", type=Path)
    pb.add_argument("--out", type=Path, default=None,
                    help="output path (default: <bundle_dir>/<textid>.bkkx)")

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
    grp = pm.add_mutually_exclusive_group()
    grp.add_argument("--rebuild", action="store_true",
                     help="rebuild every per-bundle .bkkx, ignoring mtimes")
    grp.add_argument("--no-build", action="store_true",
                     help="error if any per-bundle .bkkx is missing or stale")

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

    pco = sub.add_parser("core", help="build a .bkki index over the bkk-core knowledge layer")
    pco.add_argument("core_root", type=Path, nargs="?", default=None,
                     help="core root directory (or set core.root in .bkkrc)")
    pco.add_argument("--out", type=Path, default=None,
                     help="core .bkki output path "
                          "(default: core.index from .bkkrc, "
                          "else <core_root>/_core.bkki)")

    ps = sub.add_parser("search", help="run a KWIC query against a .bkkx index")
    ps.add_argument("index_path", type=Path)
    ps.add_argument("query")
    ps.add_argument("--context", type=int, default=20)
    ps.add_argument("--witness", action="append", default=None,
                    help="restrict witness-side matches (repeatable); "
                         "master matches are always returned")
    ps.add_argument("--textid", default=None,
                    help="restrict to one bundle (corpus indices)")
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
        if args.out is None:
            args.out = Path(idx.get("out") or args.corpus / "_corpus.bkkx")
    if args.cmd == "translations":
        if args.out is None:
            args.out = args.corpus / "_translations.bkkt"
    if args.cmd == "catalog":
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

    if args.cmd == "build":
        path = build_index(args.bundle_dir, args.out)
        print(f"wrote {path}")
        return 0
    if args.cmd == "merge":
        path = merge_bundles(
            args.corpus, args.out,
            prefix=args.prefix, rebuild=args.rebuild, no_build=args.no_build,
            progress=True,
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
        path = build_annotation_index(args.annotations_root, args.out)
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
    if args.cmd == "search":
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
                witnesses=wits, textid=args.textid, voices=voices,
            )
            for hit in hits:
                _print_hit(hit)
        return 0
    return 2


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
