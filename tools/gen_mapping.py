#!/usr/bin/env python3
"""Generate bkk-mapping-variant-fold-v1.yaml from v2r-ge065.tsv."""
import csv
import sys
from pathlib import Path

sys.path.insert(0, "../module")
import yaml
from bkk.importer.hashing import ZERO_HASH, sha256_jcs

SRC = Path("/home/chris/Dropbox/projects/bkk/tools/survey-out/v2r-ge065.tsv")
DST = Path("/home/chris/Dropbox/projects/bkk/module/refs/bkk-mapping-variant-fold-v1.yaml")

HEADER = """canonical_identifier: bkk:mapping/variant-fold-v1
valid_for_charset:
- bkk:charset/cjk-v1
scope: Folds variant pairs seeded from GB2013 (通用规范汉字表) and the Var-to-Rep variant table; each source codepoint maps to a single canonical replacement.
entries:
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
    n = 0
    with SRC.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            if (row.get("var_cp") or "").lstrip().startswith("#"):
                continue
            remarks = row.get("remarks") or ""
            if has_multiple_mappings(row) or "basic" in remark_tags(remarks):
                continue
            n += 1
            src_cp = row["var_cp"]
            src_char = row["var_char"]
            rep_cp = row["reg_cp"]
            rep_char = row["reg_char"]
            reason = extract_src(remarks)
            reason_y = f"'{reason}'" if "," in reason else reason
            var_count = row["var_count"]
            reg_count = row["reg_count"]
            lines.append(f"- id: vf-{n:04d}\n")
            lines.append(f"  source: {{cp: {src_cp}, char: {src_char}}}\n")
            lines.append(f"  replacement: {{cp: {rep_cp}, char: {rep_char}}}\n")
            lines.append(f"  reason: {reason_y}\n")
            lines.append(f"  note: corpus counts {var_count} -> {reg_count}\n")
    body = "".join(lines)
    data = yaml.safe_load(body)
    data["hash"] = ZERO_HASH
    digest = sha256_jcs(data)
    body += f"hash: {digest}\n"
    DST.write_text(body, encoding="utf-8")
    print(f"wrote {DST} ({n} mapping entries, hash: {digest})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
