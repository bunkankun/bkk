#!/usr/bin/env python3
"""
Character-frequency survey of the Kanripo corpus.

Walks a tree of `*.txt` juan files, strips org-mode-style header lines,
tokenizes inline markup and entity references into separate buckets, NFC-
normalizes the residue, and emits per-codepoint and aggregate frequency
tables. Output grounds the definition of the canonical character set
described in bunkankun.md (§Canonicalization).

Usage:
    python3 tools/char_survey.py [--root DIR] [--out DIR] [--limit N]
"""

from __future__ import annotations

import argparse
import bisect
import os
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_ROOT = Path("/home/Shared/krpdev/gh")
DEFAULT_OUT = Path(__file__).resolve().parent / "survey-out"

HEADER_RE = re.compile(r"^(# |#\+)")
# Comment / metadata lines that appear inside the body too (e.g.
#   "# src: XC 1.2, ed. Lou Yulie p. 535"
#   "# dating: 8110"
# org-mode style. Anywhere in the file.
COMMENT_LINE_RE = re.compile(r"(?m)^#[^\n]*\n?")
# Leading org-mode headline markers: "*", "**", "***", ... at line start
# followed by a space. These are layout (register dividers); the text after
# is real content.
ORG_HEADLINE_RE = re.compile(r"(?m)^\*+ ")

TOKEN_RE = re.compile(
    r"<pb:[^>\n]*>"
    r"|&KR[0-9A-Fa-f]+;"
    r"|&CH-0F[0-9A-Fa-f]+;"
    r"|&[^;\s<>&]{1,64};"
    r"|<[^>\n]{1,128}>"
)

PILCROW = "\u00b6"


# Unicode block ranges (start_inclusive, end_inclusive, name).
# Sourced from the Unicode standard; covers everything likely to appear in
# the Kanripo corpus plus enough surrounding blocks that an unexpected
# codepoint still gets a name. List is sorted by start.
UNICODE_BLOCKS: list[tuple[int, int, str]] = [
    (0x0000, 0x007F, "Basic Latin"),
    (0x0080, 0x00FF, "Latin-1 Supplement"),
    (0x0100, 0x017F, "Latin Extended-A"),
    (0x0180, 0x024F, "Latin Extended-B"),
    (0x0250, 0x02AF, "IPA Extensions"),
    (0x02B0, 0x02FF, "Spacing Modifier Letters"),
    (0x0300, 0x036F, "Combining Diacritical Marks"),
    (0x0370, 0x03FF, "Greek and Coptic"),
    (0x0400, 0x04FF, "Cyrillic"),
    (0x1100, 0x11FF, "Hangul Jamo"),
    (0x2000, 0x206F, "General Punctuation"),
    (0x2070, 0x209F, "Superscripts and Subscripts"),
    (0x20A0, 0x20CF, "Currency Symbols"),
    (0x2100, 0x214F, "Letterlike Symbols"),
    (0x2150, 0x218F, "Number Forms"),
    (0x2190, 0x21FF, "Arrows"),
    (0x2200, 0x22FF, "Mathematical Operators"),
    (0x2300, 0x23FF, "Miscellaneous Technical"),
    (0x2400, 0x243F, "Control Pictures"),
    (0x2460, 0x24FF, "Enclosed Alphanumerics"),
    (0x2500, 0x257F, "Box Drawing"),
    (0x2580, 0x259F, "Block Elements"),
    (0x25A0, 0x25FF, "Geometric Shapes"),
    (0x2600, 0x26FF, "Miscellaneous Symbols"),
    (0x2700, 0x27BF, "Dingbats"),
    (0x2E00, 0x2E7F, "Supplemental Punctuation"),
    (0x2E80, 0x2EFF, "CJK Radicals Supplement"),
    (0x2F00, 0x2FDF, "Kangxi Radicals"),
    (0x2FF0, 0x2FFF, "Ideographic Description Characters"),
    (0x3000, 0x303F, "CJK Symbols and Punctuation"),
    (0x3040, 0x309F, "Hiragana"),
    (0x30A0, 0x30FF, "Katakana"),
    (0x3100, 0x312F, "Bopomofo"),
    (0x3130, 0x318F, "Hangul Compatibility Jamo"),
    (0x3190, 0x319F, "Kanbun"),
    (0x31C0, 0x31EF, "CJK Strokes"),
    (0x31F0, 0x31FF, "Katakana Phonetic Extensions"),
    (0x3200, 0x32FF, "Enclosed CJK Letters and Months"),
    (0x3300, 0x33FF, "CJK Compatibility"),
    (0x3400, 0x4DBF, "CJK Unified Ideographs Extension A"),
    (0x4DC0, 0x4DFF, "Yijing Hexagram Symbols"),
    (0x4E00, 0x9FFF, "CJK Unified Ideographs"),
    (0xA000, 0xA48F, "Yi Syllables"),
    (0xAC00, 0xD7AF, "Hangul Syllables"),
    (0xE000, 0xF8FF, "Private Use Area"),
    (0xF900, 0xFAFF, "CJK Compatibility Ideographs"),
    (0xFB00, 0xFB4F, "Alphabetic Presentation Forms"),
    (0xFE00, 0xFE0F, "Variation Selectors"),
    (0xFE30, 0xFE4F, "CJK Compatibility Forms"),
    (0xFE50, 0xFE6F, "Small Form Variants"),
    (0xFF00, 0xFFEF, "Halfwidth and Fullwidth Forms"),
    (0xFFF0, 0xFFFF, "Specials"),
    (0x1F200, 0x1F2FF, "Enclosed Ideographic Supplement"),
    (0x20000, 0x2A6DF, "CJK Unified Ideographs Extension B"),
    (0x2A700, 0x2B73F, "CJK Unified Ideographs Extension C"),
    (0x2B740, 0x2B81F, "CJK Unified Ideographs Extension D"),
    (0x2B820, 0x2CEAF, "CJK Unified Ideographs Extension E"),
    (0x2CEB0, 0x2EBEF, "CJK Unified Ideographs Extension F"),
    (0x2EBF0, 0x2EE5F, "CJK Unified Ideographs Extension I"),
    (0x2F800, 0x2FA1F, "CJK Compatibility Ideographs Supplement"),
    (0x30000, 0x3134F, "CJK Unified Ideographs Extension G"),
    (0x31350, 0x323AF, "CJK Unified Ideographs Extension H"),
    (0xE0100, 0xE01EF, "Variation Selectors Supplement"),
    (0xF0000, 0xFFFFD, "Supplementary Private Use Area-A"),
    (0x100000, 0x10FFFD, "Supplementary Private Use Area-B"),
]
_BLOCK_STARTS = [b[0] for b in UNICODE_BLOCKS]


def block_name(cp: int) -> str:
    i = bisect.bisect_right(_BLOCK_STARTS, cp) - 1
    if 0 <= i < len(UNICODE_BLOCKS):
        start, end, name = UNICODE_BLOCKS[i]
        if start <= cp <= end:
            return name
    return "Unassigned/Unmapped"


def char_name(cp: int) -> str:
    try:
        return unicodedata.name(chr(cp))
    except ValueError:
        return ""


def strip_header(text: str) -> str:
    """Remove leading org-mode header block AND every later '# …' comment line.

    Inline comment lines like '# src: XC 1.2, ed. Lou Yulie p. 535' carry
    editorial metadata, not canonical text, and need to be excluded from the
    codepoint count along with the file-top header.
    """
    return COMMENT_LINE_RE.sub("", text)


def strip_org_headline_markers(text: str) -> tuple[str, int]:
    """Remove leading '*+ ' from each line; return (text, count_of_strips).

    The trailing CJK content is real and stays in the residue; the leading
    asterisks are layout (register dividers) and are bucketed separately.
    """
    count = 0

    def _sub(m: re.Match) -> str:
        nonlocal count
        count += 1
        return ""

    return ORG_HEADLINE_RE.sub(_sub, text), count


def iter_txt_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip .git and other dot-directories in place.
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if name.startswith("."):
                continue
            if not name.endswith(".txt"):
                continue
            yield Path(dirpath) / name


def survey(root: Path, limit: int | None) -> dict:
    cp_counts: Counter[int] = Counter()
    ws_counts: Counter[int] = Counter()
    pilcrow_count = 0
    org_headline_count = 0
    pb_count = 0
    pb_samples: Counter[str] = Counter()
    entity_kr: Counter[str] = Counter()
    entity_chpua: Counter[str] = Counter()
    entity_other: Counter[str] = Counter()
    markup_other: Counter[str] = Counter()

    file_count = 0
    decode_errors: list[str] = []
    nfc_changed_files = 0

    t0 = time.time()
    for path in iter_txt_files(root):
        if limit is not None and file_count >= limit:
            break
        file_count += 1
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            decode_errors.append(f"{path}: {e}")
            continue

        body = strip_header(text)
        body, n_headlines = strip_org_headline_markers(body)
        org_headline_count += n_headlines

        # Tokenize: split out markup and entities; iterate the rest as residue.
        residue_parts: list[str] = []
        last = 0
        for m in TOKEN_RE.finditer(body):
            if m.start() > last:
                residue_parts.append(body[last:m.start()])
            tok = m.group(0)
            if tok.startswith("<pb:"):
                pb_count += 1
                if len(pb_samples) < 64:
                    pb_samples[tok] += 1
                else:
                    if tok in pb_samples:
                        pb_samples[tok] += 1
            elif tok.startswith("&KR"):
                entity_kr[tok] += 1
            elif tok.startswith("&CH-0F"):
                entity_chpua[tok] += 1
            elif tok.startswith("&"):
                entity_other[tok] += 1
            else:
                markup_other[tok] += 1
            last = m.end()
        if last < len(body):
            residue_parts.append(body[last:])

        residue = "".join(residue_parts)
        nfc = unicodedata.normalize("NFC", residue)
        if nfc != residue:
            nfc_changed_files += 1

        for ch in nfc:
            cp = ord(ch)
            if ch == PILCROW:
                pilcrow_count += 1
            elif ch.isspace():
                ws_counts[cp] += 1
            else:
                cp_counts[cp] += 1

    elapsed = time.time() - t0

    return {
        "file_count": file_count,
        "decode_errors": decode_errors,
        "nfc_changed_files": nfc_changed_files,
        "elapsed": elapsed,
        "cp_counts": cp_counts,
        "ws_counts": ws_counts,
        "pilcrow_count": pilcrow_count,
        "org_headline_count": org_headline_count,
        "pb_count": pb_count,
        "pb_samples": pb_samples,
        "entity_kr": entity_kr,
        "entity_chpua": entity_chpua,
        "entity_other": entity_other,
        "markup_other": markup_other,
    }


def write_codepoints_tsv(path: Path, cp_counts: Counter[int]) -> None:
    rows = sorted(cp_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    with path.open("w", encoding="utf-8") as f:
        f.write("cp_hex\tchar\tcount\tblock\tcategory\tname\n")
        for cp, count in rows:
            ch = chr(cp)
            display = ch if ch.isprintable() else ""
            f.write(
                f"U+{cp:04X}\t{display}\t{count}\t{block_name(cp)}"
                f"\t{unicodedata.category(ch)}\t{char_name(cp)}\n"
            )


def write_blocks_tsv(path: Path, cp_counts: Counter[int]) -> None:
    by_block: dict[str, list[int]] = defaultdict(list)
    for cp, count in cp_counts.items():
        by_block[block_name(cp)].append(count)
    rows = [
        (name, len(counts), sum(counts))
        for name, counts in by_block.items()
    ]
    rows.sort(key=lambda r: -r[2])
    with path.open("w", encoding="utf-8") as f:
        f.write("block\tdistinct_codepoints\ttotal_count\n")
        for name, distinct, total in rows:
            f.write(f"{name}\t{distinct}\t{total}\n")


def write_categories_tsv(path: Path, cp_counts: Counter[int]) -> None:
    by_cat: dict[str, list[int]] = defaultdict(list)
    for cp, count in cp_counts.items():
        by_cat[unicodedata.category(chr(cp))].append(count)
    rows = [
        (cat, len(counts), sum(counts)) for cat, counts in by_cat.items()
    ]
    rows.sort(key=lambda r: -r[2])
    with path.open("w", encoding="utf-8") as f:
        f.write("category\tdistinct_codepoints\ttotal_count\n")
        for cat, distinct, total in rows:
            f.write(f"{cat}\t{distinct}\t{total}\n")


def write_entities_tsv(path: Path, kr: Counter, chpua: Counter, other: Counter) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write("namespace\tentity\tcount\n")
        for ns, c in (("kr", kr), ("ch-pua", chpua), ("other", other)):
            for ent, count in c.most_common():
                f.write(f"{ns}\t{ent}\t{count}\n")


def write_markup_tsv(path: Path, pb_count: int, pb_samples: Counter, other: Counter) -> None:
    with path.open("w", encoding="utf-8") as f:
        f.write("kind\ttoken\tcount\n")
        f.write(f"pb-total\t<pb:...>\t{pb_count}\n")
        for tok, count in pb_samples.most_common(20):
            f.write(f"pb-sample\t{tok}\t{count}\n")
        for tok, count in other.most_common():
            f.write(f"other\t{tok}\t{count}\n")


def write_summary(path: Path, root: Path, result: dict) -> None:
    cp_counts: Counter[int] = result["cp_counts"]
    ws_counts: Counter[int] = result["ws_counts"]
    total_cp = sum(cp_counts.values())
    total_ws = sum(ws_counts.values())

    with path.open("w", encoding="utf-8") as f:
        f.write(f"root: {root}\n")
        f.write(f"files processed: {result['file_count']}\n")
        f.write(f"decode errors: {len(result['decode_errors'])}\n")
        if result["decode_errors"]:
            for line in result["decode_errors"][:10]:
                f.write(f"  {line}\n")
        f.write(f"files where NFC altered residue: {result['nfc_changed_files']}\n")
        f.write(f"elapsed seconds: {result['elapsed']:.2f}\n\n")

        f.write(f"distinct codepoints (textual): {len(cp_counts)}\n")
        f.write(f"total textual codepoints: {total_cp}\n")
        f.write(f"pilcrow ({PILCROW!r}) count: {result['pilcrow_count']}\n")
        f.write(f"org-mode headline markers stripped: {result['org_headline_count']}\n")
        f.write(f"whitespace codepoints distinct/total: {len(ws_counts)}/{total_ws}\n")
        f.write(f"<pb:...> tokens: {result['pb_count']}\n")
        f.write(f"&KR entity tokens distinct/total: "
                f"{len(result['entity_kr'])}/{sum(result['entity_kr'].values())}\n")
        f.write(f"&CH-0F entity tokens distinct/total: "
                f"{len(result['entity_chpua'])}/{sum(result['entity_chpua'].values())}\n")
        f.write(f"other entity tokens distinct/total: "
                f"{len(result['entity_other'])}/{sum(result['entity_other'].values())}\n")
        f.write(f"other angle markup tokens distinct/total: "
                f"{len(result['markup_other'])}/{sum(result['markup_other'].values())}\n\n")

        f.write("--- top 20 textual codepoints ---\n")
        for cp, count in cp_counts.most_common(20):
            ch = chr(cp)
            f.write(f"  U+{cp:04X} {ch!r:>6} {count:>10}  {block_name(cp)}\n")

        f.write("\n--- top 20 blocks ---\n")
        by_block: dict[str, int] = defaultdict(int)
        for cp, count in cp_counts.items():
            by_block[block_name(cp)] += count
        for name, total in sorted(by_block.items(), key=lambda kv: -kv[1])[:20]:
            f.write(f"  {total:>10}  {name}\n")

        f.write("\n--- top 20 entities (any namespace) ---\n")
        merged: Counter[tuple[str, str]] = Counter()
        for ns, c in (("kr", result["entity_kr"]),
                      ("ch-pua", result["entity_chpua"]),
                      ("other", result["entity_other"])):
            for ent, count in c.items():
                merged[(ns, ent)] += count
        for (ns, ent), count in merged.most_common(20):
            f.write(f"  {count:>10}  {ns}\t{ent}\n")

        f.write("\n--- whitespace breakdown ---\n")
        for cp, count in ws_counts.most_common():
            f.write(f"  U+{cp:04X}  {count:>10}  {char_name(cp)}\n")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--limit", type=int, default=None,
                   help="Process at most N .txt files (smoke-test).")
    args = p.parse_args()

    if not args.root.exists():
        print(f"root does not exist: {args.root}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)

    result = survey(args.root, args.limit)

    write_codepoints_tsv(args.out / "codepoints.tsv", result["cp_counts"])
    write_blocks_tsv(args.out / "blocks.tsv", result["cp_counts"])
    write_categories_tsv(args.out / "categories.tsv", result["cp_counts"])
    write_entities_tsv(
        args.out / "entities.tsv",
        result["entity_kr"], result["entity_chpua"], result["entity_other"],
    )
    write_markup_tsv(
        args.out / "markup.tsv",
        result["pb_count"], result["pb_samples"], result["markup_other"],
    )
    write_summary(args.out / "summary.txt", args.root, result)

    print(f"processed {result['file_count']} files in {result['elapsed']:.1f}s")
    print(f"outputs: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
