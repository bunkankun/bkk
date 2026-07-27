from __future__ import annotations

from bkk.voice.derive_dictionary import derive_dictionary_voice_markers


def _note(offset: int, length: int, marker_id: str = "n1") -> dict:
    return {"type": "voice", "offset": offset, "length": length, "name": "note", "id": marker_id}


def _lemma_voices(voices: list[dict]) -> list[dict]:
    return [voice for voice in voices if voice.get("name") == "lemma"]


def _lemma_tuples(text: str, voices: list[dict]) -> list[tuple[str, int, int, str]]:
    return [
        (
            voice["id"],
            voice["offset"],
            voice["length"],
            text[voice["offset"]:voice["offset"] + voice["length"]],
        )
        for voice in _lemma_voices(voices)
    ]


def test_dictionary_derives_lemma_and_def_voice_from_generic_note() -> None:
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
        {
            "type": "voice",
            "offset": len(prefix),
            "length": len(note_text),
            "name": "def",
            "id": "n1",
            "source": "dictionary",
            "responds-to": "dl1",
            "lemma": "南東",
            "lemma_offset": len("韻藻"),
            "lemma_length": 2,
        },
    ]


def test_dictionary_uses_placeholder_count_for_separated_placeholders() -> None:
    prefix = "礬頭多"
    note_text = "畫史巨然明潤最有爽氣丨丨太丨"
    text = prefix + note_text
    markers = [_note(len(prefix), len(note_text))]

    voices = derive_dictionary_voice_markers(text, markers)

    assert _lemma_tuples(text, voices) == [
        ("dl1", 0, 3, "礬頭多"),
    ]


def test_dictionary_uses_common_placeholder_count_across_quotations() -> None:
    prefix = "燈影多"
    note_text = "漢書上元以丨丨丨者為上宋史其相勝丨丨丨也"
    text = prefix + note_text
    markers = [_note(len(prefix), len(note_text))]

    voices = derive_dictionary_voice_markers(text, markers)

    assert _lemma_tuples(text, voices) == [
        ("dl1", 0, 3, "燈影多"),
    ]


def test_dictionary_treats_you_separated_placeholder_groups_as_references() -> None:
    first_lemma = "南東"
    first_note = "詩丨丨其畝"
    second_lemma = "自東"
    second_note = "詩我來丨丨又自西丨丨"
    text = first_lemma + first_note + second_lemma + second_note
    markers = [
        _note(len(first_lemma), len(first_note), "n1"),
        _note(len(first_lemma) + len(first_note) + len(second_lemma), len(second_note), "n2"),
    ]

    voices = derive_dictionary_voice_markers(text, markers)

    assert _lemma_tuples(text, voices) == [
        ("dl1", 0, 2, "南東"),
        ("dl2", len(first_lemma) + len(first_note), 2, "自東"),
    ]


def test_dictionary_single_placeholder_references_do_not_shrink_longer_lemma() -> None:
    prefix = "一東"
    note_text = "漢書少陽在丨方丨動也禮記大明生於丨又姓陶潛友丨不訾"
    text = prefix + note_text
    markers = [_note(len(prefix), len(note_text))]

    voices = derive_dictionary_voice_markers(text, markers)

    assert _lemma_tuples(text, voices) == [
        ("dl1", 0, 2, "一東"),
    ]


def test_dictionary_phonetic_description_derives_single_character_lemma() -> None:
    prefix = "一東"
    note_text = "德紅切眷方也漢書少陽在丨方丨動也"
    text = prefix + note_text
    markers = [_note(len(prefix), len(note_text))]

    voices = derive_dictionary_voice_markers(text, markers)

    assert _lemma_tuples(text, voices) == [
        ("dl1", 1, 1, "東"),
    ]


def test_dictionary_derives_same_final_gap_without_placeholders() -> None:
    first_lemma = "首陽東"
    first_note = "詩丨丨丨"
    second_lemma = "畝盡東"
    second_note = "左傳晉人曰必使齊之封內盡東其畝"
    text = first_lemma + first_note + second_lemma + second_note
    markers = [
        _note(len(first_lemma), len(first_note), "n1"),
        _note(len(first_lemma) + len(first_note) + len(second_lemma), len(second_note), "n2"),
    ]

    voices = derive_dictionary_voice_markers(text, markers)

    assert _lemma_tuples(text, voices) == [
        ("dl1", 0, 3, "首陽東"),
        ("dl2", len(first_lemma) + len(first_note), 3, "畝盡東"),
    ]


def test_dictionary_does_not_derive_unmarked_gap_with_different_final() -> None:
    first_lemma = "南東"
    first_note = "詩丨丨其畝"
    second_lemma = "異西"
    second_note = "左傳無占位符"
    text = first_lemma + first_note + second_lemma + second_note
    markers = [
        _note(len(first_lemma), len(first_note), "n1"),
        _note(len(first_lemma) + len(first_note) + len(second_lemma), len(second_note), "n2"),
    ]

    voices = derive_dictionary_voice_markers(text, markers)

    assert _lemma_tuples(text, voices) == [
        ("dl1", 0, 2, "南東"),
    ]


def test_dictionary_splits_rhyme_source_quotations() -> None:
    prefix = "一歌"
    note_text = "古俄􅋀人聲曰丨歌者柯也韻㑹合樂曰丨"
    text = prefix + note_text
    markers = [_note(len(prefix), len(note_text))]

    voices = derive_dictionary_voice_markers(text, markers)

    assert _lemma_tuples(text, voices) == [
        ("dl1", 1, 1, "歌"),
    ]


def test_dictionary_trims_default_side_label_from_lemma() -> None:
    prefix = "韻藻載歌"
    note_text = "書乃丨載丨曰元首明哉沈佺期詩丨丨樂嵗豐"
    text = prefix + note_text
    markers = [_note(len(prefix), len(note_text))]

    voices = derive_dictionary_voice_markers(text, markers)

    assert _lemma_tuples(text, voices) == [
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
        for voice in _lemma_voices(voices)
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

    assert _lemma_tuples(text, voices) == [
        ("dl1", 0, 2, "甲乙"),
    ]


def test_dictionary_rederives_from_existing_def_metadata() -> None:
    text = "北東書丨丨"
    markers = [
        {
            "type": "voice",
            "offset": 2,
            "length": 3,
            "name": "def",
            "id": "n1",
            "source": "dictionary",
            "lemma": "北東",
            "lemma_offset": 0,
            "lemma_length": 2,
        },
    ]

    voices = derive_dictionary_voice_markers(text, markers)

    assert voices == [
        {
            "type": "voice",
            "offset": 0,
            "length": 2,
            "name": "lemma",
            "id": "dl1",
            "source": "dictionary",
        },
        {
            "type": "voice",
            "offset": 2,
            "length": 3,
            "name": "def",
            "id": "n1",
            "source": "dictionary",
            "responds-to": "dl1",
            "lemma": "北東",
            "lemma_offset": 0,
            "lemma_length": 2,
        },
    ]
