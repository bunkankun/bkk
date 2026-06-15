#!/usr/bin/env python3
"""
Find variant *pairs* where BOTH members appear in the corpus.

For each line in data/Unihan_Variants.txt, every (column-1, target-cp) pair
defines a variant relationship. We collect those as unordered pairs
{a, b}, dedup, then keep only the pairs whose two members both appear in
tools/survey-out/codepoints.tsv. For each surviving pair we report the
two corpus counts, the variant types that produced the pair, and the ratio.

Sorted by min(count_a, count_b) descending — pairs where both members are
common are the most consequential canonicalization decisions.

Outputs (under tools/survey-out/):
  - variant_pairs.tsv  : long detail, one row per pair both in corpus
  - variant_pairs.txt  : top-of-list summary + per-type counts
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
DETAIL = OUT_DIR / "variant_pairs.tsv"
SUMMARY = OUT_DIR / "variant_pairs.txt"

CP_RE = re.compile(r"U\+([0-9A-Fa-f]+)")


def load_corpus_counts() -> dict[int, tuple[int, str]]:
    """codepoint -> (count, char)."""
    out: dict[int, tuple[int, str]] = {}
    with CODEPOINTS_TSV.open(encoding="utf-8") as f:
        next(f)
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            cp_hex, char, count = parts[0], parts[1], parts[2]
            cp = int(cp_hex[2:], 16)
            out[cp] = (int(count), char)
    return out


def parse_pairs() -> dict[frozenset[int], set[str]]:
    """
    Return { frozenset({a, b}) -> set of variant_types } over all rows.
    Self-loops (a == b) are skipped. Annotation tags (e.g. '<kMatthews')
    are ignored — only the U+XXXX tokens are extracted.
    """
    pairs: dict[frozenset[int], set[str]] = defaultdict(set)
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
            a = int(m.group(1), 16)
            for tm in CP_RE.finditer(value):
                b = int(tm.group(1), 16)
                if a == b:
                    continue
                pairs[frozenset({a, b})].add(vtype)
    return pairs


def main() -> int:
    if not CODEPOINTS_TSV.exists():
        print(f"missing: {CODEPOINTS_TSV}", file=sys.stderr)
        return 2
    if not UNIHAN.exists():
        print(f"missing: {UNIHAN}", file=sys.stderr)
        return 2

    corpus = load_corpus_counts()
    pairs = parse_pairs()

    # Filter to pairs where both members are in the corpus.
    kept: list[tuple[int, int, int, int, str, str, frozenset[int], set[str]]] = []
    for pair, vtypes in pairs.items():
        a, b = sorted(pair)
        if a not in corpus or b not in corpus:
            continue
        ca, cha = corpus[a]
        cb, chb = corpus[b]
        # Order display so the more-frequent member is on the left.
        if cb > ca:
            a, b = b, a
            ca, cb = cb, ca
            cha, chb = chb, cha
        kept.append((ca, cb, a, b, cha, chb, pair, vtypes))

    # Sort by lower count descending — pairs where the rarer member is most
    # populated are the highest-impact canonicalization decisions.
    kept.sort(key=lambda r: (-r[1], -r[0]))

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with DETAIL.open("w", encoding="utf-8") as f:
        f.write("cp_major\tchar_major\tcount_major"
                "\tcp_minor\tchar_minor\tcount_minor"
                "\tratio_major_over_minor\ttypes\n")
        for ca, cb, a, b, cha, chb, _pair, vtypes in kept:
            ratio = ca / cb if cb else float("inf")
            f.write(
                f"U+{a:04X}\t{cha}\t{ca}"
                f"\tU+{b:04X}\t{chb}\t{cb}"
                f"\t{ratio:.3f}\t{','.join(sorted(vtypes))}\n"
            )

    # Per-type counts.
    by_type_pairs: dict[str, int] = defaultdict(int)
    by_type_total: dict[str, int] = defaultdict(int)
    for ca, cb, a, b, cha, chb, _pair, vtypes in kept:
        for vt in vtypes:
            by_type_pairs[vt] += 1
            by_type_total[vt] += ca + cb

    union_total_corpus = sum(corpus[cp][0] for cp in corpus)
    pair_total = sum(ca + cb for ca, cb, *_ in kept)
    distinct_cps_in_pairs = {cp for _, _, a, b, *_ in kept for cp in (a, b)}

    with SUMMARY.open("w", encoding="utf-8") as f:
        f.write("Variant pairs where BOTH members appear in the corpus\n")
        f.write("=====================================================\n\n")
        f.write(f"Unihan_Variants.txt distinct unordered pairs: {len(pairs)}\n")
        f.write(f"  ... of which both members are in our corpus: {len(kept)}\n")
        f.write(f"distinct corpus codepoints involved in those pairs: "
                f"{len(distinct_cps_in_pairs)}\n")
        f.write(f"sum of corpus occurrences across both members of all "
                f"in-corpus pairs: {pair_total}\n")
        f.write(f"  (note: codepoints in multiple pairs are counted "
                f"multiple times)\n")
        f.write(f"corpus total codepoints (denominator): "
                f"{union_total_corpus}\n\n")

        f.write("Per variant_type (a pair may carry several types):\n")
        f.write(f"  {'type':<32}{'pairs':>10}{'sum_counts':>16}\n")
        for vt in sorted(by_type_pairs):
            f.write(
                f"  {vt:<32}{by_type_pairs[vt]:>10}{by_type_total[vt]:>16}\n"
            )

        f.write("\n--- top 30 in-corpus variant pairs by count_minor desc ---\n")
        f.write(
            f"  {'major':<10}{'count_major':>14}  "
            f"{'minor':<10}{'count_minor':>14}  "
            f"{'ratio':>8}  types\n"
        )
        for ca, cb, a, b, cha, chb, _pair, vtypes in kept[:30]:
            ratio = ca / cb if cb else float("inf")
            f.write(
                f"  U+{a:04X} {cha}  {ca:>12}    "
                f"U+{b:04X} {chb}  {cb:>12}  "
                f"{ratio:>8.2f}  {','.join(sorted(vtypes))}\n"
            )

    print(f"wrote {SUMMARY}")
    print(f"wrote {DETAIL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
