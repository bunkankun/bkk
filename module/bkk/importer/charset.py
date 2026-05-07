"""Body-text charset invariant.

A juan body's ``text`` field is the canonical character stream and must
contain only CJK ideographs and BKK PUA codepoints. Anything else
(punctuation, layout whitespace, ASCII residue) belongs in markers.

This predicate is the single source of truth, used by:

* the KRP and TLS importers (when emitting body text from source)
* the body-text-charset validator
* the test suite
"""

from __future__ import annotations

from .pua import PUA_BASE, PUA_END


def is_allowed_body_char(ch: str) -> bool:
    """True iff ``ch`` is permitted in a juan body ``text`` field.

    The allowed set is CJK ideographs (Unified, Ext A, Ext B, Ext C–F,
    Compatibility) plus the BKK PUA range. Everything else — punctuation,
    indents, ASCII, comment text, headings — belongs in markers.
    """
    cp = ord(ch)
    return (
        0x4E00  <= cp <= 0x9FFF   or  # CJK Unified Ideographs
        0x3400  <= cp <= 0x4DBF   or  # CJK Ext A
        0x20000 <= cp <= 0x2A6DF  or  # CJK Ext B
        0x2A700 <= cp <= 0x2EBEF  or  # CJK Ext C–F
        0xF900  <= cp <= 0xFAFF   or  # CJK Compatibility
        PUA_BASE <= cp < PUA_END      # BKK PUA
    )
