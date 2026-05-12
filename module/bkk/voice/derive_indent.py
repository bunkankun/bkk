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

Consecutive line segments with the same voice name are emitted as a single
voice span: a run of several commentators' lines between two root lines
becomes one ``commentary`` span. The id of each emitted marker is drawn
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

    if not line_offsets:
        return []

    line_offsets.sort()

    # Build line segments: (start_offset, end_offset, voice_name).
    # Segment ends at the next line-break offset or text_len.
    segments: list[tuple[int, int, str]] = []
    for i, start in enumerate(line_offsets):
        end = line_offsets[i + 1] if i + 1 < len(line_offsets) else text_len
        if end <= start:
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


def _id_prefix(name: str) -> str:
    return name[:1] if name else "v"
