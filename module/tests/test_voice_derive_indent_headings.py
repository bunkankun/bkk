"""Unit tests for tractat heading derivation from CJK indents."""

from __future__ import annotations

from bkk.voice.derive_indent_headings import (
    derive_voice_markers_from_indent_headings,
    has_indent_heading_profile,
)


def _lb(offset: int) -> dict:
    return {"type": "line-break", "offset": offset, "content": "", "id": ""}


def _indent(offset: int, depth: int) -> dict:
    return {"type": "indent", "offset": offset, "content": "\u3000" * depth, "id": ""}


def _punct(offset: int, content: str) -> dict:
    return {"type": "punctuation", "offset": offset, "content": content, "id": ""}


def _note(offset: int, length: int) -> dict:
    return {"type": "voice", "offset": offset, "length": length, "name": "note", "id": ""}


def test_kr3a0013_style_headings_are_derived() -> None:
    text = "傅子晉傅玄撰正心篇本文仁論篇"
    markers = [
        _lb(0), _indent(0, 1),          # 傅子
        _lb(2), _indent(2, 3),          # 晉傅玄撰 — attribution, rejected
        _lb(6), _indent(6, 2),          # 正心篇
        _lb(9),                         # 本文
        _lb(11), _indent(11, 2),        # 仁論篇
    ]

    out = derive_voice_markers_from_indent_headings(len(text), markers, text)

    assert out == [
        {
            "type": "voice",
            "offset": 0,
            "length": 2,
            "name": "label",
            "id": "h1",
            "source": "indent-headings",
            "indent_depth": 1,
        },
        {
            "type": "voice",
            "offset": 6,
            "length": 3,
            "name": "head",
            "id": "h2",
            "source": "indent-headings",
            "indent_depth": 2,
            "path": [1],
        },
        {
            "type": "voice",
            "offset": 11,
            "length": 3,
            "name": "head",
            "id": "h3",
            "source": "indent-headings",
            "indent_depth": 2,
            "path": [2],
        },
    ]


def test_toc_rows_with_internal_indents_are_rejected() -> None:
    text = "傅子目録儒家類正心仁論義信通志"
    markers = [
        _lb(0), _indent(0, 1), _indent(4, 7),
        _lb(7), _indent(7, 2), _indent(9, 7),
        _lb(11), _indent(11, 2), _indent(13, 7),
    ]

    assert derive_voice_markers_from_indent_headings(len(text), markers, text) == []
    assert has_indent_heading_profile(len(text), markers, text) is False


def test_later_depth_two_internal_indent_is_not_toc_after_regular_headings() -> None:
    text = "前一篇正文前二篇正文同王十三維哭殷遙儲光羲正文"
    markers = [
        _lb(0), _indent(0, 2),
        _lb(3),
        _lb(5), _indent(5, 2),
        _lb(8),
        _lb(10), _indent(10, 2), _indent(18, 5),
        _lb(21),
    ]

    out = derive_voice_markers_from_indent_headings(len(text), markers, text)

    assert [
        (marker["offset"], marker["length"], marker["indent_depth"], marker.get("path"))
        for marker in out
    ] == [
        (0, 3, 2, [1]),
        (5, 3, 2, [2]),
        (10, 8, 2, [3]),
    ]
    assert has_indent_heading_profile(len(text), markers, text) is True


def test_long_prefatory_prose_and_deep_indent_are_rejected() -> None:
    text = "臣等謹案傅子晉司𨽻校尉鶉觚子北地傅玄撰正心篇"
    markers = [
        _lb(0), _indent(0, 4),
        _lb(19), _indent(19, 2),
    ]

    out = derive_voice_markers_from_indent_headings(len(text), markers, text)

    assert out == [
        {
            "type": "voice",
            "offset": 19,
            "length": 3,
            "name": "head",
            "id": "h1",
            "source": "indent-headings",
            "indent_depth": 2,
            "path": [1],
        },
    ]


def test_punctuation_bearing_toc_entry_is_rejected() -> None:
    text = "附録四十八條正心篇"
    markers = [
        _lb(0), _indent(0, 2), _punct(2, "("), _punct(4, "/"), _punct(6, ")"),
        _lb(6), _indent(6, 2),
    ]

    out = derive_voice_markers_from_indent_headings(len(text), markers, text)

    assert [marker["offset"] for marker in out] == [6]


def test_attached_note_does_not_hide_heading_text() -> None:
    text = "正心篇一本作正心正文西施詠河嶽英靈集"
    markers = [
        _lb(0), _indent(0, 2),
        _punct(3, "("), _note(3, 5), _punct(5, "/"), _punct(8, ")"),
        _lb(8),
        _lb(10), _indent(10, 2),
        _punct(13, "("), _note(13, 5), _punct(15, "/"), _punct(18, ")"),
    ]

    out = derive_voice_markers_from_indent_headings(len(text), markers, text)

    assert [
        (marker["offset"], marker["length"], marker["indent_depth"])
        for marker in out
    ] == [
        (0, 3, 2),
        (10, 3, 2),
    ]
    assert has_indent_heading_profile(len(text), markers, text) is True


def test_depth_one_commentary_lemma_before_note_is_rejected() -> None:
    text = "春過賀遂員外藥園藥園唐李華賀遂員外藥園小山池記"
    markers = [
        _lb(0), _indent(0, 2),
        _lb(8), _indent(8, 1),
        _punct(10, "("), _note(10, 21), _punct(31, ")"),
    ]

    out = derive_voice_markers_from_indent_headings(len(text), markers, text)

    assert [
        (marker["offset"], marker["length"], marker["indent_depth"])
        for marker in out
    ] == [
        (0, 8, 2),
    ]


def test_depth_one_commentary_lemma_between_notes_is_rejected() -> None:
    text = "前文善卷後文"
    markers = [
        _lb(0), _indent(0, 1),
        _punct(2, ")"), _note(0, 2),
        _punct(4, "("), _note(4, 2),
    ]

    assert derive_voice_markers_from_indent_headings(len(text), markers, text) == []


def test_short_line_inside_note_is_rejected() -> None:
    text = "前文詩之後後文"
    markers = [
        _lb(2), _indent(2, 3),
        _lb(5),
        _punct(4, "/"),
        _note(0, 5),
    ]

    assert derive_voice_markers_from_indent_headings(len(text), markers, text) == []


def test_later_depth_one_title_like_line_after_section_is_rejected() -> None:
    text = "近體詩十六首春過賀遂員外藥園槿籬一本作槿籬"
    markers = [
        _lb(0), _indent(0, 1),
        _lb(6), _indent(6, 2),
        _lb(14), _indent(14, 1),
    ]

    out = derive_voice_markers_from_indent_headings(len(text), markers, text)

    assert [
        (marker["offset"], marker["length"], marker["indent_depth"])
        for marker in out
    ] == [
        (0, 6, 1),
        (6, 8, 2),
    ]


def test_long_depth_two_line_is_heading() -> None:
    text = "河南嚴尹弟見宿弊廬訪别人賦十韻本文"
    markers = [
        _lb(0), _indent(0, 2),
        _lb(15),
    ]

    out = derive_voice_markers_from_indent_headings(len(text), markers, text)

    assert [
        (marker["offset"], marker["length"], marker["indent_depth"])
        for marker in out
    ] == [
        (0, 15, 2),
    ]


def test_depth_two_heading_overrun_merges_next_depth_two_line() -> None:
    text = "送祕書朝監還日本國并序還極元集唐詩品彚俱無國字正文"
    markers = [
        _lb(0), _indent(0, 2),
        _lb(16), _indent(16, 2),
        _lb(26),
    ]

    out = derive_voice_markers_from_indent_headings(len(text), markers, text)

    assert [
        (marker["offset"], marker["length"], marker["indent_depth"])
        for marker in out
    ] == [
        (0, 26, 2),
    ]


def test_early_one_indent_count_line_is_section_heading() -> None:
    text = "王右丞集箋注卷一仁和趙殿成撰古詩十首奉和聖製天長節賜宰臣歌應制"
    markers = [
        _lb(0), _indent(0, 1),
        _lb(8), _indent(8, 12),
        _lb(14), _indent(14, 1),
        _lb(18), _indent(18, 2),
    ]

    out = derive_voice_markers_from_indent_headings(len(text), markers, text)

    assert [
        (marker["offset"], marker["length"], marker["indent_depth"])
        for marker in out
    ] == [
        (0, 8, 1),
        (14, 4, 1),
        (18, 13, 2),
    ]
    assert has_indent_heading_profile(len(text), markers, text) is True


def test_single_initial_level_one_heading_is_label_not_citation_path() -> None:
    text = "王右丞集箋注卷二積雨輞川莊作正文"
    markers = [
        _lb(0), _indent(0, 1),
        _lb(8), _indent(8, 2),
    ]

    out = derive_voice_markers_from_indent_headings(len(text), markers, text)

    assert [
        (marker["offset"], marker["name"], marker["indent_depth"], marker.get("path"))
        for marker in out
    ] == [
        (0, "label", 1, None),
        (8, "head", 2, [1]),
    ]


def test_juan_starter_and_single_category_heading_are_labels() -> None:
    text = "王右丞集箋注卷十近體詩二十六首奉和聖製從蓬萊正文"
    markers = [
        _lb(0), _indent(0, 1),
        _lb(8), _indent(8, 1),
        _lb(15), _indent(15, 2),
        _lb(25),
    ]

    out = derive_voice_markers_from_indent_headings(len(text), markers, text)

    assert [
        (marker["offset"], marker["name"], marker["indent_depth"], marker.get("path"))
        for marker in out
    ] == [
        (0, "label", 1, None),
        (8, "label", 1, None),
        (15, "head", 2, [1]),
    ]


def test_juan_starter_without_indent_is_label() -> None:
    text = "王右丞集箋注卷十二仁和趙殿成撰近體詩十六首春過賀遂員外藥園正文"
    markers = [
        _lb(15), _indent(15, 1),
        _lb(21), _indent(21, 2),
        _lb(29),
    ]

    out = derive_voice_markers_from_indent_headings(len(text), markers, text)

    assert [
        (marker["offset"], marker["length"], marker["name"], marker.get("path"))
        for marker in out
    ] == [
        (0, 9, "label", None),
        (15, 6, "label", None),
        (21, 8, "head", [1]),
    ]


def test_closing_title_repeat_is_skipped() -> None:
    text = "傅子正心篇傅子"
    markers = [
        _lb(0), _indent(0, 1),
        _lb(2), _indent(2, 2),
        _lb(5), _indent(5, 1),
    ]

    out = derive_voice_markers_from_indent_headings(len(text), markers, text)

    assert [(marker["offset"], marker["length"]) for marker in out] == [
        (0, 2),
        (2, 3),
    ]
    assert has_indent_heading_profile(len(text), markers, text) is True


def test_depth_three_short_heads_without_suffix_are_derived() -> None:
    text = "夏殷春秋正文周春秋"
    markers = [
        _lb(0), _indent(0, 3),
        _lb(4),
        _lb(6), _indent(6, 3),
    ]

    out = derive_voice_markers_from_indent_headings(len(text), markers, text)

    assert [
        (marker["offset"], marker["length"], marker["indent_depth"])
        for marker in out
    ] == [
        (0, 4, 3),
        (6, 3, 3),
    ]


def test_depth_three_internal_indent_splits_heading_line() -> None:
    text = "吳越春秋越絶書宋春秋齊春秋"
    markers = [
        _lb(0), _indent(0, 3), _indent(4, 1),
        _lb(7), _indent(7, 3), _indent(10, 1),
    ]

    out = derive_voice_markers_from_indent_headings(len(text), markers, text)

    assert [
        (marker["offset"], marker["length"], marker["indent_depth"])
        for marker in out
    ] == [
        (0, 4, 3),
        (4, 3, 3),
        (7, 3, 3),
        (10, 3, 3),
    ]


def test_depth_three_split_heading_allows_embedded_note_punctuation() -> None:
    text = "漢晉春秋見編年漢魏春秋"
    markers = [
        _lb(0), _indent(0, 3), _punct(4, "("), _punct(6, "/"), _punct(7, ")"),
        _indent(7, 1),
    ]

    out = derive_voice_markers_from_indent_headings(len(text), markers, text)

    assert [
        (marker["offset"], marker["length"], marker["indent_depth"])
        for marker in out
    ] == [
        (0, 7, 3),
        (7, 4, 3),
    ]
