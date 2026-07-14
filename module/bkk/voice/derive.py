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
    closer (inclusive of both). Ids are assigned per-bucket as ``n1``,
    ``n2``, ….

    Returns an empty list when the bucket carries no ``(`` punctuation
    marker.

    Raises :class:`ValueError` if the ``(``/``)`` pairing is malformed.
    """
    parens: list[tuple[int, str]] = []
    for m in markers:
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
        parens.append((off, ch))

    if not parens:
        return []

    parens.sort(key=lambda p: p[0])

    spans: list[tuple[int, int]] = []
    i = 0
    n = len(parens)
    while i < n:
        o_off, o_ch = parens[i]
        if o_ch != "(":
            raise VoiceDerivationProblem(
                "stray-close",
                f"unexpected ')' at offset {o_off} with no matching '('",
                offset=o_off,
            )
        if i + 1 >= n:
            raise VoiceDerivationProblem(
                "unmatched-open",
                f"unmatched '(' at offset {o_off}",
                offset=o_off,
            )
        c_off, c_ch = parens[i + 1]
        if c_ch != ")":
            raise VoiceDerivationProblem(
                "expected-close",
                f"expected ')' after '(' at offset {o_off}, "
                f"got '{c_ch}' at offset {c_off}",
                offset=o_off,
                length=max(0, c_off - o_off),
            )
        if c_off < o_off:
            raise VoiceDerivationProblem(
                "close-before-open",
                f"')' offset {c_off} precedes '(' offset {o_off}",
                offset=c_off,
            )
        spans.append((o_off, c_off))
        i += 2

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
