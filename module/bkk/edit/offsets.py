"""Offset rebasing helpers for text edits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class RebasedSpan:
    start: int
    length: int


class OffsetRebaseConflict(ValueError):
    """Raised when an edit changes the content of an anchored span."""


def _field(edit: Any, name: str) -> Any:
    if isinstance(edit, dict):
        return edit.get(name)
    return getattr(edit, name)


def edit_insert_length(edit: Any) -> int:
    insert = _field(edit, "insert") or ""
    return len(insert)


def map_position(position: int, edit: Any, *, right: bool) -> int:
    """Map a position through one splice.

    ``right`` chooses which side of a zero-width insertion the position binds
    to when the insertion occurs exactly at ``position``.
    """
    start = int(_field(edit, "start"))
    delete_count = int(_field(edit, "delete_count"))
    end = start + delete_count
    delta = edit_insert_length(edit) - delete_count
    if position < start:
        return position
    if position > end:
        return position + delta
    if delete_count == 0 and position == start:
        return start + (edit_insert_length(edit) if right else 0)
    if position == end:
        return start + edit_insert_length(edit)
    return start + (edit_insert_length(edit) if right else 0)


def map_structural_span(start: int, end: int, edits: Iterable[Any]) -> tuple[int, int]:
    """Map a structural span through edits, allowing inserted text to join it."""
    mapped_start = start
    mapped_end = end
    for edit in edits:
        mapped_start = map_position(mapped_start, edit, right=False)
        mapped_end = map_position(mapped_end, edit, right=True)
    return mapped_start, max(mapped_start, mapped_end)


def rebase_content_span(start: int, length: int, edits: Iterable[Any]) -> RebasedSpan:
    """Map a content span through edits, rejecting edits inside that span.

    This preserves the old cited content. Insertions at the span start are
    outside the citation and shift it right; insertions at the span end remain
    outside it. Deletions/replacements touching the span, or insertions inside
    it, are conflicts.
    """
    if length < 0:
        raise ValueError("length must be non-negative")
    mapped_start = start
    mapped_end = start + length
    for edit in edits:
        edit_start = int(_field(edit, "start"))
        delete_count = int(_field(edit, "delete_count"))
        edit_end = edit_start + delete_count
        if delete_count == 0:
            if mapped_start < edit_start < mapped_end:
                raise OffsetRebaseConflict("insertion overlaps span")
        elif edit_start < mapped_end and edit_end > mapped_start:
            raise OffsetRebaseConflict("deletion overlaps span")
        mapped_start = map_position(mapped_start, edit, right=True)
        mapped_end = map_position(mapped_end, edit, right=False)
    return RebasedSpan(mapped_start, max(0, mapped_end - mapped_start))
