#!/usr/bin/env python3
"""Emit the pending Z/Spoofing codepoint list.

Lists every codepoint that has a ``kZVariant`` or ``kSpoofingVariant``
relationship in ``data/Unihan_Variants.txt`` and is NOT already a source
or replacement in ``module/refs/bkk-mapping-variant-fold-v1.yaml``.

The intent is to flag these codepoints when they appear in a future text:
each one represents a fold decision the bootstrap couldn't make (because
one or both members of the pair were absent from the corpus survey, or
because the pair carried Semantic/Simplified/Traditional types that the
strict bootstrap filter dropped).

Output: ``tools/survey-out/variant_pending.tsv``, one row per
``(pending_cp, variant_type, partner_cp)`` triple, with corpus-presence
flags for both members.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

try:
    from yaml import CSafeLoader as SafeLoader
except ImportError:
    from yaml import SafeLoader

ROOT = Path(__file__).resolve().parent.parent
UNIHAN = ROOT / "data/Unihan_Variants.txt"
CODEPOINTS_TSV = ROOT / "tools/survey-out/codepoints.tsv"
MAPPING_YAML = ROOT / "module/refs/bkk-mapping-variant-fold-v1.yaml"
OUT_TSV = ROOT / "tools/survey-out/variant_pending.tsv"

CP_RE = re.compile(r"U\+([0-9A-Fa-f]+)")
KEEP_TYPES = {"kZVariant", "kSpoofingVariant"}


def cp_hex(cp: int) -> str:
    return f"U+{cp:04X}"


def parse_unihan_pairs() -> dict[frozenset[int], set[str]]:
    """{frozenset({a, b}) -> {variant_type, ...}} restricted to Z/Spoofing."""
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
            if vtype not in KEEP_TYPES:
                continue
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


def load_corpus() -> dict[int, str]:
    """codepoint -> char (presence indicator + display)."""
    out: dict[int, str] = {}
    with CODEPOINTS_TSV.open(encoding="utf-8") as f:
        next(f)
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            cp = int(parts[0][2:], 16)
            out[cp] = parts[1]
    return out


def load_folded_cps() -> set[int]:
    """All cps that appear as source or replacement in the variant fold."""
    doc = yaml.load(MAPPING_YAML.read_bytes(), Loader=SafeLoader) or {}
    cps: set[int] = set()
    for e in doc.get("entries", []):
        for key in ("source", "replacement"):
            cp_field = (e.get(key) or {}).get("cp")
            if isinstance(cp_field, str):
                m = CP_RE.match(cp_field)
                if m:
                    cps.add(int(m.group(1), 16))
    return cps


def char_for(cp: int, corpus: dict[int, str]) -> str:
    """Best-effort glyph for display. Uses corpus char if known; else chr(cp)."""
    if cp in corpus and corpus[cp]:
        return corpus[cp]
    try:
        return chr(cp)
    except ValueError:
        return ""


def main() -> int:
    for path in (UNIHAN, CODEPOINTS_TSV, MAPPING_YAML):
        if not path.exists():
            print(f"missing: {path}", file=sys.stderr)
            return 2

    pairs = parse_unihan_pairs()
    corpus = load_corpus()
    folded = load_folded_cps()

    rows: list[tuple[int, str, int]] = []
    for pair, vtypes in pairs.items():
        a, b = sorted(pair)
        for cp, partner in ((a, b), (b, a)):
            if cp in folded:
                continue
            for vt in vtypes:
                rows.append((cp, vt, partner))

    rows.sort(key=lambda r: (r[0], r[1], r[2]))

    OUT_TSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_TSV.open("w", encoding="utf-8") as f:
        f.write(
            "cp\tchar\tin_corpus\ttype"
            "\tpartner_cp\tpartner_char\tpartner_in_corpus\tpartner_in_fold\n"
        )
        for cp, vt, partner in rows:
            f.write(
                f"{cp_hex(cp)}\t{char_for(cp, corpus)}"
                f"\t{'yes' if cp in corpus else 'no'}"
                f"\t{vt}"
                f"\t{cp_hex(partner)}\t{char_for(partner, corpus)}"
                f"\t{'yes' if partner in corpus else 'no'}"
                f"\t{'yes' if partner in folded else 'no'}\n"
            )

    distinct = len({cp for cp, _, _ in rows})
    in_corpus_pending = sum(1 for cp in {cp for cp, _, _ in rows} if cp in corpus)
    print(f"wrote {OUT_TSV}")
    print(f"  unihan Z/Spoofing pairs:           {len(pairs)}")
    print(f"  folded cps (already covered):      {len(folded)}")
    print(f"  pending triples:                   {len(rows)}")
    print(f"  distinct pending cps:              {distinct}")
    print(f"    ... of which in corpus:          {in_corpus_pending}")
    print(f"    ... of which absent from corpus: {distinct - in_corpus_pending}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
