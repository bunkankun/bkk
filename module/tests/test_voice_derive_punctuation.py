"""Unit tests for semantic punctuation voice derivation."""

from __future__ import annotations

import pytest

from bkk.voice.derive_punctuation import (
    derive_voice_markers_from_punctuation,
    derive_voice_markers_from_punctuation_best_effort,
)


def _punct(offset: int, ch: str) -> dict:
    return {"type": "punctuation", "offset": offset, "content": ch, "id": ""}


def test_title_brackets_derive_title_voice() -> None:
    markers = [_punct(2, "《"), _punct(8, "》")]

    assert derive_voice_markers_from_punctuation(20, markers) == [
        {
            "type": "voice",
            "offset": 2,
            "length": 6,
            "name": "title",
            "id": "t1",
            "source": "punctuation",
        },
    ]


def test_multiple_titles_increment_ids() -> None:
    markers = [
        _punct(1, "《"),
        _punct(4, "》"),
        _punct(8, "《"),
        _punct(12, "》"),
    ]

    assert derive_voice_markers_from_punctuation(20, markers) == [
        {
            "type": "voice",
            "offset": 1,
            "length": 3,
            "name": "title",
            "id": "t1",
            "source": "punctuation",
        },
        {
            "type": "voice",
            "offset": 8,
            "length": 4,
            "name": "title",
            "id": "t2",
            "source": "punctuation",
        },
    ]


def test_non_title_punctuation_is_ignored() -> None:
    markers = [_punct(2, "("), _punct(8, ")")]

    assert derive_voice_markers_from_punctuation(20, markers) == []


def test_unmatched_title_open_raises() -> None:
    with pytest.raises(ValueError, match="unmatched"):
        derive_voice_markers_from_punctuation(20, [_punct(2, "《")])


def test_best_effort_keeps_valid_title_around_problem() -> None:
    voices, problems = derive_voice_markers_from_punctuation_best_effort(
        20,
        [
            _punct(1, "《"),
            _punct(4, "》"),
            _punct(8, "《"),
            _punct(12, "《"),
            _punct(16, "》"),
        ],
    )

    assert voices == [
        {
            "type": "voice",
            "offset": 1,
            "length": 3,
            "name": "title",
            "id": "t1",
            "source": "punctuation",
        },
        {
            "type": "voice",
            "offset": 12,
            "length": 4,
            "name": "title",
            "id": "t2",
            "source": "punctuation",
        },
    ]
    assert [(problem.code, problem.offset, problem.length) for problem in problems] == [
        ("expected-close", 8, 4),
    ]
