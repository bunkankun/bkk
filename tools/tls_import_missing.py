#!/usr/bin/env python3
"""
List TLS source XML files that didn't produce a bundle in the output tree.

A successful TLS import writes ``<out>/<text-id>/<text-id>.manifest.yaml``.
This script walks the TLS source layout (``<in>/tls-texts/data/`` recursively),
and for each ``<text-id>.xml`` it finds (excluding ``*-ann.xml`` sidecars),
checks whether the corresponding manifest exists. Anything missing is
printed to stdout, one source path per line.

Usage:
    python3 tools/tls_import_missing.py \\
        --in  ~/Dropbox/current/hxwd \\
        --out /home/Shared/bkk/bkbooks

python3 tools/tls_import_missing.py --in ~/Dropbox/current/hxwd --out /home/Shared/bkk/bkbooks \
  | cut -f1 \
  | xargs -I{} python -m bkk.importer --format tls --in ~/Dropbox/current/hxwd --out /home/Shared/bkk/bkbooks --text-id {}
        
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--in", dest="in_root", type=Path, required=True,
                   help="TLS source root (parent of tls-texts/)")
    p.add_argument("--out", dest="out_root", type=Path, required=True,
                   help="bundle output root passed to the importer")
    p.add_argument("--check", choices=["dir", "manifest"], default="manifest",
                   help="dir: report ids with no <out>/<id>/ dir at all; "
                        "manifest (default): also report ids whose dir "
                        "exists but lacks <id>.manifest.yaml")
    args = p.parse_args()

    base = args.in_root / "tls-texts" / "data"
    if not base.is_dir():
        print(f"error: {base} does not exist", file=sys.stderr)
        return 2
    if not args.out_root.is_dir():
        print(f"error: {args.out_root} does not exist", file=sys.stderr)
        return 2

    sources: dict[str, Path] = {}
    for path in base.rglob("*.xml"):
        stem = path.stem
        if stem.endswith("-ann"):
            continue
        sources.setdefault(stem, path)

    missing: list[tuple[str, Path]] = []
    for text_id, src in sorted(sources.items()):
        bundle_dir = args.out_root / text_id
        if args.check == "dir":
            ok = bundle_dir.is_dir()
        else:
            ok = (bundle_dir / f"{text_id}.manifest.yaml").is_file()
        if not ok:
            missing.append((text_id, src))

    for text_id, src in missing:
        print(f"{text_id}\t{src}")

    print(
        f"\n{len(missing)} of {len(sources)} source text(s) missing "
        f"from {args.out_root}",
        file=sys.stderr,
    )
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
