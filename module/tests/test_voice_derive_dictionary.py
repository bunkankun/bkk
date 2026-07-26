from __future__ import annotations

from bkk.voice.derive_dictionary import derive_dictionary_voice_markers


def _note(offset: int, length: int, marker_id: str = "n1") -> dict:
    return {"type": "voice", "offset": offset, "length": length, "name": "note", "id": marker_id}


def test_dictionary_derives_lemma_voice_from_generic_note() -> None:
    prefix = "韻藻南東"
    note_text = "詩丨丨其畝"
    text = prefix + note_text
    markers = [_note(len(prefix), len(note_text))]

    voices = derive_dictionary_voice_markers(text, markers)

    assert voices == [
        {
            "type": "voice",
            "offset": len("韻藻"),
            "length": 2,
            "name": "lemma",
            "id": "dl1",
            "source": "dictionary",
        },
    ]


def test_dictionary_uses_placeholder_count_for_separated_placeholders() -> None:
    prefix = "礬頭多"
    note_text = "畫史巨然明潤最有爽氣丨丨太丨"
    text = prefix + note_text
    markers = [_note(len(prefix), len(note_text))]

    voices = derive_dictionary_voice_markers(text, markers)

    assert [
        (voice["id"], voice["offset"], voice["length"], text[voice["offset"]:voice["offset"] + voice["length"]])
        for voice in voices
    ] == [
        ("dl1", 0, 3, "礬頭多"),
    ]


def test_dictionary_uses_common_placeholder_count_across_quotations() -> None:
    prefix = "燈影多"
    note_text = "漢書上元以丨丨丨者為上宋史其相勝丨丨丨也"
    text = prefix + note_text
    markers = [_note(len(prefix), len(note_text))]

    voices = derive_dictionary_voice_markers(text, markers)

    assert [
        (voice["id"], voice["offset"], voice["length"], text[voice["offset"]:voice["offset"] + voice["length"]])
        for voice in voices
    ] == [
        ("dl1", 0, 3, "燈影多"),
    ]


def test_dictionary_splits_rhyme_source_quotations() -> None:
    prefix = "一歌"
    note_text = "古俄􅋀人聲曰丨歌者柯也韻㑹合樂曰丨"
    text = prefix + note_text
    markers = [_note(len(prefix), len(note_text))]

    voices = derive_dictionary_voice_markers(text, markers)

    assert [
        (voice["id"], voice["offset"], voice["length"], text[voice["offset"]:voice["offset"] + voice["length"]])
        for voice in voices
    ] == [
        ("dl1", 1, 1, "歌"),
    ]


def test_dictionary_trims_default_side_label_from_lemma() -> None:
    prefix = "韻藻載歌"
    note_text = "書乃丨載丨曰元首明哉沈佺期詩丨丨樂嵗豐"
    text = prefix + note_text
    markers = [_note(len(prefix), len(note_text))]

    voices = derive_dictionary_voice_markers(text, markers)

    assert [
        (voice["id"], voice["offset"], voice["length"], text[voice["offset"]:voice["offset"] + voice["length"]])
        for voice in voices
    ] == [
        ("dl1", len("韻藻"), 2, "載歌"),
    ]


def test_dictionary_skips_ambiguous_quotation_counts() -> None:
    prefix = "燈影多"
    note_text = "漢書上元以丨丨丨者為上宋史其相勝丨丨也"
    text = prefix + note_text
    markers = [_note(len(prefix), len(note_text))]

    voices = derive_dictionary_voice_markers(text, markers)

    assert voices == []


def test_dictionary_dedupes_repeated_note_targets() -> None:
    prefix = "北東"
    first = "書導沇水丨丨"
    second = "詩沂之丨丨"
    text = prefix + first + second
    markers = [
        _note(len(prefix), len(first), "n1"),
        _note(len(prefix), len(first) + len(second), "n2"),
    ]

    voices = derive_dictionary_voice_markers(text, markers)

    assert [
        (voice["id"], voice["offset"], voice["length"])
        for voice in voices
    ] == [
        ("dl1", 0, 2),
    ]


def test_dictionary_skips_span_crossing_previous_note() -> None:
    text = "甲乙書丨丨丙丁書丨丨"
    markers = [
        _note(2, 4, "n1"),
        _note(7, 4, "n2"),
    ]

    voices = derive_dictionary_voice_markers(text, markers)

    assert [
        (voice["id"], voice["offset"], voice["length"], text[voice["offset"]:voice["offset"] + voice["length"]])
        for voice in voices
    ] == [
        ("dl1", 0, 2, "甲乙"),
    ]
