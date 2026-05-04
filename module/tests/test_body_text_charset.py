"""Body-text charset invariant.

Every juan body's ``text`` field is the canonical character stream of the
juan. It must contain only CJK ideographs and BKK PUA codepoints — anything
else (org-mode headings, comment lines, punctuation, layout whitespace)
belongs in markers, not text.

Two tiers:

* synthetic — feed ``_parse_juan_text`` a hand-built mandoku-view juan with
  every kind of structural line and assert the resulting ``section.text`` is
  CJK-only and the structural lines surfaced as their typed markers.

* integration — walk the KR3a0013 round-trip output (built by the existing
  ``out_root`` fixture in ``test_krp_roundtrip``) and assert every juan's
  ``front``/``body``/``back`` ``text`` field is CJK-only.

The ``_is_allowed_body_char`` predicate is the source of truth for the future
BKK validator (see VALIDATOR.md); kept narrow on purpose.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from bkk.importer.pua import PUA_BASE, PUA_END
from bkk.importer.read.krp import _parse_juan_text

from .test_krp_roundtrip import out_root  # noqa: F401  (pytest fixture)


def _is_allowed_body_char(ch: str) -> bool:
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


def _first_offender(s: str) -> tuple[int, str] | None:
    for i, ch in enumerate(s):
        if not _is_allowed_body_char(ch):
            return i, ch
    return None


# ---------- Tier A: synthetic juan -----------------------------------------


_SYNTHETIC_JUAN = (
    "# -*- coding: utf-8 -*-\n"
    "#+TITLE: 試験\n"
    "#+PROPERTY: ID KRT0001\n"
    "#+PROPERTY: JUAN 0\n"
    "<pb:KRT0001_TEST_001-1a>¶\n"
    "** 1 第一章\n"
    "\n"
    "道可道，¶\n"
    "非恒道。¶\n"
    "\n"
    "# src: synthetic source note\n"
    "# dating: 8120\n"
    "名可名，¶\n"
    "非恒名。¶\n"
)


def test_synthetic_body_is_cjk_only():
    juan = _parse_juan_text(_SYNTHETIC_JUAN, juan_seq=1, text_id="KRT0001",
                            imglist={})
    sec = juan.sections[0]
    offender = _first_offender(sec.text)
    assert offender is None, (
        f"body text contains non-CJK char {offender[1]!r} "
        f"(U+{ord(offender[1]):04X}) at offset {offender[0]}: "
        f"{sec.text!r}"
    )


def test_synthetic_body_extracts_structure_to_markers():
    juan = _parse_juan_text(_SYNTHETIC_JUAN, juan_seq=1, text_id="KRT0001",
                            imglist={})
    sec = juan.sections[0]
    types = [m.type for m in sec.markers]
    # The structural lines all surfaced as typed markers.
    assert "head" in types
    assert types.count("comment") == 2
    # And so did punctuation extracted from content lines.
    assert "punctuation" in types


# ---------- Tier B: integration against KR3a0013 round-trip ----------------


def test_kr3a0013_body_text_is_cjk_only(out_root: Path):  # noqa: F811
    """Every text bucket in every juan of KR3a0013 contains only CJK + PUA."""
    bad: list[str] = []
    for juan_path in sorted(out_root.glob("KR3a0013_*.yaml")):
        if juan_path.name.endswith(".manifest.yaml"):
            continue
        juan = yaml.safe_load(juan_path.read_text(encoding="utf-8"))
        for bucket in ("front", "body", "back"):
            section = juan.get(bucket)
            if not section:
                continue
            text = section.get("text", "")
            offender = _first_offender(text)
            if offender is not None:
                idx, ch = offender
                bad.append(
                    f"{juan_path.name}/{bucket} offset {idx}: "
                    f"{ch!r} (U+{ord(ch):04X})"
                )
    assert not bad, "non-CJK chars in body text:\n  " + "\n  ".join(bad)
