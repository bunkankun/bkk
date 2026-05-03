"""Canonicalization helpers.

Per bunkankun.md the canonicalization procedure has 6 steps; v1 of the
importer applies steps 1-4 (source, entity expansion as a no-op for clean
TLS XML, NFC, layout extraction) and computes the text hash (step 6).
Step 5 (substitution against the canonical character set) is deferred
until ``bkk-cjk-v1`` is finalized.

This module owns:
- NFC application,
- per-bucket merging of section-local text + markers into bucket-global
  text + markers (offsets shifted to bucket coordinates),
- the seg-offset map used downstream to resolve annotation offsets.
"""

from __future__ import annotations

import unicodedata
from dataclasses import replace

from .ir import Marker, Section


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def merge_sections(sections: list[Section]) -> tuple[str, list[Marker], dict[str, int]]:
    """Concatenate sections into one bucket; shift marker offsets accordingly.

    Returns ``(text, markers, seg_offsets)`` where ``seg_offsets`` maps a
    ``tls:seg`` (or ``tls:head``) marker id to its codepoint offset in the
    merged bucket text.
    """
    out_text_parts: list[str] = []
    out_markers: list[Marker] = []
    seg_offsets: dict[str, int] = {}
    cursor = 0
    for sec in sections:
        for m in sec.markers:
            shifted = replace(m, offset=m.offset + cursor)
            out_markers.append(shifted)
            if shifted.type in ("tls:seg", "tls:head") and shifted.id:
                seg_offsets[shifted.id] = shifted.offset
        out_text_parts.append(sec.text)
        cursor += len(sec.text)
    return "".join(out_text_parts), out_markers, seg_offsets
