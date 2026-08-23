"""Punctuation rendering helpers."""

from __future__ import annotations

from bkk.rendering.punctuation import (
    NOTE_CLOSE_PUNCT,
    NOTE_OPEN_PUNCT,
    PAGE_BREAK_RENDER_TOKEN,
    RenderInjection,
    RenderUnit,
    is_note_close_punctuation,
    is_note_open_punctuation,
    render_text_with_punctuation,
    sort_punctuation_render_order,
)


def test_sort_punctuation_render_order_matches_web_ui_sequence():
    chars = list("《『「(：\n)」』、，。；？》")
    units = [
        RenderUnit(ch=ch, index=index / len(chars))
        for index, ch in enumerate(chars)
    ]

    assert "".join(unit.ch for unit in sort_punctuation_render_order(units)) == (
        "》？；。』」，、：)\n(「『《"
    )


def test_sort_punctuation_render_order_includes_layout_tokens():
    units = [
        RenderUnit(ch=ch, index=index)
        for index, ch in enumerate(f"。\n)/》{PAGE_BREAK_RENDER_TOKEN}\u3000\u3000")
    ]

    assert "".join(unit.ch for unit in sort_punctuation_render_order(units)) == (
        f"》。/)\n{PAGE_BREAK_RENDER_TOKEN}\u3000\u3000"
    )


def test_render_text_with_punctuation_orders_same_offset_injections():
    injections = [
        RenderInjection(offset=1, content="《『「(：\n)"),
        RenderInjection(offset=1, content="」』、，。；？》", index=1),
    ]

    assert render_text_with_punctuation("甲乙", injections) == (
        "甲》？；。』」，、：)\n(「『《乙"
    )


def test_render_text_with_punctuation_keeps_trailing_marker_in_final_window():
    injections = [RenderInjection(offset=4, content="。")]

    assert render_text_with_punctuation("甲乙丙丁", injections, 0, 2) == "甲乙"
    assert render_text_with_punctuation("甲乙丙丁", injections, 2, 4) == "丙丁。"


def test_render_text_with_punctuation_can_attach_boundaries_to_previous_window():
    injections = [
        RenderInjection(offset=0, content="「"),
        RenderInjection(offset=2, content="，"),
        RenderInjection(offset=4, content="。"),
    ]

    assert (
        render_text_with_punctuation(
            "甲乙丙丁", injections, 0, 2, boundary="trailing",
        )
        == "「甲乙，"
    )
    assert (
        render_text_with_punctuation(
            "甲乙丙丁", injections, 2, 4, boundary="trailing",
        )
        == "丙丁。"
    )


def test_note_boundary_punctuation_matches_web_ui_set():
    assert NOTE_OPEN_PUNCT == frozenset(("(", "（", "「", "『", "《", "〈", "〔", "【"))
    assert NOTE_CLOSE_PUNCT == frozenset((")", "）", "」", "』", "》", "〉", "〕", "】"))
    assert is_note_open_punctuation("《") is True
    assert is_note_close_punctuation("》") is True
    assert is_note_open_punctuation("。") is False
    assert is_note_close_punctuation("。") is False
