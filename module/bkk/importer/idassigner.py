"""Deterministic IDs for importer-inserted markers.

Source-derived IDs (TLS xml:id, KRP ed_n) are authoritative. Where no
source ID exists, the importer assigns one of shape

    <text-id>_<edition>_<juan-label>-bkk<type-short><n>

The ``bkk`` prefix on the slug distinguishes importer-inserted IDs from
source-derived slugs (e.g. ``1a.5``). The counter ``n`` is per
(text, edition, juan, marker type), starting at 1. Order is determined
by marker order within the juan's merged bucket text, so the assignment
is reproducible across re-imports as long as the source ordering is
stable.
"""

from __future__ import annotations

from .ir import Marker

_TYPE_SHORT: dict[str, str] = {
    "page-break": "pb",
    "line-break": "lb",
    "paragraph-break": "p",
    "head": "h",
    "indent": "ind",
    "punctuation": "pn",
    "variant": "var",
    "voice": "vc",
    "tls:seg": "sg",
    "tls:seg-end": "sge",
    "tls:head": "th",
    "tls:div-start": "ds",
    "tls:div-end": "de",
    "tls:ann": "ann",
    "cbeta:juan-start": "cjs",
    "cbeta:juan-end": "cje",
    "cbeta:mulu": "cm",
}


def _short(marker_type: str) -> str:
    if marker_type in _TYPE_SHORT:
        return _TYPE_SHORT[marker_type]
    return marker_type.replace(":", "").replace("-", "")[:6] or "m"


def assign_marker_ids(
    markers: list[Marker], *,
    text_id: str, edition: str, juan_label: str,
) -> None:
    """Mutate ``markers`` in place, filling ``id`` on every marker with
    ``id == ""``. Source-derived IDs are left untouched."""
    counters: dict[str, int] = {}
    for m in markers:
        if m.id:
            continue
        short = _short(m.type)
        counters[short] = counters.get(short, 0) + 1
        m.id = f"{text_id}_{edition}_{juan_label}-bkk{short}{counters[short]}"
