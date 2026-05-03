"""Classify sections into front/body/back buckets.

Default heuristic for TLS texts: a section whose head text contains any of the
configured tokens (default: ``序``) is routed to ``front``; everything else
goes to ``body``. ``back`` is unused for TLS in v1.

KRP texts use a different rule (opening-indent split — see
``split_front_by_opening_indent``); the resulting sections carry an explicit
``bucket`` field that ``bucket_sections`` honors verbatim.
"""

from __future__ import annotations

from .ir import Marker, Section


DEFAULT_FRONT_TOKENS = ("序",)


def bucket_sections(
    sections: list[Section],
    front_tokens: tuple[str, ...] = DEFAULT_FRONT_TOKENS,
) -> tuple[list[Section], list[Section], list[Section]]:
    """Return ``(front, body, back)`` lists, preserving relative order.

    A section with an explicit ``bucket`` field is routed accordingly. For
    sections without one the legacy heuristic applies (front-token match in
    head text → front; else body).
    """
    front: list[Section] = []
    body: list[Section] = []
    back: list[Section] = []
    for sec in sections:
        if sec.bucket == "front":
            front.append(sec)
        elif sec.bucket == "back":
            back.append(sec)
        elif sec.bucket == "body":
            body.append(sec)
        elif any(tok in sec.head_text for tok in front_tokens):
            front.append(sec)
        else:
            body.append(sec)
    return front, body, back


def split_front_by_opening_indent(sections: list[Section]) -> list[Section]:
    """Apply the KRP opening-indent rule to a juan's sections.

    Walks the *first* section's markers looking for the first ``line-break``
    marker that is *immediately* followed by an ``indent`` marker at the same
    offset. Everything before that offset becomes a synthetic ``front``
    section; from that offset on stays as the original ``body`` section. Any
    additional sections in the input are returned unchanged with
    ``bucket="body"``.

    Returns a fresh list; does not mutate the input sections.
    """
    if not sections:
        return list(sections)

    head = sections[0]
    split_off = _find_first_indented_line(head.markers)
    out: list[Section] = []
    if split_off is None:
        out.append(_with_bucket(head, "body"))
    else:
        front, body = _split_section_at(head, split_off)
        front.bucket = "front"
        body.bucket = "body"
        if front.text or front.markers:
            out.append(front)
        out.append(body)

    for sec in sections[1:]:
        out.append(_with_bucket(sec, "body"))
    return out


# ---------- helpers --------------------------------------------------------


def _with_bucket(section: Section, bucket: str) -> Section:
    return Section(
        head_text=section.head_text,
        head_marker_id=section.head_marker_id,
        text=section.text,
        markers=list(section.markers),
        bucket=bucket,
    )


def _find_first_indented_line(markers: list[Marker]) -> int | None:
    """Return the offset of the first line-break that opens an indented line.

    A line is "indented" when an ``indent`` marker appears at the same offset
    as the line-break with no other intervening content marker.
    """
    for i, m in enumerate(markers):
        if m.type != "line-break":
            continue
        for nxt in markers[i + 1:]:
            if nxt.offset != m.offset:
                break
            if nxt.type == "indent":
                return m.offset
            if nxt.type in ("line-break", "page-break"):
                continue
            break
    return None


def _split_section_at(section: Section,
                      split_offset: int) -> tuple[Section, Section]:
    """Split a section's text and markers at ``split_offset``.

    Markers at exactly ``split_offset`` go to the body section so the
    body-opening line-break/indent stay together with the body text.
    """
    front_text = section.text[:split_offset]
    body_text = section.text[split_offset:]

    front_markers: list[Marker] = []
    body_markers: list[Marker] = []
    for m in section.markers:
        if m.offset < split_offset:
            front_markers.append(m)
        else:
            body_markers.append(Marker(
                type=m.type, offset=m.offset - split_offset,
                content=m.content, id=m.id, extras=dict(m.extras),
            ))

    front = Section(
        head_text=section.head_text,
        head_marker_id=section.head_marker_id,
        text=front_text,
        markers=front_markers,
    )
    body = Section(
        head_text=section.head_text,
        head_marker_id=section.head_marker_id,
        text=body_text,
        markers=body_markers,
    )
    return front, body
