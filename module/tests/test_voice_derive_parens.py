"""Unit tests for paren-based voice derivation."""

from __future__ import annotations

import pytest

from bkk.voice.derive import derive_voice_markers


def _paren(offset: int, ch: str) -> dict:
    return {"type": "punctuation", "offset": offset, "content": ch, "id": ""}


def test_empty_markers_returns_no_markers():
    assert derive_voice_markers(0, []) == []


def test_no_parens_returns_no_markers():
    # Punctuation other than `(` / `)` is ignored.
    markers = [{"type": "punctuation", "offset": 3, "content": "/", "id": ""}]
    assert derive_voice_markers(10, markers) == []


def test_single_paren_pair_one_note():
    markers = [_paren(2, "("), _paren(8, ")")]
    assert derive_voice_markers(20, markers) == [
        {"type": "voice", "offset": 2, "length": 6, "name": "note", "id": "n1"},
    ]


def test_multiple_paren_pairs_increment_ids():
    markers = [
        _paren(0, "("), _paren(3, ")"),
        _paren(5, "("), _paren(9, ")"),
        _paren(12, "("), _paren(18, ")"),
    ]
    assert derive_voice_markers(20, markers) == [
        {"type": "voice", "offset": 0, "length": 3, "name": "note", "id": "n1"},
        {"type": "voice", "offset": 5, "length": 4, "name": "note", "id": "n2"},
        {"type": "voice", "offset": 12, "length": 6, "name": "note", "id": "n3"},
    ]


def test_touching_pairs_same_offset_merge_into_one_note():
    markers = [
        _paren(2, "("), _paren(8, ")"),
        _paren(8, "("), _paren(12, ")"),
    ]
    assert derive_voice_markers(20, markers) == [
        {"type": "voice", "offset": 2, "length": 10, "name": "note", "id": "n1"},
    ]


def test_touching_pairs_same_offset_merge_regardless_of_tie_order():
    markers = [
        _paren(2, "("), _paren(8, "("),
        _paren(8, ")"), _paren(12, ")"),
    ]
    assert derive_voice_markers(20, markers) == [
        {"type": "voice", "offset": 2, "length": 10, "name": "note", "id": "n1"},
    ]


def test_no_root_emitted_even_with_long_unparen_runs():
    # 50 codepoints of text with a single paren pair near the end should
    # still produce only the note marker — no root span for the
    # surrounding prose.
    markers = [_paren(40, "("), _paren(45, ")")]
    out = derive_voice_markers(50, markers)
    assert all(v["name"] == "note" for v in out)
    assert len(out) == 1


def test_no_responds_to_set_on_any_marker():
    # The paren deriver has no anchor structure of its own — every
    # marker is a freestanding ``note``.
    markers = [
        _paren(0, "("), _paren(3, ")"),
        _paren(5, "("), _paren(9, ")"),
    ]
    out = derive_voice_markers(15, markers)
    assert all("responds-to" not in v for v in out)


def test_unmatched_open_raises():
    markers = [_paren(2, "(")]
    with pytest.raises(ValueError, match="unmatched"):
        derive_voice_markers(10, markers)


def test_close_before_open_raises():
    markers = [_paren(2, ")")]
    with pytest.raises(ValueError, match="no matching"):
        derive_voice_markers(10, markers)


def test_missing_offset_raises():
    markers = [{"type": "punctuation", "content": "(", "id": ""}]
    with pytest.raises(ValueError, match="missing integer offset"):
        derive_voice_markers(10, markers)


def test_non_paren_punctuation_ignored():
    # ``/`` is the column-break inside a paren span; it's not voiced.
    markers = [
        _paren(0, "("),
        {"type": "punctuation", "offset": 3, "content": "/", "id": ""},
        _paren(8, ")"),
    ]
    assert derive_voice_markers(10, markers) == [
        {"type": "voice", "offset": 0, "length": 8, "name": "note", "id": "n1"},
    ]


def test_non_dict_markers_skipped():
    # Plain strings/None in the list (defensive; shouldn't happen from
    # the YAML loader) are silently ignored.
    markers = ["not a dict", None, _paren(0, "("), _paren(5, ")")]
    assert derive_voice_markers(10, markers) == [
        {"type": "voice", "offset": 0, "length": 5, "name": "note", "id": "n1"},
    ]


def test_unsorted_markers_are_sorted_before_pairing():
    # Caller may hand markers in any order; deriver sorts by offset.
    markers = [_paren(8, ")"), _paren(2, "(")]
    assert derive_voice_markers(10, markers) == [
        {"type": "voice", "offset": 2, "length": 6, "name": "note", "id": "n1"},
    ]
