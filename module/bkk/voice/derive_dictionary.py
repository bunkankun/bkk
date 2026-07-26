"""Derive dictionary lemma voice markers from existing note voices.

Dictionary-like KRP texts print the lemma in the default text and the
explanation in note text. Inside those notes, U+4E28 (``丨``) abbreviates
characters from the preceding lemma. This pass runs after generic ``note``
voices have been derived and marks only the default-text lemma span.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from bkk.importer.charset import is_allowed_body_char


PLACEHOLDER = "丨"

_SOURCE_CUES = tuple(sorted({
    "唐韻", "廣韻", "集韻", "韻會", "韻㑹", "正韻", "玉篇", "類篇",
    "說文", "説文",
    "後漢書", "舊唐書", "春秋左傳", "春秋", "漢書", "唐書", "宋史",
    "遼史", "史記", "魏志", "魏書", "周書", "梁書", "南史", "北史",
    "隋書", "齊書", "宋書", "晉書", "周禮", "儀禮", "禮記", "爾雅",
    "論語", "孟子", "管子", "莊子", "列子", "韓非子", "淮南子",
    "抱朴子", "文心雕龍", "潛夫論", "參同契", "易林", "法言",
    "通考", "國䇿", "戰國策", "新序", "山海經", "本草", "楚辭",
    "書", "詩",
}, key=len, reverse=True))

_MAX_LEMMA_LEN = 4

_LABELS = tuple(sorted((
    "補藻", "補注", "補音", "補義", "補遺", "補正", "韻藻",
    "補", "藻", "增",
), key=len, reverse=True))


@dataclass(frozen=True)
class _Span:
    start: int
    end: int


def derive_dictionary_voice_markers(
    text: str,
    markers: list[dict],
) -> list[dict]:
    """Return dictionary ``lemma`` voice markers.

    Input notes are ordinary ``voice`` markers with ``name="note"``. For each
    note containing ``丨``, the placeholder pattern in the note estimates how
    many immediately preceding default-text characters make up the lemma.
    """
    if not text or PLACEHOLDER not in text:
        return []

    notes = _note_spans(markers, len(text))
    if not notes:
        return []

    existing_lemmas = _existing_dictionary_lemma_spans(markers)
    emitted_spans: set[tuple[int, int]] = set()
    out: list[dict] = []
    counter = 0

    for index, note in enumerate(notes):
        note_text = text[note.start:note.end]
        if PLACEHOLDER not in note_text:
            continue
        length = _infer_lemma_length(note_text)
        if length is None or length > note.start:
            continue

        lemma_start = note.start - length
        lemma_end = note.start
        if index > 0 and lemma_start < notes[index - 1].end:
            continue
        if _overlaps_any(lemma_start, lemma_end, existing_lemmas):
            continue

        lemma_start, lemma = _trim_label(lemma_start, text[lemma_start:lemma_end])
        length = lemma_end - lemma_start
        if not _valid_lemma(lemma):
            continue

        key = (lemma_start, lemma_end)
        if key in emitted_spans:
            continue
        emitted_spans.add(key)
        counter += 1
        out.append({
            "type": "voice",
            "offset": lemma_start,
            "length": length,
            "name": "lemma",
            "id": f"dl{counter}",
            "source": "dictionary",
        })

    return out


def _note_spans(markers: list[dict], text_len: int) -> list[_Span]:
    spans: list[_Span] = []
    for marker in markers:
        if (
            isinstance(marker, dict)
            and marker.get("type") == "voice"
            and marker.get("name") == "note"
        ):
            start = marker.get("offset")
            length = marker.get("length")
            if (
                isinstance(start, int)
                and not isinstance(start, bool)
                and isinstance(length, int)
                and not isinstance(length, bool)
                and 0 <= start <= text_len
                and 0 <= length
            ):
                spans.append(_Span(start, min(text_len, start + length)))
    return sorted(spans, key=lambda span: (span.start, span.end))


def _existing_dictionary_lemma_spans(markers: list[dict]) -> list[_Span]:
    spans: list[_Span] = []
    for marker in markers:
        if not (
            isinstance(marker, dict)
            and marker.get("type") == "voice"
            and marker.get("name") == "lemma"
            and marker.get("source") == "dictionary"
        ):
            continue
        start = marker.get("offset")
        length = marker.get("length")
        if (
            isinstance(start, int)
            and not isinstance(start, bool)
            and isinstance(length, int)
            and not isinstance(length, bool)
            and length >= 0
        ):
            spans.append(_Span(start, start + length))
    return spans


def _infer_lemma_length(note_text: str) -> int | None:
    counts = [
        min(segment.count(PLACEHOLDER), _MAX_LEMMA_LEN)
        for segment in _quotation_segments(note_text)
        if PLACEHOLDER in segment
    ]
    counts = [count for count in counts if count > 0]
    if not counts:
        return None
    by_frequency = Counter(counts)
    best_frequency = max(by_frequency.values())
    best = [count for count, frequency in by_frequency.items() if frequency == best_frequency]
    if len(best) != 1:
        return None
    return best[0]


def _quotation_segments(note_text: str) -> list[str]:
    starts = _source_cue_starts(note_text)
    if not starts:
        return [note_text]
    if starts[0] != 0:
        starts.insert(0, 0)
    starts.append(len(note_text))
    return [
        note_text[start:end]
        for start, end in zip(starts, starts[1:])
        if end > start
    ]


def _source_cue_starts(note_text: str) -> list[int]:
    found: list[tuple[int, int]] = []
    for cue in _SOURCE_CUES:
        start = 0
        while True:
            pos = note_text.find(cue, start)
            if pos < 0:
                break
            found.append((pos, len(cue)))
            start = pos + 1
    found.sort(key=lambda item: (item[0], -item[1]))

    starts: list[int] = []
    occupied_until = -1
    for pos, cue_len in found:
        if pos < occupied_until:
            continue
        starts.append(pos)
        occupied_until = pos + cue_len
    return starts


def _overlaps_any(start: int, end: int, spans: list[_Span]) -> bool:
    return any(start < span.end and span.start < end for span in spans)


def _valid_lemma(lemma: str) -> bool:
    return (
        bool(lemma)
        and PLACEHOLDER not in lemma
        and all(is_allowed_body_char(ch) for ch in lemma)
    )


def _trim_label(start: int, lemma: str) -> tuple[int, str]:
    for label in _LABELS:
        if lemma.startswith(label):
            return start + len(label), lemma[len(label):]
    return start, lemma
