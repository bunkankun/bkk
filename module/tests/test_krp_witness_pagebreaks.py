"""Tests for the master/witness page-break merge in :mod:`bkk.importer.read.krp`.

The master bundle should carry every witness's page-breaks at aligned
offsets, with each marker keeping its own edition's image. Master entries
already present (typically the base edition's page-breaks) are not
duplicated.
"""

from __future__ import annotations

from bkk.importer.ir import Juan, Marker, Section
from bkk.importer.read.krp import (
    _attach_witness_page_breaks,
    _lookup_image,
    _parse_imglist_file,
)


def _juan_from_pages(pages: list[tuple[str, str]]) -> Juan:
    """Build a one-section Juan from ``[(page_id, page_text), ...]``.

    Page-breaks are placed at the offset where each page's text starts in
    the section's concatenated text — close enough to the real parser's
    output to exercise the alignment helper.
    """
    text_parts: list[str] = []
    markers: list[Marker] = []
    cursor = 0
    for page_id, page_text in pages:
        markers.append(Marker(
            type="page-break", offset=cursor, content="", id=page_id,
        ))
        text_parts.append(page_text)
        cursor += len(page_text)
    section = Section(
        head_text="t", head_marker_id=pages[0][0],
        text="".join(text_parts), markers=markers,
    )
    return Juan(seq=1, sections=[section], metadata={})


# ---------- imglist parser --------------------------------------------------


def test_imglist_parser_keys_by_edition():
    """SBCK and WYG entries for the same page id no longer collide."""
    text = (
        "001-1a00\tSBCK 001-1a\tSBCK/p1.png\n"
        "001-1a00\tWYG 001-1a\tWYG/p1.png\n"
    )
    out = _parse_imglist_file(text)
    assert out == {
        ("SBCK", "001-1a"): "SBCK/p1.png",
        ("WYG", "001-1a"): "WYG/p1.png",
    }


def test_lookup_image_picks_correct_edition():
    imglist = {
        ("SBCK", "001-1a"): "SBCK/p1.png",
        ("WYG", "001-1a"): "WYG/p1.png",
    }
    assert _lookup_image(imglist, "KR3a0001_SBCK_001-1a", "001-1a") == "SBCK/p1.png"
    assert _lookup_image(imglist, "KR3a0001_WYG_001-1a", "001-1a") == "WYG/p1.png"


def test_lookup_image_missing_returns_none():
    assert _lookup_image({}, "KR3a0001_WYG_001-1a", "001-1a") is None
    # Malformed id (no edition segment) → None instead of raising.
    assert _lookup_image({}, "weird-id", "001-1a") is None


# ---------- witness page-break injection ------------------------------------


def test_witness_page_break_injected_at_aligned_offset():
    master = _juan_from_pages([
        ("KR_X_001-1a", "alpha"),
        ("KR_X_001-1b", "bravo"),
    ])
    witness = _juan_from_pages([
        ("KR_Y_001-1a", "alpha"),
        ("KR_Y_001-1b", "bravo"),
    ])
    # Stash an image on the witness page-break so we can verify it travels.
    witness.sections[0].markers[1].extras["image"] = "Y/p1b.png"

    _attach_witness_page_breaks(master, witness)

    section = master.sections[0]
    pb_ids = [m.id for m in section.markers if m.type == "page-break"]
    # Master keeps its own page-breaks, plus both witness ones.
    assert pb_ids == [
        "KR_X_001-1a", "KR_X_001-1b",
        "KR_Y_001-1a", "KR_Y_001-1b",
    ]
    # Aligned to the same offsets the master uses.
    by_id = {m.id: m for m in section.markers if m.type == "page-break"}
    assert by_id["KR_Y_001-1a"].offset == 0
    assert by_id["KR_Y_001-1b"].offset == len("alpha")
    # Per-edition image carried through.
    assert by_id["KR_Y_001-1b"].extras["image"] == "Y/p1b.png"
    # Master's own marker untouched (no image was set).
    assert "image" not in by_id["KR_X_001-1b"].extras


def test_dedup_skips_ids_already_present():
    """Base-edition page-breaks already in the master must not duplicate."""
    master = _juan_from_pages([
        ("KR_BASE_001-1a", "alpha"),
        ("KR_BASE_001-1b", "bravo"),
    ])
    witness = _juan_from_pages([
        ("KR_BASE_001-1a", "alpha"),
        ("KR_BASE_001-1b", "bravo"),
    ])
    # Witness has an image; master does not. Dedup keeps master's marker
    # as-is — we don't overwrite with witness data.
    witness.sections[0].markers[0].extras["image"] = "BASE/p1a.png"

    _attach_witness_page_breaks(master, witness)

    pbs = [m for m in master.sections[0].markers if m.type == "page-break"]
    assert [m.id for m in pbs] == ["KR_BASE_001-1a", "KR_BASE_001-1b"]
    assert "image" not in pbs[0].extras


def test_witness_offset_snaps_through_diverging_text():
    """Witness page-break inside a divergence snaps to the master span start."""
    master = _juan_from_pages([
        ("KR_X_001-1a", "alpha"),       # offsets 0..4
        ("KR_X_001-1b", "DIFFERS_HERE"),  # offsets 5..16
    ])
    witness = _juan_from_pages([
        ("KR_Y_001-1a", "alpha"),
        ("KR_Y_001-1b", "elsewise"),  # different content, different length
    ])

    _attach_witness_page_breaks(master, witness)

    pbs = {m.id: m for m in master.sections[0].markers if m.type == "page-break"}
    # The aligned page-break (KR_Y_001-1a) lands exactly on offset 0.
    assert pbs["KR_Y_001-1a"].offset == 0
    # The diverging witness page-break snaps to the start of the master's
    # diverging span (offset 5).
    assert pbs["KR_Y_001-1b"].offset == 5
