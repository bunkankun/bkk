"""Step 5 of the canonicalization procedure: substitution against the
declared canonical character set.

Steps 1-4 (UTF-8 source, entity expansion, NFC, layout extraction) are
already applied by the importers. This module walks an existing
post-step-4 text stream and, for every codepoint that is outside the
canonical character set, looks up its canonical replacement in the
declared mapping(s) and rewrites the stream. Each rewrite produces a
``substitution`` marker pinned to the offset at which the original
character used to sit.

v1 supports only 1:1 codepoint substitutions: a single source codepoint
is replaced by a single canonical codepoint. ids-collapse and other
multi-codepoint cases are out of scope; the canonicalizer aborts if a
mapping entry would require a length change. This is enough to cover
the variant-fold mapping that ships with the project, and it keeps
existing marker offsets in subsequent positions valid without shifting.
"""

from __future__ import annotations

from typing import Any

from .refs import CanonicalizationContext, MappingEntry


SUBSTITUTION_REASON = "scribal-variant-collapsed"


class UnmappedCodepointError(ValueError):
    """A codepoint outside the canonical set has no mapping entry."""

    def __init__(self, codepoint: int, offset: int):
        self.codepoint = codepoint
        self.offset = offset
        super().__init__(
            f"codepoint U+{codepoint:04X} at offset {offset} is outside the "
            f"canonical character set and has no mapping entry"
        )


def _build_substitution_marker(
    offset: int,
    original_cp: int,
    entry: MappingEntry,
    ctx: CanonicalizationContext,
) -> dict[str, Any]:
    mapping = ctx.mappings[entry.mapping_index]
    return {
        "type": "substitution",
        "offset": offset,
        "original": chr(original_cp),
        "replacement": chr(entry.replacement_cp),
        "reason": SUBSTITUTION_REASON,
        "mapping": {
            "identifier": mapping.canonical_identifier,
            "hash": mapping.hash,
            "entry": entry.entry_id,
        },
    }


def canonicalize_text(
    text: str,
    ctx: CanonicalizationContext,
) -> tuple[str, list[dict[str, Any]]]:
    """Return the canonicalized text plus any substitution markers emitted.

    Raises :class:`UnmappedCodepointError` if a codepoint is outside the
    inclusion blocks and has no entry in any loaded mapping. The caller
    is expected to surface the bundle / juan / bucket context.
    """
    if not text:
        return text, []

    out: list[str] = []
    markers: list[dict[str, Any]] = []

    for offset, ch in enumerate(text):
        cp = ord(ch)
        entry = ctx.mapping_entries.get(cp)
        if entry is not None:
            replacement = chr(entry.replacement_cp)
            if len(replacement) != 1:
                raise RuntimeError(
                    f"mapping entry {entry.entry_id!r} is not 1:1 "
                    f"(replacement length {len(replacement)}); v1 of "
                    f"bkk chars canonicalize only supports 1:1 substitutions"
                )
            out.append(replacement)
            markers.append(_build_substitution_marker(offset, cp, entry, ctx))
            continue

        if ctx.in_inclusion_block(cp) and cp not in ctx.excluded:
            out.append(ch)
            continue

        if cp in ctx.excluded:
            # Excluded but no mapping entry: this is a configuration error.
            # The charset's `excluded` list and the substitution mapping(s)
            # should be aligned.
            raise UnmappedCodepointError(cp, offset)

        # Outside all inclusion blocks and no mapping match.
        raise UnmappedCodepointError(cp, offset)

    return "".join(out), markers
