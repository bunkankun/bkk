#!/usr/bin/env python3
"""Bootstrap the canonical character set and substitution mapping.

Reads ``tools/survey-out/variant_pairs.tsv``, keeps pairs whose ``types``
column is exactly ``kZVariant`` or ``kSpoofingVariant`` (no Semantic /
Simplified / Traditional overlap), folds each connected component to its
highest-count member, and emits:

  - module/refs/bkk-charset-cjk-v1.yaml       canonical character set
  - module/refs/bkk-mapping-variant-fold-v1.yaml   substitution mapping

The representative of each component stays in the canonical set; every
other member is excluded and gets a mapping entry pointing at the rep.

Output is deterministic — re-running the script produces byte-identical
files (and therefore the same self-referential hashes).
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAIRS_TSV = ROOT / "tools/survey-out/variant_pairs.tsv"
OUT_DIR = ROOT / "module/refs"
CHARSET_YAML = OUT_DIR / "bkk-charset-cjk-v1.yaml"
MAPPING_YAML = OUT_DIR / "bkk-mapping-variant-fold-v1.yaml"

sys.path.insert(0, str(ROOT / "module"))
from bkk.importer.hashing import ZERO_HASH, sha256_jcs  # noqa: E402
from bkk.importer.write.yaml_writer import dump as yaml_dump, marker_to_flow  # noqa: E402

KEEP_TYPES = {"kZVariant", "kSpoofingVariant"}

# Inclusion blocks for bkk:charset/cjk-v1. Mirrors the predicate in
# module/bkk/importer/charset.py and the PUA range in module/bkk/importer/pua.py.
INCLUSION_BLOCKS: list[tuple[str, int, int]] = [
    ("CJK Unified Ideographs",         0x4E00,   0x9FFF),
    ("CJK Unified Ideographs Ext A",   0x3400,   0x4DBF),
    ("CJK Unified Ideographs Ext B",   0x20000,  0x2A6DF),
    ("CJK Unified Ideographs Ext C-F", 0x2A700,  0x2EBEF),
    ("CJK Compatibility Ideographs",   0xF900,   0xFAFF),
    ("BKK PUA",                        0x105000, 0x105FFF),
]


def cp_hex(cp: int) -> str:
    return f"U+{cp:04X}"


def read_filtered_pairs() -> list[dict]:
    rows: list[dict] = []
    with PAIRS_TSV.open(encoding="utf-8") as f:
        next(f)  # header
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 8:
                continue
            types = parts[7]
            if types not in KEEP_TYPES:
                continue
            rows.append({
                "cp_major": int(parts[0][2:], 16),
                "char_major": parts[1],
                "count_major": int(parts[2]),
                "cp_minor": int(parts[3][2:], 16),
                "char_minor": parts[4],
                "count_minor": int(parts[5]),
                "reason": types,
            })
    return rows


def codepoint_info(rows: list[dict]) -> dict[int, dict]:
    info: dict[int, dict] = {}
    for r in rows:
        info.setdefault(r["cp_major"], {"char": r["char_major"], "count": r["count_major"]})
        info.setdefault(r["cp_minor"], {"char": r["char_minor"], "count": r["count_minor"]})
    return info


def connected_components(rows: list[dict]) -> list[set[int]]:
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for r in rows:
        union(r["cp_major"], r["cp_minor"])

    comps: dict[int, set[int]] = defaultdict(set)
    for r in rows:
        root = find(r["cp_major"])
        comps[root].add(r["cp_major"])
        comps[root].add(r["cp_minor"])
    return list(comps.values())


def reason_for(cp: int, rows: list[dict]) -> str:
    rs = {r["reason"] for r in rows if r["cp_minor"] == cp or r["cp_major"] == cp}
    return "kZVariant" if "kZVariant" in rs else "kSpoofingVariant"


def build_charset_doc(
    exclusions: list[tuple[int, int]],
    info: dict[int, dict],
    rows: list[dict],
) -> dict:
    return {
        "canonical_identifier": "bkk:charset/cjk-v1",
        "description": "Bootstrap canonical character set for BKK CJK text.",
        "inclusion_blocks": [
            marker_to_flow({"name": name, "range": [cp_hex(lo), cp_hex(hi)]})
            for name, lo, hi in INCLUSION_BLOCKS
        ],
        "excluded": [
            marker_to_flow({
                "cp": cp_hex(cp),
                "char": info[cp]["char"],
                "reason": reason_for(cp, rows),
                "replaced_by": cp_hex(rep),
            })
            for cp, rep in exclusions
        ],
        "hash": ZERO_HASH,
    }


def build_mapping_doc(
    exclusions: list[tuple[int, int]],
    info: dict[int, dict],
    rows: list[dict],
) -> dict:
    entries = []
    for i, (cp, rep) in enumerate(exclusions, start=1):
        entries.append({
            "id": f"vf-{i:04d}",
            "source":      marker_to_flow({"cp": cp_hex(cp),  "char": info[cp]["char"]}),
            "replacement": marker_to_flow({"cp": cp_hex(rep), "char": info[rep]["char"]}),
            "reason": reason_for(cp, rows),
            "note": (
                f"Unihan_Variants.txt; corpus counts "
                f"{info[cp]['count']} -> {info[rep]['count']}"
            ),
        })
    return {
        "canonical_identifier": "bkk:mapping/variant-fold-v1",
        "valid_for_charset": ["bkk:charset/cjk-v1"],
        "scope": (
            "Folds Unihan kZVariant and kSpoofingVariant pairs whose only "
            "variant relationship is Z/Spoofing (no Semantic/Simplified/"
            "Traditional overlap)."
        ),
        "entries": entries,
        "hash": ZERO_HASH,
    }


def finalize(doc: dict) -> str:
    doc["hash"] = sha256_jcs({**doc, "hash": ZERO_HASH})
    return yaml_dump(doc)


def main() -> int:
    if not PAIRS_TSV.exists():
        print(f"missing: {PAIRS_TSV}", file=sys.stderr)
        return 2

    rows = read_filtered_pairs()
    info = codepoint_info(rows)
    comps = connected_components(rows)

    exclusions: list[tuple[int, int]] = []
    for comp in comps:
        ranked = sorted(comp, key=lambda cp: (-info[cp]["count"], cp))
        rep = ranked[0]
        for cp in ranked[1:]:
            exclusions.append((cp, rep))
    exclusions.sort(key=lambda x: x[0])

    charset_doc = build_charset_doc(exclusions, info, rows)
    mapping_doc = build_mapping_doc(exclusions, info, rows)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CHARSET_YAML.write_text(finalize(charset_doc), encoding="utf-8")
    MAPPING_YAML.write_text(finalize(mapping_doc), encoding="utf-8")

    minor_total = sum(info[cp]["count"] for cp, _ in exclusions)
    print(f"wrote {CHARSET_YAML}")
    print(f"wrote {MAPPING_YAML}")
    print(f"  pairs filtered:           {len(rows)}")
    print(f"  connected components:     {len(comps)}")
    print(f"  codepoints excluded:      {len(exclusions)}")
    print(f"  minor-side occurrences:   {minor_total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
