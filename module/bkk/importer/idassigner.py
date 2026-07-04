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


def marker_type_short(marker_type: str) -> str:
    """Return the stable slug abbreviation used for generated marker IDs."""
    if marker_type in _TYPE_SHORT:
        return _TYPE_SHORT[marker_type]
    return marker_type.replace(":", "").replace("-", "")[:6] or "m"


def allocate_marker_ids(
    marker_types: list[str],
    *,
    text_id: str,
    edition: str,
    juan_label: str,
    occupied_ids: set[str] | None = None,
) -> list[str]:
    """Allocate collision-free IDs for interactively inserted markers.

    Import assignment starts from a complete ordered marker stream. Editors
    instead add markers to an already-persisted stream, where numeric gaps
    must not be reused. Start after the largest occupied suffix for each
    marker type and reserve each result as it is returned.
    """
    occupied = set(occupied_ids or ())
    counters: dict[str, int] = {}
    allocated: list[str] = []
    for marker_type in marker_types:
        short = marker_type_short(marker_type)
        prefix = f"{text_id}_{edition}_{juan_label}-bkk{short}"
        if short not in counters:
            largest = 0
            for marker_id in occupied:
                if not marker_id.startswith(prefix):
                    continue
                suffix = marker_id[len(prefix):]
                if suffix.isdigit():
                    largest = max(largest, int(suffix))
            counters[short] = largest
        while True:
            counters[short] += 1
            marker_id = f"{prefix}{counters[short]}"
            if marker_id not in occupied:
                break
        occupied.add(marker_id)
        allocated.append(marker_id)
    return allocated


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
        short = marker_type_short(m.type)
        counters[short] = counters.get(short, 0) + 1
        m.id = f"{text_id}_{edition}_{juan_label}-bkk{short}{counters[short]}"
