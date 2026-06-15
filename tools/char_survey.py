#!/usr/bin/env python3
"""
Character audit across bundles.

Walks `module/output/<bundle>/<bundle>_NNN.yaml` master juan files,
counts codepoints in `front.text` + `body.text` with no normalization,
and emits one CSV per bundle plus a corpus-wide summary.

Schema (both per-bundle and summary): cp_hex,char,count

Usage:
    python3 tools/char_survey.py [--root DIR] [--out-dir DIR]
                                 [--summary FILE] [--limit N]
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from collections import Counter
from pathlib import Path

import yaml

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeLoader

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = REPO_ROOT / "module" / "output"
DEFAULT_OUT_DIR = REPO_ROOT / "wip" / "chars"
DEFAULT_SUMMARY = REPO_ROOT / "wip" / "chars-summary.csv"

BUNDLE_RE = re.compile(r"^KR[0-9a-z]+$")


def iter_bundles(root: Path):
    seen: set[str] = set()
    for child in sorted(root.rglob("*")):
        if not child.is_dir():
            continue
        if not BUNDLE_RE.match(child.name):
            continue
        if child.name in seen:
            continue
        juans = sorted(child.glob(f"{child.name}_*.yaml"))
        if juans:
            seen.add(child.name)
            yield child.name, juans


def count_bundle(paths: list[Path]) -> Counter[int]:
    counter: Counter[int] = Counter()
    for path in paths:
        with path.open("rb") as f:
            doc = yaml.load(f, Loader=SafeLoader) or {}
        for key in ("front", "body"):
            section = doc.get(key) or {}
            text = section.get("text") or ""
            counter.update(ord(ch) for ch in text)
    return counter


def write_csv(path: Path, counter: Counter[int]) -> None:
    rows = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cp_hex", "char", "count"])
        for cp, count in rows:
            ch = chr(cp)
            display = ch if ch.isprintable() else ""
            w.writerow([f"U+{cp:04X}", display, count])


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    p.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    p.add_argument("--limit", type=int, default=None,
                   help="Process at most N bundles (smoke-test).")
    args = p.parse_args()

    if not args.root.exists():
        print(f"root does not exist: {args.root}", file=sys.stderr)
        return 2

    args.out_dir.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)

    summary: Counter[int] = Counter()
    bundle_count = 0
    t0 = time.time()
    for bundle_id, juans in iter_bundles(args.root):
        if args.limit is not None and bundle_count >= args.limit:
            break
        bundle_count += 1
        counter = count_bundle(juans)
        write_csv(args.out_dir / f"{bundle_id}.csv", counter)
        summary.update(counter)

    write_csv(args.summary, summary)
    elapsed = time.time() - t0

    print(f"processed {bundle_count} bundles in {elapsed:.1f}s")
    print(f"per-bundle CSVs: {args.out_dir}")
    print(f"summary: {args.summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
