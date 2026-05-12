"""Derive ``voice`` markers from ``(`` / ``)`` punctuation pairs.

In KRP source layouts, double-column small-character commentary is fenced
by ``(`` … ``)`` (with ``/`` as a column-break inside). The KRP importer
extracts those as ``punctuation`` point markers so the source round-trips,
but the voice semantics — what is root, what is commentary — never make
it onto the canonical text stream. This module recovers them as
range-typed ``voice`` markers (see bunkankun.md §"Voices").
"""

from __future__ import annotations


def derive_voice_markers(
    text_len: int, markers: list[dict],
) -> list[dict]:
    """Return new ``voice`` marker dicts derived from ``(`` / ``)`` pairs.

    ``markers`` is the bucket's existing marker list (plain dicts as
    loaded from YAML); it is not mutated. ``/`` punctuation markers are
    column-break layout inside a commentary and are ignored.

    Returns an empty list when the bucket carries no ``(`` punctuation
    marker — voicing implies at least one commentary span, and an
    all-root bucket is left unmarked rather than wrapped in a single
    cover marker.

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
            raise ValueError(f"punctuation marker missing integer offset: {m}")
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
            raise ValueError(
                f"unexpected ')' at offset {o_off} with no matching '('"
            )
        if i + 1 >= n:
            raise ValueError(f"unmatched '(' at offset {o_off}")
        c_off, c_ch = parens[i + 1]
        if c_ch != ")":
            raise ValueError(
                f"expected ')' after '(' at offset {o_off}, "
                f"got '{c_ch}' at offset {c_off}"
            )
        if c_off < o_off:
            raise ValueError(
                f"')' offset {c_off} precedes '(' offset {o_off}"
            )
        spans.append((o_off, c_off))
        i += 2

    out: list[dict] = []
    cursor = 0
    n_root = 0
    n_cmt = 0
    prev_root_id: str | None = None
    for o_open, o_close in spans:
        if o_open > cursor:
            n_root += 1
            rid = f"r{n_root}"
            out.append({
                "type": "voice",
                "offset": cursor,
                "length": o_open - cursor,
                "name": "root",
                "id": rid,
            })
            prev_root_id = rid
        n_cmt += 1
        cid = f"c{n_cmt}"
        cmt: dict = {
            "type": "voice",
            "offset": o_open,
            "length": o_close - o_open,
            "name": "commentary",
            "id": cid,
        }
        if prev_root_id is not None:
            cmt["responds-to"] = prev_root_id
        out.append(cmt)
        cursor = o_close

    if cursor < text_len:
        n_root += 1
        rid = f"r{n_root}"
        out.append({
            "type": "voice",
            "offset": cursor,
            "length": text_len - cursor,
            "name": "root",
            "id": rid,
        })

    return out
