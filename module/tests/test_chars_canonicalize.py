"""Tests for ``bkk chars canonicalize``."""

from __future__ import annotations

import pytest

from bkk.chars.canonicalize import (
    SUBSTITUTION_REASON,
    UnmappedCodepointError,
    canonicalize_text,
)
from bkk.chars.refs import (
    CanonicalizationContext,
    MappingAsset,
    MappingEntry,
    load_context,
)


def _toy_ctx() -> CanonicalizationContext:
    mapping = MappingAsset(
        canonical_identifier="bkk:mapping/test-v1",
        hash="sha256:" + "1" * 64,
        filename="test-mapping.yaml",
    )
    return CanonicalizationContext(
        charset_id="bkk:charset/test-v1",
        charset_hash="sha256:" + "2" * 64,
        charset_filename="test-charset.yaml",
        inclusion_blocks=[(0x4E00, 0x9FFF)],
        excluded={0x5434: {"reason": "kZVariant", "replaced_by": 0x5449}},
        mappings=[mapping],
        mapping_entries={
            0x5434: MappingEntry(
                entry_id="tf-0001",
                replacement_cp=0x5449,
                reason="kZVariant",
                mapping_index=0,
            ),
        },
    )


def test_canonicalize_no_substitutions():
    ctx = _toy_ctx()
    text = "周易"  # both chars are in CJK Unified
    new_text, markers = canonicalize_text(text, ctx)
    assert new_text == text
    assert markers == []


def test_canonicalize_replaces_excluded_codepoint():
    ctx = _toy_ctx()
    src = chr(0x5434)         # excluded (kZVariant)
    repl = chr(0x5449)        # canonical replacement
    text = "周" + src + "易"
    new_text, markers = canonicalize_text(text, ctx)
    assert new_text == "周" + repl + "易"
    assert len(markers) == 1
    m = markers[0]
    assert m["type"] == "substitution"
    assert m["offset"] == 1
    assert m["original"] == src
    assert m["replacement"] == repl
    assert m["reason"] == SUBSTITUTION_REASON
    assert m["mapping"]["identifier"] == "bkk:mapping/test-v1"
    assert m["mapping"]["entry"] == "tf-0001"
    assert m["mapping"]["hash"].startswith("sha256:")


def test_canonicalize_raises_on_unmapped_outside_set():
    ctx = _toy_ctx()
    # U+0041 'A' is outside the inclusion block and has no mapping entry.
    with pytest.raises(UnmappedCodepointError) as exc_info:
        canonicalize_text("周A易", ctx)
    assert exc_info.value.offset == 1
    assert exc_info.value.codepoint == 0x0041


def test_canonicalize_empty_text():
    ctx = _toy_ctx()
    assert canonicalize_text("", ctx) == ("", [])


def test_canonicalize_offsets_unchanged_for_1to1_replacements():
    """Two adjacent substitutions: each marker's offset is the position
    in the post-substitution text stream, which (since every replacement
    is 1:1) equals the position in the input stream."""
    ctx = _toy_ctx()
    src = chr(0x5434)
    repl = chr(0x5449)
    new_text, markers = canonicalize_text(src + src, ctx)
    assert new_text == repl + repl
    assert [m["offset"] for m in markers] == [0, 1]


def test_load_context_default_refs_dir():
    """The shipped charset and mapping load cleanly and self-verify."""
    ctx = load_context()
    assert ctx.charset_id == "bkk:charset/cjk-v1"
    assert ctx.charset_hash.startswith("sha256:")
    assert any(
        lo <= 0x4E00 <= hi for lo, hi in ctx.inclusion_blocks
    )  # CJK Unified
    assert any(
        lo <= 0x105000 <= hi for lo, hi in ctx.inclusion_blocks
    )  # BKK PUA
    assert 0x5434 in ctx.excluded
    assert 0x5434 in ctx.mapping_entries
    entry = ctx.mapping_entries[0x5434]
    assert entry.replacement_cp == 0x5449
    assert entry.entry_id.startswith("vf-")
    assert ctx.mappings[entry.mapping_index].canonical_identifier == (
        "bkk:mapping/variant-fold-v1"
    )


def test_load_context_real_charset_covers_excluded_with_mapping():
    """Every excluded codepoint in the bootstrap charset is resolvable
    through the shipped mapping, so canonicalize_text never raises
    UnmappedCodepointError for shipped corpus characters that are inside
    one of the inclusion blocks."""
    ctx = load_context()
    missing = [cp for cp in ctx.excluded if cp not in ctx.mapping_entries]
    assert missing == [], (
        f"{len(missing)} excluded codepoint(s) lack a mapping entry: "
        f"{['U+{:04X}'.format(cp) for cp in missing[:5]]}"
    )
