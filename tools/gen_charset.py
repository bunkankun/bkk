#!/usr/bin/env python3
"""Generate bkk-charset-cjk-v1.yaml from v2r-ge065.tsv."""
import csv
import sys
from pathlib import Path

sys.path.insert(0, "../module")
import yaml
from bkk.importer.hashing import ZERO_HASH, sha256_jcs

SRC = Path("survey-out/v2r-ge065.tsv")
DST = Path("../module/refs/bkk-charset-cjk-v1.yaml")

HEADER = """canonical_identifier: bkk:charset/cjk-v1
description: Canonical character set for BKK CJK text, seeded from GB2013 and Var-to-Rep variant tables.
inclusion_blocks:
- {name: CJK Unified Ideographs, range: [U+4E00, U+9FFF]}
- {name: CJK Unified Ideographs Ext A, range: [U+3400, U+4DBF]}
- {name: CJK Unified Ideographs Ext B, range: [U+20000, U+2A6DF]}
- {name: CJK Unified Ideographs Ext C-F, range: [U+2A700, U+2EBEF]}
- {name: CJK Compatibility Ideographs, range: [U+F900, U+FAFF]}
- {name: BKK PUA, range: [U+105000, U+108FFF]}
excluded:
"""


def extract_src(remarks: str) -> str:
    for tag in remarks.split(";"):
        tag = tag.strip()
        if tag.startswith("src="):
            return tag[4:]
    return ""


def remark_tags(remarks: str) -> set[str]:
    return {tag.strip() for tag in remarks.split(";") if tag.strip()}


def has_multiple_mappings(row: dict[str, str | None]) -> bool:
    reg_cps = (row.get("reg_cp") or "").split(",")
    reg_chars = row.get("reg_char") or ""
    return len(reg_cps) > 1 or len(reg_chars) > 1


def main() -> int:
    lines = [HEADER]
    with SRC.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            if (row.get("var_cp") or "").lstrip().startswith("#"):
                continue
            remarks = row.get("remarks") or ""
            if has_multiple_mappings(row) or "basic" in remark_tags(remarks):
                continue
            cp = row["var_cp"]
            char = row["var_char"]
            reg_cp = row["reg_cp"]
            reg_char = row["reg_char"]
            reason = extract_src(remarks) or "tw-edu-var"
            reason_y = f"'{reason}'" if "," in reason else reason
            lines.append(
                f"- {{cp: {cp}, char: {char}, reason: {reason_y}, replaced_by: {reg_cp}}}\n"
            )
    body = "".join(lines)
    data = yaml.safe_load(body)
    data["hash"] = ZERO_HASH
    digest = sha256_jcs(data)
    body += f"hash: {digest}\n"
    DST.write_text(body, encoding="utf-8")
    print(f"wrote {DST} (excluded entries: {len(data['excluded'])}, hash: {digest})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
