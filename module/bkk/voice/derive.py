"""Derive ``voice`` markers from source punctuation pairs.

In KRP source layouts, double-column small-character text is fenced by
``(`` … ``)`` (with ``/`` as a column-break inside). The KRP importer
extracts those as ``punctuation`` point markers so the source round-trips,
but the voice semantics — what kind of layer the fenced text belongs to —
never make it onto the canonical text stream.

Paren-bounded text serves many purposes in the corpus: commentator gloss,
editorial note, alternate reading, source citation, phonological annotation.
A deriver that sees only the punctuation can't disambiguate these. This
module therefore makes the minimal, defensible claim: it emits one
``voice`` marker per paren span with ``name="note"``, and leaves the
non-paren text unvoiced. Some sources use ``▲`` as an opener for emphasized
text closed by ``)``; those spans are emitted with ``name="emphasis"``.
Anything stronger (e.g. classifying the note as ``commentary`` and the
surrounding text as ``root``) belongs to the indent deriver — which has the
layout signal — or to a downstream pass that consults external context.
"""

from __future__ import annotations


_OPENERS = {
    "(": ("note", "n"),
    "▲": ("emphasis", "e"),
}


class VoiceDerivationProblem(ValueError):
    """Location-aware problem that blocks voice derivation for a bucket."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        offset: int,
        length: int = 0,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.offset = offset
        self.length = length
        self.message = message


def derive_voice_markers(
    text_len: int, markers: list[dict],
) -> list[dict]:
    """Return new ``voice`` marker dicts derived from source punctuation.

    ``markers`` is the bucket's existing marker list (plain dicts as
    loaded from YAML); it is not mutated. ``/`` punctuation markers are
    column-break layout inside a paren span and are ignored.

    Each ``(...)`` pair produces a single ``voice`` marker with
    ``name="note"`` covering the offsets from the opener through the
    closer (inclusive of both). If a close paren and the next open paren
    share the same offset, the touching runs are treated as one continuous
    note rather than two adjacent notes. Each ``▲...)`` pair produces an
    ``emphasis`` marker. Ids are assigned per-bucket and per voice prefix
    as ``n1``, ``n2``, … and ``e1``, ``e2``, ….

    Returns an empty list when the bucket carries no supported opener
    punctuation marker.

    Raises :class:`ValueError` if the ``(``/``)`` pairing is malformed.
    """
    parens: list[tuple[int, str, int]] = []
    for index, m in enumerate(markers):
        if not isinstance(m, dict):
            continue
        if m.get("type") != "punctuation":
            continue
        ch = m.get("content")
        if ch not in (*_OPENERS, ")"):
            continue
        off = m.get("offset")
        if not isinstance(off, int):
            raise VoiceDerivationProblem(
                "punctuation-offset",
                f"punctuation marker missing integer offset: {m}",
                offset=0,
            )
        parens.append((off, ch, index))

    if not parens:
        return []

    parens.sort(key=lambda p: (p[0], p[2]))

    groups: list[tuple[int, list[str]]] = []
    for off, ch, _ in parens:
        if groups and groups[-1][0] == off:
            groups[-1][1].append(ch)
        else:
            groups.append((off, [ch]))

    spans: list[tuple[int, int, str, str]] = []
    open_span: tuple[int, str, str, str] | None = None
    for group_index, (off, chars) in enumerate(groups):
        opener = _first_opener(chars)
        has_close = ")" in chars
        if open_span is None:
            if has_close and opener is None:
                raise VoiceDerivationProblem(
                    "stray-close",
                    f"unexpected ')' at offset {off} with no matching '('",
                    offset=off,
                )
            if opener is None:
                continue
            name, prefix = _OPENERS[opener]
            if has_close:
                spans.append((off, off, name, prefix))
                continue
            open_span = (off, opener, name, prefix)
            continue

        open_off, open_ch, name, prefix = open_span
        if has_close:
            if name == "note" and "(" in chars and _has_later_close(groups, group_index):
                continue
            spans.append((open_off, off, name, prefix))
            open_span = None
            if opener is not None:
                next_name, next_prefix = _OPENERS[opener]
                open_span = (off, opener, next_name, next_prefix)
            continue

        if opener is not None:
            raise VoiceDerivationProblem(
                "expected-close",
                f"expected ')' after '{open_ch}' at offset {open_off}, "
                f"got '{opener}' at offset {off}",
                offset=open_off,
                length=max(0, off - open_off),
            )

    if open_span is not None:
        open_off, open_ch, _, _ = open_span
        raise VoiceDerivationProblem(
            "unmatched-open",
            f"unmatched '{open_ch}' at offset {open_off}",
            offset=open_off,
        )

    counters: dict[str, int] = {}
    out: list[dict] = []
    for o_open, o_close, name, prefix in spans:
        counters[prefix] = counters.get(prefix, 0) + 1
        out.append({
            "type": "voice",
            "offset": o_open,
            "length": o_close - o_open,
            "name": name,
            "id": f"{prefix}{counters[prefix]}",
        })
    return out


def _first_opener(chars: list[str]) -> str | None:
    for ch in chars:
        if ch in _OPENERS:
            return ch
    return None


def _has_later_close(groups: list[tuple[int, list[str]]], group_index: int) -> bool:
    return any(")" in chars for _, chars in groups[group_index + 1:])
