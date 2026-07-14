"""Derive ``voice`` markers from ``(`` / ``)`` punctuation pairs.

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
non-paren text unvoiced. Anything stronger (e.g. classifying the note as
``commentary`` and the surrounding text as ``root``) belongs to the
indent deriver — which has the layout signal — or to a downstream pass
that consults external context.
"""

from __future__ import annotations


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
    """Return new ``voice`` marker dicts derived from ``(`` / ``)`` pairs.

    ``markers`` is the bucket's existing marker list (plain dicts as
    loaded from YAML); it is not mutated. ``/`` punctuation markers are
    column-break layout inside a paren span and are ignored.

    Each ``(...)`` pair produces a single ``voice`` marker with
    ``name="note"`` covering the offsets from the opener through the
    closer (inclusive of both). If a close paren and the next open paren
    share the same offset, the touching runs are treated as one continuous
    note rather than two adjacent notes. Ids are assigned per-bucket as
    ``n1``, ``n2``, ….

    Returns an empty list when the bucket carries no ``(`` punctuation
    marker.

    Raises :class:`ValueError` if the ``(``/``)`` pairing is malformed.
    """
    parens: list[tuple[int, str, int]] = []
    for index, m in enumerate(markers):
        if not isinstance(m, dict):
            continue
        if m.get("type") != "punctuation":
            continue
        ch = m.get("content")
        if ch not in ("(", ")"):
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

    spans: list[tuple[int, int]] = []
    open_off: int | None = None
    for group_index, (off, chars) in enumerate(groups):
        has_open = "(" in chars
        has_close = ")" in chars
        if open_off is None:
            if has_close and not has_open:
                raise VoiceDerivationProblem(
                    "stray-close",
                    f"unexpected ')' at offset {off} with no matching '('",
                    offset=off,
                )
            if has_open and has_close:
                spans.append((off, off))
                continue
            open_off = off
            continue

        if has_close and has_open:
            if _has_later_close(groups, group_index):
                continue
            spans.append((open_off, off))
            open_off = off
            continue

        if has_close:
            spans.append((open_off, off))
            open_off = None
            continue

        if has_open:
            raise VoiceDerivationProblem(
                "expected-close",
                f"expected ')' after '(' at offset {open_off}, "
                f"got '(' at offset {off}",
                offset=open_off,
                length=max(0, off - open_off),
            )

    if open_off is not None:
        raise VoiceDerivationProblem(
            "unmatched-open",
            f"unmatched '(' at offset {open_off}",
            offset=open_off,
        )

    out: list[dict] = []
    for i, (o_open, o_close) in enumerate(spans, 1):
        out.append({
            "type": "voice",
            "offset": o_open,
            "length": o_close - o_open,
            "name": "note",
            "id": f"n{i}",
        })
    return out


def _has_later_close(groups: list[tuple[int, list[str]]], group_index: int) -> bool:
    return any(")" in chars for _, chars in groups[group_index + 1:])
