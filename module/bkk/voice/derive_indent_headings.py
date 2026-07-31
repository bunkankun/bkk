"""Derive heading voices from CJK layout indentation.

Some KRP tractat-style texts mark section labels only by opening
ideographic-space indentation.  This deriver is intentionally narrower than
``derive_indent``: it emits only ``name: head`` voice spans for short
heading-like lines and rejects common layout uses such as TOC rows and long
prefatory prose.
"""

from __future__ import annotations

from dataclasses import dataclass


HEADING_INDENT_VOICE_SOURCE = "indent-headings"
_MIN_HEADING_LEN = 2
_MAX_HEADING_LEN = 8
_HEADING_SUFFIXES = ("篇", "章", "卷", "品")
_STANDALONE_HEADS = {"附録", "附錄", "目録", "目錄"}
_ATTRIBUTION_SUFFIXES = ("撰", "著", "注", "註", "箋", "校", "纂", "譯", "译")
_ATTRIBUTION_CHARS = {"臣"}


@dataclass(frozen=True)
class _Line:
    start: int
    end: int
    depth: int
    text: str
    internal_indent_offsets: tuple[int, ...]
    punctuation_offsets: tuple[int, ...]


@dataclass(frozen=True)
class _Candidate:
    start: int
    end: int
    depth: int
    text: str
    kind: str


def derive_voice_markers_from_indent_headings(
    text_len: int,
    markers: list[dict],
    text: str,
) -> list[dict]:
    """Return ``head`` voice markers derived from opening CJK indents."""
    candidates = _heading_candidates(text_len, markers, text)
    out: list[dict] = []
    for index, candidate in enumerate(candidates, 1):
        out.append({
            "type": "voice",
            "offset": candidate.start,
            "length": candidate.end - candidate.start,
            "name": "head",
            "id": f"h{index}",
            "source": HEADING_INDENT_VOICE_SOURCE,
            "indent_depth": candidate.depth,
        })
    return out


def has_indent_heading_profile(text_len: int, markers: list[dict], text: str) -> bool:
    """Return true when the bucket looks like tractat heading layout."""
    candidates = _heading_candidates(text_len, markers, text)
    if len(candidates) < 2:
        return False
    return any(candidate.kind in {"suffix", "known"} for candidate in candidates)


def _heading_candidates(
    text_len: int,
    markers: list[dict],
    text: str,
) -> list[_Candidate]:
    if text_len <= 0:
        return []

    lines = _lines(text_len, markers, text)
    out: list[_Candidate] = []
    seen_title_like: set[str] = set()
    emitted_title_like = False
    for line in lines:
        candidates = _candidates_for_line(
            line,
            allow_title_like=not emitted_title_like,
            seen_title_like=seen_title_like,
        )
        if not candidates:
            continue
        out.extend(candidates)
        if any(candidate.kind == "title" for candidate in candidates):
            emitted_title_like = True
            seen_title_like.update(
                candidate.text for candidate in candidates
                if candidate.kind == "title"
            )
    return out


def _lines(text_len: int, markers: list[dict], text: str) -> list[_Line]:
    line_offsets: list[int] = []
    seen_line: set[int] = set()
    indent_at: dict[int, int] = {}
    indent_offsets: list[int] = []
    punctuation_offsets: list[int] = []

    for marker in markers:
        if not isinstance(marker, dict):
            continue
        offset = marker.get("offset")
        if not isinstance(offset, int):
            continue
        marker_type = marker.get("type")
        if marker_type == "line-break":
            if offset not in seen_line:
                seen_line.add(offset)
                line_offsets.append(offset)
        elif marker_type == "indent":
            content = marker.get("content") or ""
            depth = len(content) if set(content) <= {"\u3000"} else 0
            if depth:
                indent_at[offset] = max(indent_at.get(offset, 0), depth)
                indent_offsets.append(offset)
        elif marker_type == "punctuation":
            punctuation_offsets.append(offset)

    if not line_offsets:
        return []

    line_offsets.sort()
    indent_offsets.sort()
    punctuation_offsets.sort()

    lines: list[_Line] = []
    for index, start in enumerate(line_offsets):
        end = line_offsets[index + 1] if index + 1 < len(line_offsets) else text_len
        if end <= start:
            continue
        depth = indent_at.get(start, 0)
        if depth <= 0:
            continue
        internal_indent_offsets = tuple(
            offset for offset in indent_offsets if start < offset < end
        )
        internal_punctuation_offsets = tuple(
            offset for offset in punctuation_offsets if start < offset < end
        )
        lines.append(_Line(
            start=start,
            end=end,
            depth=depth,
            text=text[start:end],
            internal_indent_offsets=internal_indent_offsets,
            punctuation_offsets=internal_punctuation_offsets,
        ))
    return lines


def _candidates_for_line(
    line: _Line,
    *,
    allow_title_like: bool,
    seen_title_like: set[str],
) -> list[_Candidate]:
    if line.depth not in {1, 2, 3}:
        return []
    if line.internal_indent_offsets:
        # Multi-column TOC rows commonly use depth-1/2 plus internal spacing.
        # Depth-3 rows in tractat bibliographic sections, by contrast, can
        # carry two headings on one physical line.
        if line.depth != 3:
            return []
        points = (
            (line.start, *line.internal_indent_offsets),
            (*line.internal_indent_offsets, line.end),
        )
        candidates: list[_Candidate] = []
        for start, end in zip(*points):
            candidate = _candidate_for_span(
                start,
                end,
                line.depth,
                line.text[start - line.start:end - line.start],
                punctuation_offsets=line.punctuation_offsets,
                allow_title_like=False,
                seen_title_like=seen_title_like,
            )
            if candidate is not None:
                candidates.append(candidate)
        return candidates
    candidate = _candidate_for_span(
        line.start,
        line.end,
        line.depth,
        line.text,
        punctuation_offsets=line.punctuation_offsets,
        allow_title_like=allow_title_like,
        seen_title_like=seen_title_like,
    )
    return [candidate] if candidate is not None else []


def _candidate_for_span(
    start: int,
    end: int,
    depth: int,
    span_text: str,
    *,
    punctuation_offsets: tuple[int, ...],
    allow_title_like: bool,
    seen_title_like: set[str],
) -> _Candidate | None:
    if depth != 3 and any(start < offset < end for offset in punctuation_offsets):
        return None
    if not (_MIN_HEADING_LEN <= len(span_text) <= _MAX_HEADING_LEN):
        return None
    if _looks_like_attribution(span_text):
        return None
    if span_text in _STANDALONE_HEADS:
        return _Candidate(start, end, depth, span_text, "known")
    if span_text.endswith(_HEADING_SUFFIXES):
        return _Candidate(start, end, depth, span_text, "suffix")
    if depth in {2, 3}:
        return _Candidate(start, end, depth, span_text, "short")
    if allow_title_like and depth == 1 and span_text not in seen_title_like:
        return _Candidate(start, end, depth, span_text, "title")
    return None


def _looks_like_attribution(text: str) -> bool:
    return text.endswith(_ATTRIBUTION_SUFFIXES) or any(
        char in text for char in _ATTRIBUTION_CHARS
    )
