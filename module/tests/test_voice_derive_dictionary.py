from __future__ import annotations

from bkk.chars.lemma_repeat import apply_lemma_repeat_substitutions
from bkk.voice.derive_dictionary import derive_dictionary_voice_markers


def _lb(offset: int) -> dict:
    return {"type": "line-break", "offset": offset, "content": "", "id": ""}


def test_dictionary_derives_note_spans_with_lemmas_across_lines() -> None:
    line1 = "東唐韻德紅切"
    line2 = "補藻北東書導沇水又丨丨入海師曰丨折而丨也"
    line3 = "同玉篇集韻徒東切"
    line4 = "補藻僉同書詢謀丨丨議者丨丨"
    text = line1 + line2 + line3 + line4
    markers = [
        _lb(0),
        _lb(len(line1)),
        _lb(len(line1) + len(line2)),
        _lb(len(line1) + len(line2) + len(line3)),
    ]

    voices = derive_dictionary_voice_markers(text, markers)

    assert [
        (voice["lemma"], voice["offset"], voice["length"])
        for voice in voices
    ] == [
        ("北東", len(line1) + len("補藻北東"), len("書導沇水又丨丨入海師曰丨折而丨也")),
        ("僉同", len(line1) + len(line2) + len(line3) + len("補藻僉同"), len("書詢謀丨丨議者丨丨")),
    ]
    assert all(voice["source"] == "dictionary" for voice in voices)
    assert [voice["id"] for voice in voices] == ["dn1", "dn2"]


def test_dictionary_derives_peiwen_head_gloss_and_yunzao_entries() -> None:
    line1 = "上平聲一東韻一"
    line2 = "東德紅切眷方也漢書少陽在丨方丨動也禮記大明生於丨"
    line3 = "韻藻南東詩丨丨其畝李孝先詩沂之丨丨自東詩我來丨丨"
    text = line1 + line2 + line3
    markers = [
        _lb(0),
        _lb(len(line1)),
        _lb(len(line1) + len(line2)),
    ]

    voices = derive_dictionary_voice_markers(text, markers)

    assert [
        (voice["lemma"], voice["offset"], voice["length"])
        for voice in voices
    ] == [
        ("東", len(line1) + len("東德紅切"), len("眷方也漢書少陽在丨方丨動也禮記大明生於丨")),
        ("南東", len(line1) + len(line2) + len("韻藻南東"), len("詩丨丨其畝李孝先詩沂之丨丨")),
        ("自東", len(line1) + len(line2) + len("韻藻南東詩丨丨其畝李孝先詩沂之丨丨自東"), len("詩我來丨丨")),
    ]

    substituted, _markers, emitted = apply_lemma_repeat_substitutions(text, voices)

    assert "漢書少陽在東方東動也禮記大明生於東" in substituted
    assert "南東其畝李孝先詩沂之南東" in substituted
    assert "自東" in {marker["lemma"] for marker in emitted}


def test_dictionary_trims_single_zao_label() -> None:
    line1 = "上平聲一東韻三"
    line2 = "穹去宮切説文窮也廣韻又髙也"
    line3 = "藻皓穹漢書鞏自丨丨"
    text = line1 + line2 + line3
    markers = [
        _lb(0),
        _lb(len(line1)),
        _lb(len(line1) + len(line2)),
    ]

    voices = derive_dictionary_voice_markers(text, markers)

    assert [
        (voice["lemma"], voice["offset"], voice["length"])
        for voice in voices
    ] == [
        ("皓穹", len(line1) + len(line2) + len("藻皓穹"), len("漢書鞏自丨丨")),
    ]


def test_dictionary_prefers_two_character_compound_for_separated_placeholders() -> None:
    line1 = "上平聲一東韻四"
    line2 = "功古紅切説文以勞定國曰丨廣韻丨績也"
    line3 = "韻藻試功書明丨以丨車服以庸"
    text = line1 + line2 + line3
    markers = [
        _lb(0),
        _lb(len(line1)),
        _lb(len(line1) + len(line2)),
    ]

    voices = derive_dictionary_voice_markers(text, markers)

    assert [
        (voice["lemma"], voice["offset"], voice["length"])
        for voice in voices
    ] == [
        ("功", len(line1) + len("功古紅切"), len("説文以勞定國曰丨廣韻丨績也")),
        ("試功", len(line1) + len(line2) + len("韻藻試功"), len("書明丨以丨車服以庸")),
    ]
