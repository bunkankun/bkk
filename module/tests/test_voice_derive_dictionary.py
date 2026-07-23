from __future__ import annotations

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
