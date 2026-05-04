"""Apply variants to derive per-witness texts and translate offsets back.

A witness text is the master text with each ``{type: variant, ..., <ed>: form}``
replaced by ``form`` for that edition. A position in a witness text translates
back to a master span via the segment list returned alongside the text:

- *identity* segments preserve a run of master characters one-to-one;
- *variant* segments correspond to a single variant entry — the master span
  ``[m_start, m_end)`` was replaced with the witness form ``[w_start, w_end)``.

Translating a witness span ``[w_lo, w_hi)`` back to master coordinates: any
identity segment contributes its precise sub-range; a variant segment that the
span touches widens the result to cover the segment's full master extent.
That widening is exactly what makes a witness-only character produce a hit
on the master position the variant lives at.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    w_start: int
    w_end: int
    m_start: int
    m_end: int
    is_variant: bool


def apply_witness(
    master_text: str,
    variants: list[dict],
    witness_short: str,
) -> tuple[str, list[Segment]]:
    """Return ``(witness_text, segments)`` for ``witness_short``.

    ``variants`` is a list of marker dicts in the YAML shape
    ``{offset, length, content, <witness>: form}``. Entries that don't carry
    ``witness_short`` are skipped (the master char passes through identity).
    """
    apply: list[tuple[int, int, str]] = []
    for v in variants:
        if witness_short not in v:
            continue
        offset = v["offset"]
        length = v.get("length")
        if length is None:
            length = len(v.get("content") or "")
        apply.append((offset, length, v[witness_short]))
    apply.sort(key=lambda t: t[0])

    out_chunks: list[str] = []
    segments: list[Segment] = []
    m_cursor = 0
    w_cursor = 0
    for m_off, length, w_form in apply:
        if m_off < m_cursor:
            # Overlapping/out-of-order entries shouldn't occur in well-formed
            # bundles; skip rather than corrupt the segment list.
            continue
        if m_off > m_cursor:
            chunk = master_text[m_cursor:m_off]
            out_chunks.append(chunk)
            segments.append(Segment(
                w_start=w_cursor, w_end=w_cursor + len(chunk),
                m_start=m_cursor, m_end=m_off,
                is_variant=False,
            ))
            w_cursor += len(chunk)
            m_cursor = m_off
        out_chunks.append(w_form)
        segments.append(Segment(
            w_start=w_cursor, w_end=w_cursor + len(w_form),
            m_start=m_off, m_end=m_off + length,
            is_variant=True,
        ))
        w_cursor += len(w_form)
        m_cursor = m_off + length

    if m_cursor < len(master_text):
        chunk = master_text[m_cursor:]
        out_chunks.append(chunk)
        segments.append(Segment(
            w_start=w_cursor, w_end=w_cursor + len(chunk),
            m_start=m_cursor, m_end=len(master_text),
            is_variant=False,
        ))

    return "".join(out_chunks), segments


def witness_to_master_span(
    segments: list[Segment],
    w_start: int,
    w_end: int,
) -> tuple[int, int]:
    """Translate a witness substring span to ``(master_offset, master_length)``.

    Touching any variant segment widens the result to that segment's full
    master extent.
    """
    if w_end <= w_start:
        seg = _segment_at(segments, w_start)
        if seg is None:
            return (segments[-1].m_end if segments else 0), 0
        m = seg.m_start if seg.is_variant else seg.m_start + (w_start - seg.w_start)
        return m, 0

    first = _segment_at(segments, w_start)
    last = _segment_at(segments, w_end - 1)
    if first is None or last is None:
        return 0, 0

    m_start = first.m_start if first.is_variant else first.m_start + (w_start - first.w_start)
    m_end = last.m_end if last.is_variant else last.m_start + (w_end - last.w_start)
    return m_start, m_end - m_start


def _segment_at(segments: list[Segment], w_pos: int):
    # Linear scan: segment counts are small per witness (one per variant + identity runs).
    for seg in segments:
        if seg.w_start <= w_pos < seg.w_end:
            return seg
    return None
