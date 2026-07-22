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


class InvalidSubstitutionMarkerError(ValueError):
    """A substitution marker cannot be applied to the current text."""


def _replacement_char(entry: MappingEntry) -> str:
    cp = entry.replacement_cp
    if (
        not isinstance(cp, int)
        or isinstance(cp, bool)
        or cp < 0
        or cp > 0x10FFFF
        or 0xD800 <= cp <= 0xDFFF
    ):
        raise RuntimeError(
            f"mapping entry {entry.entry_id!r} has invalid replacement "
            f"codepoint {cp!r}"
        )
    replacement = chr(cp)
    if len(replacement) != 1:
        raise RuntimeError(
            f"mapping entry {entry.entry_id!r} is not 1:1 "
            f"(replacement length {len(replacement)}); v1 of "
            f"bkk chars canonicalize only supports 1:1 substitutions"
        )
    return replacement


def _build_substitution_marker(
    offset: int,
    original_cp: int,
    entry: MappingEntry,
    ctx: CanonicalizationContext,
    replacement: str,
) -> dict[str, Any]:
    mapping = ctx.mappings[entry.mapping_index]
    return {
        "type": "substitution",
        "offset": offset,
        "original": chr(original_cp),
        "replacement": replacement,
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
            replacement = _replacement_char(entry)
            out.append(replacement)
            markers.append(
                _build_substitution_marker(offset, cp, entry, ctx, replacement)
            )
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


def canonicalize_text_lenient(
    text: str,
    ctx: CanonicalizationContext,
) -> tuple[str, list[dict[str, Any]], list[UnmappedCodepointError]]:
    """Like :func:`canonicalize_text`, but never raises on unmapped codepoints.

    Each unmapped codepoint is appended to the returned ``unmapped`` list
    (with its offset) and left in place in the output text. Mappable
    codepoints are still substituted and reported via ``markers`` exactly
    as in the strict variant. Used by ``bkk chars canonicalize`` when the
    caller wants a full survey of unmapped codepoints instead of aborting
    on the first occurrence.
    """
    if not text:
        return text, [], []

    out: list[str] = []
    markers: list[dict[str, Any]] = []
    unmapped: list[UnmappedCodepointError] = []

    for offset, ch in enumerate(text):
        cp = ord(ch)
        entry = ctx.mapping_entries.get(cp)
        if entry is not None:
            replacement = _replacement_char(entry)
            out.append(replacement)
            markers.append(
                _build_substitution_marker(offset, cp, entry, ctx, replacement)
            )
            continue

        if ctx.in_inclusion_block(cp) and cp not in ctx.excluded:
            out.append(ch)
            continue

        unmapped.append(UnmappedCodepointError(cp, offset))
        out.append(ch)

    return "".join(out), markers, unmapped


def canonicalize_query(text: str, ctx: CanonicalizationContext) -> str:
    """Step-5 substitution for search queries.

    Differs from :func:`canonicalize_text` in two ways: unmapped codepoints
    pass through unchanged (rather than raising) so a user-typed character
    outside the canonical set yields zero hits instead of a server error,
    and no substitution markers are produced. Assumes the caller has
    already NFC-normalized ``text``; mapping keys are NFC code points.
    """
    if not text:
        return text

    out: list[str] = []
    for ch in text:
        entry = ctx.mapping_entries.get(ord(ch))
        if entry is not None:
            out.append(_replacement_char(entry))
        else:
            out.append(ch)
    return "".join(out)


def revert_substitution_markers(
    text: str,
    markers: list[dict[str, Any]],
    *,
    allow_already_reverted: bool = False,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Undo ``substitution`` markers in ``text``.

    Returns ``(reverted_text, kept_markers, removed_markers)``. Each
    substitution marker is expected to describe a v1 1:1 replacement emitted
    by :func:`canonicalize_text`: ``text[offset]`` must currently be the
    marker's ``replacement`` character, and it is rewritten to ``original``.
    The marker itself is omitted from ``kept_markers``. If
    ``allow_already_reverted`` is true, a marker whose offset already contains
    ``original`` is treated as stale and removed without changing the text.
    """
    if not markers:
        return text, [], []

    chars = list(text)
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    seen_offsets: set[int] = set()

    for marker in markers:
        if not isinstance(marker, dict) or marker.get("type") != "substitution":
            if isinstance(marker, dict):
                kept.append(marker)
            continue

        offset = marker.get("offset")
        original = marker.get("original")
        replacement = marker.get("replacement")
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or not isinstance(original, str)
            or len(original) != 1
            or not isinstance(replacement, str)
            or len(replacement) != 1
        ):
            raise InvalidSubstitutionMarkerError(
                f"malformed substitution marker: {marker!r}"
            )
        if offset in seen_offsets:
            raise InvalidSubstitutionMarkerError(
                f"multiple substitution markers at offset {offset}"
            )
        if offset < 0 or offset >= len(chars):
            raise InvalidSubstitutionMarkerError(
                f"substitution marker offset {offset} is outside text length "
                f"{len(chars)}"
            )
        if chars[offset] == replacement:
            chars[offset] = original
        elif allow_already_reverted and chars[offset] == original:
            # The text was already restored by an earlier run, but a stale
            # marker asset still contains the substitution marker. Drop it.
            pass
        else:
            raise InvalidSubstitutionMarkerError(
                f"substitution marker at offset {offset} expects "
                f"{replacement!r}, found {chars[offset]!r}"
            )

        seen_offsets.add(offset)
        removed.append(marker)

    return "".join(chars), kept, removed
