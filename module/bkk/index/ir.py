"""Result types returned by :class:`bkk.index.Index.search`."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VariantOverlay:
    """A variant reading whose master span intersects a KWIC window."""

    master_offset: int
    length: int
    content: str
    witness: str
    witness_form: str


@dataclass(frozen=True)
class Hit:
    """One KWIC match.

    ``master_offset``/``master_length`` are normalized to the established
    reading regardless of which text the substring was actually found in;
    ``matched_via`` is ``"master"`` for direct hits or the witness short id
    (e.g. ``"SBCK"``) for variant-mediated hits, and ``matched_text`` carries
    the substring as it appeared in the matched source text. ``overlays``
    lists every variant entry whose master span intersects the KWIC window
    so the renderer can display them alongside the master line.
    """

    textid: str
    juan_seq: int
    bucket: str
    master_offset: int
    master_length: int
    matched_via: str
    matched_text: str
    left: str
    match: str
    right: str
    overlays: tuple[VariantOverlay, ...]
    toc_label: str | None
