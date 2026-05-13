"""Derive ``voice`` markers from layout indentation.

Some KRP/TLS sources don't fence commentary with ``(`` / ``)``; instead they
**indent** the line that carries each textual layer. The TLS importer captures
each ideographic space (U+3000) at a line opening as a point-typed ``indent``
marker (see ``bkk.importer.read.tls._append_text_filtered``), so the
layout signal is already on disk; this module reads it back out as
range-typed ``voice`` markers (see bunkankun.md §"Voices").

The classifier is configurable through an ``indent_voice_map`` that maps a
line's indent depth (in U+3000 codepoints) to a voice name. The default
map encodes the convention used by KR5c0095 (彭耜, 道德眞經集註):

    0 → root          (the classical line being commented on)
    1 → commentary    (a commentator's gloss)
    2 → head          (chapter title, e.g. 道沖章第四)
    3 → head          (chapter title, e.g. 道可道章第一)
    4 → attribution   (front-matter line, e.g. 宋鶴林眞逸彭耜纂集)

Other texts will plug in their own maps; indent depths absent from the map
yield no voice marker (the underlying ``indent`` markers stay on disk
unchanged).

**TOC sections are skipped.** A *strict-TOC line* opens with an
``indent`` marker *and* carries one or more additional ``indent``
markers between its line-break and the next line-break (multiple
FWS-separated cells on one printed line). A *high-indent line* opens
with an ``indent`` of depth > 1 — by itself this is just a deeply
indented line, but in the company of a strict-TOC line it is treated as
extending the same chart/list layout. We cluster strict-TOC and
high-indent lines, tolerating up to :data:`_TOC_GAP_TOLERANCE` plain
lines between consecutive cluster members. A cluster only "fires" if it
contains at least one strict-TOC line (so a lone deeply indented line
in pure-indent material like KR5c0095 stays a normal voice). The fired
cluster's span — first member through last, inclusive of intermediate
plain lines — is the **TOC section**, and every line in that span is
excluded from voice emission. This prevents indent voicing in TOC
contexts whose non-TOC neighbours would otherwise overlap with
paren-derived spans under ``--source all``.

Consecutive line segments with the same voice name are emitted as a single
voice span: a run of several commentators' lines between two root lines
becomes one ``commentary`` span. A skipped TOC section breaks the run, so
the segments on either side stay separate. The id of each emitted marker is drawn
from a per-name counter (``r1``, ``c1``, ``h1``, ``a1`` …); other voice
names fall back to a first-letter prefix.

``responds-to`` is set on any non-``root`` span and points to the id of the
most recent non-``commentary`` anchor (typically a preceding ``root``, but
a ``head`` or ``attribution`` is allowed too — a commentary may gloss a
chapter title as well as a root line). Omitted if no such anchor exists yet.
"""

from __future__ import annotations


DEFAULT_INDENT_VOICE_MAP: dict[int, str] = {
    0: "root",
    1: "commentary",
    2: "head",
    3: "head",
    4: "attribution",
}


# Max non-TOC lines between two cluster members that still belong to the
# same TOC section. Chosen empirically against KR1a0042: tight enough
# that ordinary prose (a 5+ line paragraph) breaks the cluster, loose
# enough that a short chart-explanation block (one heading line + three
# commentary lines, as in the divination-method chart of KR1a0042_001)
# stays inside the surrounding TOC layout.
_TOC_GAP_TOLERANCE = 4


def derive_voice_markers_from_indent(
    text_len: int,
    markers: list[dict],
    *,
    indent_voice_map: dict[int, str] | None = None,
) -> list[dict]:
    """Return new ``voice`` marker dicts derived from indent layout.

    ``markers`` is the bucket's existing marker list (plain dicts as
    loaded from YAML); it is not mutated. Markers other than
    ``line-break`` and ``indent`` are ignored for voicing purposes.

    Returns an empty list when the bucket has no line-break markers, or
    when every line segment falls outside the configured indent→voice
    map.
    """
    voice_map = (
        dict(DEFAULT_INDENT_VOICE_MAP)
        if indent_voice_map is None else dict(indent_voice_map)
    )
    if text_len <= 0:
        return []

    # Collect (offset, indent_depth) for every line opening. A line begins
    # at every line-break marker; its indent depth is the codepoint count
    # of any indent marker sharing the same offset (0 if none).
    indent_at: dict[int, int] = {}
    indent_offsets: list[int] = []
    line_offsets: list[int] = []
    seen_line: set[int] = set()
    for m in markers:
        if not isinstance(m, dict):
            continue
        t = m.get("type")
        off = m.get("offset")
        if not isinstance(off, int):
            continue
        if t == "line-break":
            if off not in seen_line:
                seen_line.add(off)
                line_offsets.append(off)
        elif t == "indent":
            content = m.get("content") or ""
            indent_at[off] = max(indent_at.get(off, 0), len(content))
            indent_offsets.append(off)

    if not line_offsets:
        return []

    line_offsets.sort()
    indent_offsets.sort()

    # Build line segments: (start_offset, end_offset, voice_name).
    # Segment ends at the next line-break offset or text_len. Lines that
    # fall inside a TOC section (see module docstring) are skipped.
    strict_toc_flags = [
        _is_strict_toc_line(
            start,
            line_offsets[i + 1] if i + 1 < len(line_offsets) else text_len,
            indent_offsets,
        )
        for i, start in enumerate(line_offsets)
    ]
    high_indent_flags = [
        indent_at.get(start, 0) > 1 for start in line_offsets
    ]
    toc_excluded = _toc_section_lines(
        strict_toc_flags, high_indent_flags, _TOC_GAP_TOLERANCE
    )
    segments: list[tuple[int, int, str]] = []
    for i, start in enumerate(line_offsets):
        end = line_offsets[i + 1] if i + 1 < len(line_offsets) else text_len
        if end <= start:
            continue
        if i in toc_excluded:
            continue
        depth = indent_at.get(start, 0)
        name = voice_map.get(depth)
        if name is None:
            continue
        segments.append((start, end, name))

    if not segments:
        return []

    # Group consecutive same-voice-name segments into runs.
    runs: list[tuple[int, int, str]] = []
    cur_start, cur_end, cur_name = segments[0]
    for start, end, name in segments[1:]:
        if name == cur_name and start == cur_end:
            cur_end = end
        else:
            runs.append((cur_start, cur_end, cur_name))
            cur_start, cur_end, cur_name = start, end, name
    runs.append((cur_start, cur_end, cur_name))

    # Emit one voice marker per run.
    counters: dict[str, int] = {}
    out: list[dict] = []
    last_anchor_id: str | None = None
    for start, end, name in runs:
        counters[name] = counters.get(name, 0) + 1
        prefix = _id_prefix(name)
        mid = f"{prefix}{counters[name]}"
        marker: dict = {
            "type": "voice",
            "offset": start,
            "length": end - start,
            "name": name,
            "id": mid,
        }
        if name != "root" and last_anchor_id is not None:
            marker["responds-to"] = last_anchor_id
        out.append(marker)
        if name != "commentary":
            last_anchor_id = mid

    return out


def _is_strict_toc_line(start: int, end: int, indent_offsets: list[int]) -> bool:
    """A strict-TOC line opens with an indent and carries at least one
    more indent strictly between its opener and the next line-break.

    ``indent_offsets`` is sorted; we use :mod:`bisect` so the check is
    O(log n) per line rather than O(n).
    """
    from bisect import bisect_left, bisect_right
    lo = bisect_left(indent_offsets, start)
    hi = bisect_right(indent_offsets, end - 1)
    in_line = indent_offsets[lo:hi]
    if not in_line or in_line[0] != start:
        return False
    return any(o > start for o in in_line)


def _toc_section_lines(
    strict_flags: list[bool],
    high_flags: list[bool],
    gap_tolerance: int,
) -> set[int]:
    """Group strict-TOC and high-indent lines into clusters; return every
    line index that falls inside a *fired* cluster's span.

    A cluster is a maximal run of strict-TOC and/or high-indent lines,
    tolerating up to ``gap_tolerance`` plain (neither-strict-nor-high)
    lines between consecutive members. A cluster *fires* (its span is
    excluded) only if it contains at least one strict-TOC line — a pure
    high-indent cluster, with no strict-TOC seed, stays voiced (this
    protects pure-indent fixtures like KR5c0095).

    The fired cluster's span is the inclusive range from its first to
    its last member; every line index in that span (member or plain) is
    excluded from voice emission.
    """
    excluded: set[int] = set()
    start: int | None = None
    last: int | None = None
    has_strict = False
    for i in range(len(strict_flags)):
        is_member = strict_flags[i] or high_flags[i]
        if not is_member:
            continue
        if start is None:
            start = last = i
            has_strict = strict_flags[i]
        elif i - last - 1 <= gap_tolerance:
            last = i
            has_strict = has_strict or strict_flags[i]
        else:
            if has_strict:
                excluded.update(range(start, last + 1))
            start = last = i
            has_strict = strict_flags[i]
    if start is not None and has_strict:
        excluded.update(range(start, last + 1))
    return excluded


def _id_prefix(name: str) -> str:
    return name[:1] if name else "v"
