"""Unit tests for TLS segment-type voice derivation."""

from __future__ import annotations

import pytest

from bkk.voice.derive import VoiceDerivationProblem
from bkk.voice.derive_tls_seg import derive_voice_markers_from_tls_segments


def _start(offset: int, seg_type: str, mid: str = "") -> dict:
    return {
        "type": "tls:seg-start",
        "offset": offset,
        "content": "",
        "id": mid,
        "seg_type": seg_type,
    }


def _end(offset: int, seg_type: str, mid: str = "") -> dict:
    return {
        "type": "tls:seg-end",
        "offset": offset,
        "content": "",
        "id": mid,
        "seg_type": seg_type,
    }


def test_empty_text_returns_no_markers() -> None:
    assert derive_voice_markers_from_tls_segments(0, []) == []


def test_root_and_comm_segment_runs_become_voice_spans() -> None:
    markers = [
        _start(0, "root", "s1"),
        {"type": "tls:seg", "offset": 0, "id": "s1"},
        _end(4, "root", "s1_end"),
        _start(4, "comm", "s2"),
        {"type": "tls:seg", "offset": 4, "id": "s2"},
        _end(10, "comm", "s2_end"),
        _start(10, "root", "s3"),
        _end(12, "root", "s3_end"),
    ]

    assert derive_voice_markers_from_tls_segments(12, markers) == [
        {"type": "voice", "offset": 0, "length": 4, "name": "root", "id": "r1"},
        {
            "type": "voice",
            "offset": 4,
            "length": 6,
            "name": "commentary",
            "id": "c1",
            "responds-to": "r1",
        },
        {"type": "voice", "offset": 10, "length": 2, "name": "root", "id": "r2"},
    ]


def test_leading_commentary_has_no_responds_to() -> None:
    markers = [
        _start(0, "comm"),
        _end(3, "comm"),
        _start(3, "root"),
        _end(5, "root"),
    ]

    assert derive_voice_markers_from_tls_segments(5, markers) == [
        {
            "type": "voice",
            "offset": 0,
            "length": 3,
            "name": "commentary",
            "id": "c1",
        },
        {"type": "voice", "offset": 3, "length": 2, "name": "root", "id": "r1"},
    ]


def test_plain_seg_end_closes_typed_start() -> None:
    markers = [
        _start(1, "root"),
        {"type": "tls:seg-end", "offset": 4, "content": "", "id": "s1_end"},
    ]

    assert derive_voice_markers_from_tls_segments(5, markers) == [
        {"type": "voice", "offset": 1, "length": 3, "name": "root", "id": "r1"},
    ]


def test_unsupported_segment_types_are_ignored() -> None:
    markers = [
        _start(0, "note"),
        _end(3, "note"),
        _start(3, "root"),
        _end(5, "root"),
    ]

    assert derive_voice_markers_from_tls_segments(5, markers) == [
        {"type": "voice", "offset": 3, "length": 2, "name": "root", "id": "r1"},
    ]


def test_unclosed_segment_reports_problem() -> None:
    with pytest.raises(VoiceDerivationProblem) as raised:
        derive_voice_markers_from_tls_segments(5, [_start(1, "root")])

    assert raised.value.code == "tls-seg-unclosed"
    assert raised.value.offset == 1
