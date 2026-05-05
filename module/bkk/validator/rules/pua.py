"""Section E: PUA-map.yaml constraints."""

from __future__ import annotations

import re

from ...importer.pua import PUA_BASE, PUA_END
from ..context import ValidationContext

KR_RE = re.compile(r"^KR(\d{4})$")
CODEPOINT_RE = re.compile(r"^U\+([0-9A-F]+)$")


def run(ctx: ValidationContext) -> None:
    lf = ctx.pua_map
    if lf is None:
        # Not present is fine — PUA-map is optional.
        return
    if lf.parse_error is not None:
        ctx.report.add(
            "MANIFEST_PARSE", "error", lf.rel,
            f"YAML parse error: {lf.parse_error}",
        )
        return
    if not isinstance(lf.data, dict):
        ctx.report.add(
            "PUA_TOTALS", "error", lf.rel,
            "PUA-map top level is not a mapping",
        )
        return
    data = lf.data

    if data.get("text_id") not in (None, ctx.text_id):
        ctx.report.add(
            "PUA_TOTALS", "error", lf.rel,
            f"text_id '{data.get('text_id')}' does not match bundle '{ctx.text_id}'",
        )

    entries = data.get("entries") or []
    if not isinstance(entries, list):
        ctx.report.add(
            "PUA_TOTALS", "error", lf.rel,
            "entries is not a list",
        )
        return

    parsed: list[tuple[int, int, int]] = []  # (kr_num, codepoint, count) for entries that pass format checks
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            ctx.report.add(
                "PUA_ENTRY_KR_FORMAT", "error", lf.rel,
                f"entries[{i}] is not a mapping",
            )
            continue
        kr = e.get("kr")
        cp = e.get("codepoint")
        ch = e.get("char")
        count = e.get("count")

        kr_num: int | None = None
        if isinstance(kr, str) and (km := KR_RE.match(kr)):
            kr_num = int(km.group(1))
        else:
            ctx.report.add(
                "PUA_ENTRY_KR_FORMAT", "error", lf.rel,
                f"entries[{i}].kr '{kr}' does not match ^KR\\d{{4}}$",
            )

        cp_int: int | None = None
        if isinstance(cp, str) and (cm := CODEPOINT_RE.match(cp)):
            cp_int = int(cm.group(1), 16)
            if not (PUA_BASE <= cp_int < PUA_END):
                ctx.report.add(
                    "PUA_ENTRY_CODEPOINT_FORMAT", "error", lf.rel,
                    f"entries[{i}].codepoint U+{cp_int:X} is outside [U+{PUA_BASE:X}, U+{PUA_END:X})",
                )
                cp_int = None
        else:
            ctx.report.add(
                "PUA_ENTRY_CODEPOINT_FORMAT", "error", lf.rel,
                f"entries[{i}].codepoint '{cp}' does not match ^U\\+[0-9A-F]+$",
            )

        if cp_int is not None and isinstance(ch, str):
            if ch != chr(cp_int):
                ctx.report.add(
                    "PUA_ENTRY_CHAR_MATCH", "error", lf.rel,
                    f"entries[{i}].char does not equal chr(U+{cp_int:X})",
                )
        if kr_num is not None and cp_int is not None:
            expected = PUA_BASE + kr_num
            if expected != cp_int:
                ctx.report.add(
                    "PUA_ENTRY_KR_CODEPOINT_MATCH", "error", lf.rel,
                    f"entries[{i}] kr={kr} ({hex(expected)}) != codepoint {cp} ({hex(cp_int)})",
                )

        if isinstance(count, int) and count > 0 and kr_num is not None and cp_int is not None:
            parsed.append((kr_num, cp_int, count))

    total_unique = data.get("total_unique")
    total_occurrences = data.get("total_occurrences")
    if isinstance(total_unique, int) and total_unique != len(entries):
        ctx.report.add(
            "PUA_TOTALS", "error", lf.rel,
            f"total_unique={total_unique} != len(entries)={len(entries)}",
        )
    if isinstance(total_occurrences, int):
        actual_total = sum(c for _, _, c in parsed)
        # Re-sum naively from raw counts for entries we couldn't parse fully.
        raw_total = sum(
            e.get("count") for e in entries
            if isinstance(e, dict) and isinstance(e.get("count"), int)
        )
        if total_occurrences != raw_total:
            ctx.report.add(
                "PUA_TOTALS", "error", lf.rel,
                f"total_occurrences={total_occurrences} != sum(entry.count)={raw_total}",
            )
        del actual_total  # only kept if we wanted to compare with parsed-only

    # Cross-check counts against actual juan texts. The importer aggregates
    # PUA-map entries across master + every edition (see
    # bkk.importer.read.krp.read_krp), so the validator must scan the same
    # set to get matching totals.
    actual_counts = _count_pua_across_all_editions(ctx)
    declared_counts = {
        cp: count for _, cp, count in parsed
    }
    for cp, count in declared_counts.items():
        actual = actual_counts.get(cp, 0)
        if actual != count:
            ctx.report.add(
                "PUA_COUNT_MATCHES_TEXT", "warning", lf.rel,
                f"codepoint U+{cp:X}: declared count {count}, actual occurrences across all editions {actual}",
            )
    for cp, actual in actual_counts.items():
        if cp not in declared_counts:
            ctx.report.add(
                "PUA_COUNT_MATCHES_TEXT", "warning", lf.rel,
                f"codepoint U+{cp:X} appears {actual} time(s) across editions but is not in PUA-map.entries",
            )


def _count_pua_across_all_editions(ctx: ValidationContext) -> dict[int, int]:
    counts: dict[int, int] = {}
    juan_files = list(ctx.master_juans.values())
    for ed in ctx.editions.values():
        juan_files.extend(ed.juans.values())
    for lf in juan_files:
        if not lf.exists or not isinstance(lf.data, dict):
            continue
        for bucket_name in ("front", "body", "back"):
            bucket = lf.data.get(bucket_name)
            if not isinstance(bucket, dict):
                continue
            text = bucket.get("text", "")
            if not isinstance(text, str):
                continue
            for ch in text:
                cp = ord(ch)
                if PUA_BASE <= cp < PUA_END:
                    counts[cp] = counts.get(cp, 0) + 1
    return counts
