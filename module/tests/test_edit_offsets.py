from __future__ import annotations

import pytest

from bkk.edit.offsets import (
    OffsetRebaseConflict,
    map_position,
    map_structural_span,
    rebase_content_span,
)


def test_map_position_counts_unicode_codepoints():
    edit = {"start": 1, "delete_count": 0, "insert": "𠀀新"}
    assert map_position(3, edit, right=False) == 5


def test_structural_span_expands_at_boundaries():
    edits = [{"start": 2, "delete_count": 0, "insert": "甲"}]
    assert map_structural_span(2, 4, edits) == (2, 5)


def test_content_span_preserves_start_boundary_insertions():
    span = rebase_content_span(2, 3, [{"start": 2, "delete_count": 0, "insert": "甲乙"}])
    assert (span.start, span.length) == (4, 3)


def test_content_span_excludes_end_boundary_insertions():
    span = rebase_content_span(2, 3, [{"start": 5, "delete_count": 0, "insert": "甲乙"}])
    assert (span.start, span.length) == (2, 3)


def test_content_span_shifts_after_deletion_before_it():
    span = rebase_content_span(5, 2, [{"start": 1, "delete_count": 3, "insert": ""}])
    assert (span.start, span.length) == (2, 2)


def test_content_span_blocks_internal_insertions():
    with pytest.raises(OffsetRebaseConflict):
        rebase_content_span(2, 3, [{"start": 3, "delete_count": 0, "insert": "甲"}])


def test_content_span_blocks_overlapping_deletions():
    with pytest.raises(OffsetRebaseConflict):
        rebase_content_span(2, 3, [{"start": 1, "delete_count": 2, "insert": ""}])
