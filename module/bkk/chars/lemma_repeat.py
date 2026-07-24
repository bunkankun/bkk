"""Apply dictionary lemma-repeat placeholder substitutions."""

from __future__ import annotations

from typing import Any


LEMMA_REPEAT_MARKER = "substitution:lemma-repeat"
LEMMA_REPEAT_REASON = "lemma-repeat"
PLACEHOLDER = "丨"


class LemmaRepeatError(ValueError):
    """A dictionary voice span cannot drive lemma-repeat substitution."""


def apply_lemma_repeat_substitutions(
    text: str,
    markers: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Replace ``丨`` inside dictionary voice spans.

    Returns ``(new_text, kept_markers, emitted_markers)``. Existing markers are
    returned unchanged; emitted markers must be appended and sorted by the
    caller.
    """
    if not text or PLACEHOLDER not in text:
        return text, markers, []

    chars = list(text)
    emitted: list[dict[str, Any]] = []
    seen_offsets: dict[int, str] = {}

    voices = [
        marker for marker in markers
        if isinstance(marker, dict)
        and marker.get("type") == "voice"
        and marker.get("source") == "dictionary"
        and marker.get("name") in {"dict", "note"}
    ]
    voices.sort(key=lambda m: (_int_value(m.get("offset")), _int_value(m.get("length"))))

    for voice in voices:
        start = _required_int(voice, "offset")
        length = _required_int(voice, "length")
        lemma = voice.get("lemma")
        if not isinstance(lemma, str) or not lemma:
            raise LemmaRepeatError(f"dictionary voice missing lemma: {voice!r}")
        lemma_chars = list(lemma)
        if start < 0 or length < 0 or start + length > len(chars):
            raise LemmaRepeatError(f"dictionary voice outside text bounds: {voice!r}")

        placeholder_index = 0
        for offset in range(start, start + length):
            if chars[offset] != PLACEHOLDER:
                continue
            replacement = lemma_chars[placeholder_index % len(lemma_chars)]
            placeholder_index += 1
            prior = seen_offsets.get(offset)
            if prior is not None:
                if prior != replacement:
                    raise LemmaRepeatError(
                        f"conflicting lemma-repeat replacements at offset {offset}: "
                        f"{prior!r} vs {replacement!r}"
                    )
                continue
            seen_offsets[offset] = replacement
            chars[offset] = replacement
            marker: dict[str, Any] = {
                "type": LEMMA_REPEAT_MARKER,
                "offset": offset,
                "original": PLACEHOLDER,
                "replacement": replacement,
                "reason": LEMMA_REPEAT_REASON,
                "lemma": lemma,
            }
            for key in ("lemma_offset", "lemma_length", "id"):
                if key in voice:
                    marker[key if key != "id" else "voice_id"] = voice[key]
            emitted.append(marker)

    return "".join(chars), markers, emitted


def _required_int(marker: dict[str, Any], key: str) -> int:
    value = marker.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise LemmaRepeatError(f"dictionary voice has invalid {key}: {marker!r}")
    return value


def _int_value(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0
