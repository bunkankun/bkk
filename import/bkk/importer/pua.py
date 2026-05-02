"""Kanripo private-use-area (PUA) helpers.

Kanripo sources represent characters that lacked Unicode codepoints at
transcription time as ``&KRnnnn;`` entity references. The KR registry
allocates them in the supplementary private-use plane:

    codepoint = 0x105000 + int(nnnn, 10)

That formula is authoritative — no lookup table is required.
"""

from __future__ import annotations

import re

PUA_BASE = 0x105000
PUA_END = 0x106000

_ENTITY_RE = re.compile(r"&KR(\d+);")


def kr_to_codepoint(kr_num: int) -> int:
    return PUA_BASE + kr_num


def codepoint_to_kr(cp: int) -> str | None:
    """Inverse of ``kr_to_codepoint``. Returns ``None`` for non-PUA codepoints."""
    if PUA_BASE <= cp < PUA_END:
        return f"KR{cp - PUA_BASE:04d}"
    return None


def expand_pua_entities(s: str) -> str:
    """Replace every ``&KRnnnn;`` in ``s`` with its PUA codepoint."""
    return _ENTITY_RE.sub(
        lambda m: chr(kr_to_codepoint(int(m.group(1)))), s,
    )


def summarise_pua_codepoints(text_id: str, texts: list[str]) -> dict | None:
    """Count PUA codepoints across the bundle's juan texts.

    Returns the dict shape used by ``PUA-map.yaml``::

        {text_id, total_unique, total_occurrences, entries: [...]}

    where each entry is ``{kr, char, codepoint: 'U+XXXXXX', count}``. Returns
    ``None`` when no PUA codepoints are present so the writer can omit the
    file altogether.
    """
    counts: dict[int, int] = {}
    for text in texts:
        for ch in text:
            cp = ord(ch)
            if PUA_BASE <= cp < PUA_END:
                counts[cp] = counts.get(cp, 0) + 1
    if not counts:
        return None
    entries = []
    for cp in sorted(counts):
        entries.append({
            "kr": codepoint_to_kr(cp),
            "char": chr(cp),
            "codepoint": f"U+{cp:X}",
            "count": counts[cp],
        })
    return {
        "text_id": text_id,
        "total_unique": len(entries),
        "total_occurrences": sum(counts.values()),
        "entries": entries,
    }
