"""Result types returned by :class:`bkk.index.Index.search`."""

from __future__ import annotations

from dataclasses import dataclass, field


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

    ``voice`` is the innermost voice name fully containing the hit span
    (``"root"``, ``"commentary"``, …); ``"mixed"`` if the span straddles
    voice boundaries with no single containing range; ``"none"`` if no
    voice range covers it (e.g. unmarked front matter). ``voice_stack``
    lists every fully-containing range's name, outermost → innermost, so
    callers can display a path like ``("commentary", "sound-gloss")``
    without re-querying.

    ``witness_left``/``witness_right`` carry KWIC context drawn from the
    witness text for witness-mediated hits — useful when a long variant
    reading replaces a short master span and the master KWIC window
    doesn't itself contain the matched substring. Empty for master hits.

    ``witness_left_variant_offset`` is the index within ``witness_left`` at
    which the variant content begins (everything before it is master/identity
    text — the same chars the master line shows). ``witness_right_variant_end``
    is the index within ``witness_right`` at which the variant content ends.
    Callers use these to split each side into ``anchor`` (master surroundings)
    and ``interior`` (variant chars) and to optionally collapse the interior
    for display.
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
    voice: str
    voice_stack: tuple[str, ...]
    witness_left: str = ""
    witness_right: str = ""
    witness_left_variant_offset: int = 0
    witness_right_variant_end: int = 0


@dataclass(frozen=True)
class IndexSummary:
    """Bird's-eye rollup of a query without materialising any :class:`Hit`.

    Used when a query would yield more matches than the server-side cap;
    powers the search "overview" UI so the user can narrow without ever
    paginating through tens of thousands of hits.

    Counts are summed over candidate positions (cheap, derivable from the
    trigram index plus light SQL joins) and may slightly overcount for
    queries of length ≥ 3 because trigram candidates aren't string-verified;
    for 2-char queries they are exact.
    """

    total: int
    by_textid: dict[str, int] = field(default_factory=dict)
    by_witness_label: dict[str, int] = field(default_factory=dict)
    trigram_left: list[tuple[str, int]] = field(default_factory=list)
    trigram_right: list[tuple[str, int]] = field(default_factory=list)
