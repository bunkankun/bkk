"""Command-line interface for the BKK index.

Subcommands::

    python -m bkk.index build <bundle_dir> [--out PATH]
    python -m bkk.index merge <corpus> [--out PATH] [--prefix KR3a]
                                       [--rebuild | --no-build]
    python -m bkk.index search <bkkx_path> <query> [--context N]
                                                   [--witness LABEL]...
                                                   [--textid ID]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .build import build_index
from .ir import Hit
from .merge import merge_bundles
from .query import Index


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

    ps = sub.add_parser("search", help="run a KWIC query against a .bkkx index")
    ps.add_argument("index_path", type=Path)
    ps.add_argument("query")
    ps.add_argument("--context", type=int, default=20)
    ps.add_argument("--witness", action="append", default=None,
                    help="restrict witness-side matches (repeatable); "
                         "master matches are always returned")
    ps.add_argument("--textid", default=None,
                    help="restrict to one bundle (corpus indices)")
    return p


def run(argv: list[str] | None = None) -> int:
    from bkk.config import load_rc
    rc = load_rc()
    g = rc.get("global", {})
    idx = rc.get("index", {})

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "merge":
        if args.corpus is None:
            args.corpus = idx.get("corpus") or g.get("corpus")
        if args.corpus is None:
            parser.error("corpus is required (or set global.corpus in .bkkrc)")
        if args.out is None:
            args.out = Path(idx.get("out") or args.corpus / "_corpus.bkkx")

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
    if args.cmd == "search":
        with Index(args.index_path) as ix:
            wits = set(args.witness) if args.witness else None
            hits = ix.search(
                args.query, context=args.context,
                witnesses=wits, textid=args.textid,
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
