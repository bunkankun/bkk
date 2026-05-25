from __future__ import annotations

from pathlib import Path

import yaml

from bkk.marker_assets import (
    build_marker_asset,
    effective_markers_for_bucket,
    marker_asset_hash,
    split_inline_external_markers,
)


def test_effective_markers_inline_only():
    juan = {
        "body": {
            "text": "甲乙",
            "markers": [{"type": "punctuation", "offset": 1, "content": "、"}],
        }
    }
    assert effective_markers_for_bucket(juan, "body") == [
        {"type": "punctuation", "offset": 1, "content": "、"}
    ]


def test_effective_markers_external_only():
    juan = {"body": {"text": "甲乙"}}
    asset = {
        "markers": {
            "body": [{"type": "punctuation", "offset": 1, "content": "、"}]
        }
    }
    assert effective_markers_for_bucket(juan, "body", asset) == [
        {"type": "punctuation", "offset": 1, "content": "、"}
    ]


def test_effective_markers_mixed_sorted_external_first_at_same_offset():
    juan = {
        "body": {
            "text": "甲乙",
            "markers": [{"type": "tls:head", "offset": 0, "id": "h"}],
        }
    }
    asset = {
        "markers": {
            "body": [
                {"type": "punctuation", "offset": 0, "content": "「"},
                {"type": "line-break", "offset": 2},
            ]
        }
    }
    assert [m["type"] for m in effective_markers_for_bucket(juan, "body", asset)] == [
        "punctuation",
        "tls:head",
        "line-break",
    ]


def test_split_keeps_structural_and_toc_ids_inline():
    markers = [
        {"type": "tls:head", "offset": 0, "id": "h"},
        {"type": "punctuation", "offset": 1, "id": "toc"},
        {"type": "line-break", "offset": 2},
    ]
    inline, external = split_inline_external_markers(markers, keep_ids={"toc"})
    assert [m.get("id") for m in inline] == ["h", "toc"]
    assert [m["type"] for m in external] == ["line-break"]


def test_marker_asset_hash_ignores_existing_hash_value():
    asset = build_marker_asset(
        "KR0x", 1, None,
        {"body": [{"type": "line-break", "offset": 2}]},
    )
    changed = dict(asset)
    changed["hash"] = "sha256:" + "f" * 64
    assert marker_asset_hash(changed) == asset["hash"]
