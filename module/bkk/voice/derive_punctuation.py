"""Derive named voice markers from punctuation marker pairs.

This source is intentionally separate from the legacy ``parens`` deriver:
``parens`` emits generic note/emphasis spans, while ``punctuation`` is a
source-tagged rule set for semantic punctuation pairs that may grow over time.
"""

from __future__ import annotations

from .derive import VoiceDerivationProblem


PUNCTUATION_VOICE_SOURCE = "punctuation"

_OPENERS = {
    "《": ("》", "title", "t"),
}


def derive_voice_markers_from_punctuation(
    text_len: int,
    markers: list[dict],
) -> list[dict]:
    """Return ``source=punctuation`` voice markers derived from punctuation.

    Each ``《``...``》`` pair currently produces one ``name=title`` voice span.
    The returned span uses the same offset convention as the paren deriver:
    ``offset`` at the opener marker and ``length`` up to the closer marker.
    """
    out, problems = derive_voice_markers_from_punctuation_best_effort(
        text_len, markers,
    )
    if problems:
        raise problems[0]
    return out


def derive_voice_markers_from_punctuation_best_effort(
    text_len: int,
    markers: list[dict],
) -> tuple[list[dict], list[VoiceDerivationProblem]]:
    """Return recoverable punctuation voices and localized problems."""
    punct: list[tuple[int, str, int]] = []
    problems: list[VoiceDerivationProblem] = []
    closers = {closer for closer, _, _ in _OPENERS.values()}
    supported = set(_OPENERS) | closers
    for index, marker in enumerate(markers):
        if not isinstance(marker, dict):
            continue
        if marker.get("type") != "punctuation":
            continue
        ch = marker.get("content")
        if ch not in supported:
            continue
        offset = marker.get("offset")
        if not isinstance(offset, int) or isinstance(offset, bool):
            problems.append(
                VoiceDerivationProblem(
                    "punctuation-offset",
                    f"punctuation marker missing integer offset: {marker}",
                    offset=0,
                )
            )
            continue
        if offset < 0 or offset > text_len:
            continue
        punct.append((offset, ch, index))

    if not punct:
        return [], problems

    punct.sort(key=lambda item: (item[0], item[2]))

    spans: list[tuple[int, int, str, str]] = []
    open_span: tuple[int, str, str, str] | None = None
    expected_closer: str | None = None
    for offset, ch, _index in punct:
        if open_span is None:
            if ch in closers:
                problems.append(
                    VoiceDerivationProblem(
                        "stray-close",
                        f"unexpected '{ch}' at offset {offset} with no matching opener",
                        offset=offset,
                    )
                )
                continue
            closer, name, prefix = _OPENERS[ch]
            open_span = (offset, ch, name, prefix)
            expected_closer = closer
            continue

        open_offset, open_ch, name, prefix = open_span
        if ch == expected_closer:
            spans.append((open_offset, offset, name, prefix))
            open_span = None
            expected_closer = None
            continue

        if ch in _OPENERS:
            problems.append(
                VoiceDerivationProblem(
                    "expected-close",
                    f"expected '{expected_closer}' after '{open_ch}' at offset "
                    f"{open_offset}, got '{ch}' at offset {offset}",
                    offset=open_offset,
                    length=max(0, offset - open_offset),
                )
            )
            closer, next_name, next_prefix = _OPENERS[ch]
            open_span = (offset, ch, next_name, next_prefix)
            expected_closer = closer
            continue

        problems.append(
            VoiceDerivationProblem(
                "wrong-close",
                f"expected '{expected_closer}' after '{open_ch}' at offset "
                f"{open_offset}, got '{ch}' at offset {offset}",
                offset=open_offset,
                length=max(0, offset - open_offset),
            )
        )

    if open_span is not None:
        open_offset, open_ch, _name, _prefix = open_span
        problems.append(
            VoiceDerivationProblem(
                "unmatched-open",
                f"unmatched '{open_ch}' at offset {open_offset}",
                offset=open_offset,
            )
        )

    counters: dict[str, int] = {}
    out: list[dict] = []
    for open_offset, close_offset, name, prefix in spans:
        counters[prefix] = counters.get(prefix, 0) + 1
        out.append({
            "type": "voice",
            "offset": open_offset,
            "length": close_offset - open_offset,
            "name": name,
            "id": f"{prefix}{counters[prefix]}",
            "source": PUNCTUATION_VOICE_SOURCE,
        })
    return out, problems
