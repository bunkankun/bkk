"""Render punctuation markers in canonical display order."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

PAGE_BREAK_RENDER_TOKEN = "\f"

PUNCTUATION_RENDER_ORDER = (
    "》",
    "？",
    "！",
    "；",
    "。",
    "』",
    "」",
    "，",
    "、",
    "：",
    "/",
    ")",
    "\n",
    PAGE_BREAK_RENDER_TOKEN,
    "\u3000",
    "(",
    "「",
    "『",
    "《",
)

NOTE_OPEN_PUNCT = frozenset(("(", "（", "「", "『", "《", "〈", "〔", "【"))
NOTE_CLOSE_PUNCT = frozenset((")", "）", "」", "』", "》", "〉", "〕", "】"))

_PUNCTUATION_RENDER_RANK = {
    ch: index for index, ch in enumerate(PUNCTUATION_RENDER_ORDER)
}


@dataclass(frozen=True)
class RenderInjection:
    """One marker-backed string to inject at a canonical text offset."""

    offset: int
    content: str
    index: float = 0.0


@dataclass(frozen=True)
class RenderUnit:
    """One renderable codepoint from an injected marker string."""

    ch: str
    index: float


def punctuation_render_rank(ch: str) -> int | None:
    """Return the logical render rank for a punctuation character."""

    return _PUNCTUATION_RENDER_RANK.get(ch)


def is_note_open_punctuation(ch: str) -> bool:
    """Return whether ``ch`` is a note-opening boundary punctuation mark."""

    return ch in NOTE_OPEN_PUNCT


def is_note_close_punctuation(ch: str) -> bool:
    """Return whether ``ch`` is a note-closing boundary punctuation mark."""

    return ch in NOTE_CLOSE_PUNCT


def sort_punctuation_render_order(units: Sequence[RenderUnit]) -> list[RenderUnit]:
    """Sort same-offset punctuation units using the Web UI display order.

    The Web UI first orders items by their source marker index, then sorts each
    contiguous run of ranked punctuation characters by display rank. Unknown
    characters keep their index order and break ranked runs.
    """

    ordered = sorted(units, key=lambda unit: unit.index)
    result: list[RenderUnit] = []
    start = 0
    while start < len(ordered):
        first_rank = punctuation_render_rank(ordered[start].ch)
        if first_rank is None:
            result.append(ordered[start])
            start += 1
            continue
        end = start + 1
        while (
            end < len(ordered)
            and punctuation_render_rank(ordered[end].ch) is not None
        ):
            end += 1
        result.extend(
            sorted(
                ordered[start:end],
                key=lambda unit: (
                    punctuation_render_rank(unit.ch) or 0,
                    unit.index,
                ),
            )
        )
        start = end
    return result


def punctuation_injections_from_markers(
    markers: Iterable[Mapping[str, Any]], text_len: int,
) -> list[RenderInjection]:
    """Extract valid punctuation marker injections from marker mappings."""

    out: list[RenderInjection] = []
    marker_index = 0
    for marker in markers:
        off = marker.get("offset")
        content = marker.get("content")
        if not isinstance(off, int) or isinstance(off, bool):
            continue
        if not isinstance(content, str) or not content:
            continue
        if off < 0 or off > text_len:
            continue
        out.append(RenderInjection(off, content, float(marker_index)))
        marker_index += 1
    return sorted(out, key=lambda injection: (injection.offset, injection.index))


def render_text_with_punctuation(
    text: str, injections: Iterable[RenderInjection], start: int = 0,
    end: int | None = None, boundary: str = "leading",
) -> str:
    """Render ``text[start:end]`` with punctuation injected by offset.

    A marker at offset ``O`` renders immediately before the base character at
    ``O``. With the default ``boundary="leading"``, it belongs to the window
    containing that character (``start <= O < end``); trailing punctuation at
    ``O == len(text)`` renders in the final window. With
    ``boundary="trailing"``, punctuation exactly at a nonzero window boundary
    belongs to the preceding window instead. Same-offset injected characters
    are displayed in the logical order shared with the Web UI.
    """

    if boundary not in {"leading", "trailing"}:
        raise ValueError("boundary must be 'leading' or 'trailing'")

    text_len = len(text)
    if end is None:
        end = text_len
    start = max(0, min(start, text_len))
    end = max(start, min(end, text_len))

    by_offset: dict[int, list[RenderUnit]] = {}
    for injection in injections:
        off = injection.offset
        if off < start:
            continue
        if off > end:
            continue
        if boundary == "leading" and off == end and off != text_len:
            continue
        if boundary == "trailing" and off == start and start != 0:
            continue
        if off < 0 or off > text_len:
            continue
        chars = list(injection.content)
        if not chars:
            continue
        denominator = max(1, len(chars))
        units = by_offset.setdefault(off, [])
        for content_index, ch in enumerate(chars):
            units.append(
                RenderUnit(
                    ch=ch,
                    index=injection.index + content_index / denominator,
                )
            )

    parts: list[str] = []
    cursor = start
    for off in sorted(by_offset):
        if off > cursor:
            parts.append(text[cursor:off])
            cursor = off
        parts.extend(unit.ch for unit in sort_punctuation_render_order(by_offset[off]))
    if cursor < end:
        parts.append(text[cursor:end])
    return "".join(parts)
