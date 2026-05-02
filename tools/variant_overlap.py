#!/usr/bin/env python3
"""
Cross-reference corpus codepoints (tools/survey-out/codepoints.tsv) against
data/Unihan_Variants.txt.

For every codepoint that appears in *column 1* of Unihan_Variants.txt
(i.e. "this codepoint has a variant of the named kind"), count how many of
our corpus codepoints match, and sum their corpus occurrences. Repeat for
*column 3* (i.e. "this codepoint is named as someone else's variant").

Output:
  - a per-variant-type summary table (variant_type, side, distinct, total)
  - a long TSV listing the matched corpus codepoints with their counts
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODEPOINTS_TSV = ROOT / "tools/survey-out/codepoints.tsv"
UNIHAN = ROOT / "data/Unihan_Variants.txt"
OUT_DIR = ROOT / "tools/survey-out"
SUMMARY = OUT_DIR / "variant_overlap.txt"
DETAIL = OUT_DIR / "variant_overlap.tsv"


CP_RE = re.compile(r"U\+([0-9A-Fa-f]+)")


def load_corpus_counts() -> dict[int, tuple[int, str, str]]:
    """codepoint -> (count, char, name) from the survey output."""
    out: dict[int, tuple[int, str, str]] = {}
    with CODEPOINTS_TSV.open(encoding="utf-8") as f:
        next(f)  # header
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 6:
                continue
            cp_hex, char, count, _block, _cat, name = parts[:6]
            cp = int(cp_hex[2:], 16)
            out[cp] = (int(count), char, name)
    return out


def parse_unihan() -> tuple[
    dict[str, set[int]],  # source side: variant_type -> set of column-1 cps
    dict[str, set[int]],  # target side: variant_type -> set of column-3 cps
]:
    src: dict[str, set[int]] = defaultdict(set)
    tgt: dict[str, set[int]] = defaultdict(set)
    with UNIHAN.open(encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            cp_field, vtype, value = parts[0], parts[1], parts[2]
            m = CP_RE.match(cp_field)
            if not m:
                continue
            src_cp = int(m.group(1), 16)
            src[vtype].add(src_cp)
            for tm in CP_RE.finditer(value):
                tgt[vtype].add(int(tm.group(1), 16))
    return src, tgt


def main() -> int:
    if not CODEPOINTS_TSV.exists():
        print(f"missing: {CODEPOINTS_TSV}", file=sys.stderr)
        return 2
    if not UNIHAN.exists():
        print(f"missing: {UNIHAN}", file=sys.stderr)
        return 2

    corpus = load_corpus_counts()
    src, tgt = parse_unihan()

    # All variant types that appeared on either side, in a stable order.
    vtypes = sorted(set(src) | set(tgt))

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Detail TSV.
    with DETAIL.open("w", encoding="utf-8") as f:
        f.write("variant_type\tside\tcp_hex\tchar\tcount\tname\n")
        for vt in vtypes:
            for side, table in (("source", src.get(vt, set())),
                                ("target", tgt.get(vt, set()))):
                rows = []
                for cp in table:
                    if cp in corpus:
                        count, char, name = corpus[cp]
                        rows.append((count, cp, char, name))
                rows.sort(reverse=True)
                for count, cp, char, name in rows:
                    display = char if char else ""
                    f.write(
                        f"{vt}\t{side}\tU+{cp:04X}\t{display}\t{count}\t{name}\n"
                    )

    # Summary.
    union_src: set[int] = set()
    union_tgt: set[int] = set()
    for vt in vtypes:
        union_src |= src.get(vt, set())
        union_tgt |= tgt.get(vt, set())
    union_any = union_src | union_tgt

    def stats(s: set[int]) -> tuple[int, int]:
        distinct = sum(1 for cp in s if cp in corpus)
        total = sum(corpus[cp][0] for cp in s if cp in corpus)
        return distinct, total

    with SUMMARY.open("w", encoding="utf-8") as f:
        f.write("Corpus / Unihan_Variants.txt overlap\n")
        f.write("====================================\n\n")
        f.write(f"corpus distinct codepoints: {len(corpus)}\n")
        f.write(f"corpus total codepoints:    {sum(c for c, _, _ in corpus.values())}\n\n")
        f.write(
            "Sides:\n"
            "  source = corpus codepoints that appear in column 1 of\n"
            "           Unihan_Variants.txt (i.e. they have a variant defined).\n"
            "  target = corpus codepoints that appear in column 3 (i.e. some\n"
            "           other character names them as its variant).\n\n"
        )
        f.write(
            f"{'variant_type':<32}{'side':<8}"
            f"{'distinct':>10}{'corpus_total':>16}"
            f"{'unihan_size':>14}\n"
        )
        for vt in vtypes:
            for side_name, s in (("source", src.get(vt, set())),
                                 ("target", tgt.get(vt, set()))):
                distinct, total = stats(s)
                f.write(
                    f"{vt:<32}{side_name:<8}"
                    f"{distinct:>10}{total:>16}{len(s):>14}\n"
                )

        f.write("\n")
        f.write(f"{'union (any type, source side)':<40}")
        d, t = stats(union_src)
        f.write(f"distinct={d:<8} corpus_total={t:<14} unihan_size={len(union_src)}\n")

        f.write(f"{'union (any type, target side)':<40}")
        d, t = stats(union_tgt)
        f.write(f"distinct={d:<8} corpus_total={t:<14} unihan_size={len(union_tgt)}\n")

        f.write(f"{'union (any type, either side)':<40}")
        d, t = stats(union_any)
        f.write(f"distinct={d:<8} corpus_total={t:<14} unihan_size={len(union_any)}\n")

    print(f"wrote {SUMMARY}")
    print(f"wrote {DETAIL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
