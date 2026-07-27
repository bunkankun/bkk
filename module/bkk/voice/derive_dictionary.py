"""Derive dictionary lemma and definition voice markers from note voices.

Dictionary-like KRP texts print the lemma in the default text and the
explanation in note text. Inside those notes, U+4E28 (``丨``) abbreviates
characters from the preceding lemma. This pass runs after generic ``note``
voices have been derived and records the note as a dictionary definition
responding to the default-text lemma span.
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


@dataclass(frozen=True)
class _DefinitionSpan:
    start: int
    end: int
    marker: dict


def derive_dictionary_voice_markers(
    text: str,
    markers: list[dict],
) -> list[dict]:
    """Return dictionary ``lemma`` and ``def`` voice markers.

    Input notes are ordinary ``voice`` markers with ``name="note"``. Already
    migrated dictionary ``def`` markers may also be used as inputs for
    rederivation. For each matched definition, the placeholder pattern in the
    definition estimates how many immediately preceding default-text
    characters make up the lemma.
    """
    if not text:
        return []

    definitions = _definition_spans(markers, len(text))
    if not definitions:
        return []

    existing_lemmas = _existing_dictionary_lemma_spans(markers)
    emitted_spans: set[tuple[int, int]] = set()
    out: list[dict] = []
    counter = 0
    previous_lemma = ""
    existing_index = 0
    emitted_ids: set[str] = set()
    existing_lemmas = sorted(existing_lemmas, key=lambda span: (span.start, span.end))

    for index, definition in enumerate(definitions):
        while (
            existing_index < len(existing_lemmas)
            and existing_lemmas[existing_index].end <= definition.start
        ):
            span = existing_lemmas[existing_index]
            previous_lemma = text[span.start:span.end]
            existing_index += 1

        previous_definition_end = definitions[index - 1].end if index > 0 else 0
        candidate = _candidate_from_existing_definition(text, definition)
        if candidate is None:
            candidate = _candidate_from_phonetic_description(
                text, definition, previous_definition_end,
            )
        if candidate is None:
            candidate = _candidate_from_placeholders(
                text, definition, previous_definition_end,
            )
        if candidate is None:
            candidate = _candidate_from_same_final(
                text, previous_definition_end, definition.start, previous_lemma,
            )
        if candidate is None:
            continue

        lemma_start, lemma_end = candidate.start, candidate.end
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
        lemma_id = f"dl{counter}"
        def_id = _definition_id(definition.marker, counter, emitted_ids | {lemma_id})
        emitted_ids.update({lemma_id, def_id})
        out.append({
            "type": "voice",
            "offset": lemma_start,
            "length": length,
            "name": "lemma",
            "id": lemma_id,
            "source": "dictionary",
        })
        out.append({
            "type": "voice",
            "offset": definition.start,
            "length": definition.end - definition.start,
            "name": "def",
            "id": def_id,
            "source": "dictionary",
            "responds-to": lemma_id,
            "lemma": lemma,
            "lemma_offset": lemma_start,
            "lemma_length": length,
        })
        previous_lemma = lemma

    return out


def _definition_spans(markers: list[dict], text_len: int) -> list[_DefinitionSpan]:
    spans: list[_DefinitionSpan] = []
    for marker in markers:
        if not (
            isinstance(marker, dict)
            and marker.get("type") == "voice"
            and _is_dictionary_definition_input(marker)
        ):
            continue
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
            spans.append(_DefinitionSpan(start, min(text_len, start + length), marker))
    return sorted(spans, key=lambda span: (span.start, span.end))


def _is_dictionary_definition_input(marker: dict) -> bool:
    name = marker.get("name")
    source = marker.get("source")
    if name == "note":
        return source in (None, "dictionary")
    return name in {"def", "dict"} and source == "dictionary"


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
        min(group.count(PLACEHOLDER), _MAX_LEMMA_LEN)
        for segment in _quotation_segments(note_text)
        for group in _reference_groups(segment)
        if PLACEHOLDER in group
    ]
    counts = [count for count in counts if count > 0]
    if any(count > 1 for count in counts):
        counts = [count for count in counts if count > 1]
    if not counts:
        return None
    by_frequency = Counter(counts)
    best_frequency = max(by_frequency.values())
    best = [count for count, frequency in by_frequency.items() if frequency == best_frequency]
    if len(best) != 1:
        return None
    return best[0]


def _candidate_from_existing_definition(
    text: str, definition: _DefinitionSpan,
) -> _Span | None:
    marker = definition.marker
    lemma_offset = marker.get("lemma_offset")
    lemma_length = marker.get("lemma_length")
    if not (
        isinstance(lemma_offset, int)
        and not isinstance(lemma_offset, bool)
        and isinstance(lemma_length, int)
        and not isinstance(lemma_length, bool)
    ):
        return None
    lemma_end = lemma_offset + lemma_length
    if lemma_offset < 0 or lemma_length <= 0 or lemma_end > len(text):
        return None
    lemma = text[lemma_offset:lemma_end]
    if not _valid_lemma(lemma):
        return None
    return _Span(lemma_offset, lemma_end)


def _candidate_from_phonetic_description(
    text: str, note: _DefinitionSpan, previous_note_end: int,
) -> _Span | None:
    note_text = text[note.start:note.end]
    if len(note_text) < 3 or note_text[2] != "切":
        return None
    lemma_start = note.start - 1
    if lemma_start < previous_note_end:
        return None
    lemma = text[lemma_start:note.start]
    if not _valid_lemma(lemma):
        return None
    return _Span(lemma_start, note.start)


def _candidate_from_placeholders(
    text: str, note: _DefinitionSpan, previous_note_end: int,
) -> _Span | None:
    note_text = text[note.start:note.end]
    if PLACEHOLDER not in note_text:
        return None
    length = _infer_lemma_length(note_text)
    if length is None or length > note.start:
        return None
    lemma_start = note.start - length
    if lemma_start < previous_note_end:
        return None
    return _Span(lemma_start, note.start)


def _candidate_from_same_final(
    text: str, start: int, end: int, previous_lemma: str,
) -> _Span | None:
    if not previous_lemma or start >= end:
        return None
    lemma_start, lemma = _trim_label(start, text[start:end])
    if len(lemma) > _MAX_LEMMA_LEN or not _valid_lemma(lemma):
        return None
    if lemma[-1] != previous_lemma[-1]:
        return None
    return _Span(lemma_start, end)


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


def _reference_groups(segment: str) -> list[str]:
    """Split repeated references within one source segment.

    In rhyme dictionaries, ``又`` regularly introduces another citation for
    the same lemma. Placeholder counts on either side should be read as
    separate references, not added together into one overlong lemma.
    """
    return [group for group in segment.split("又") if group]


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


def _definition_id(marker: dict, counter: int, occupied: set[str]) -> str:
    marker_id = marker.get("id")
    if isinstance(marker_id, str) and marker_id and marker_id not in occupied:
        return marker_id
    candidate = f"dd{counter}"
    if candidate not in occupied:
        return candidate
    suffix = counter + 1
    while True:
        candidate = f"dd{suffix}"
        if candidate not in occupied:
            return candidate
        suffix += 1
