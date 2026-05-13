"""Unit tests for indent-based voice derivation."""

from __future__ import annotations

from bkk.voice.derive_indent import (
    DEFAULT_INDENT_VOICE_MAP,
    derive_voice_markers_from_indent,
)


def _lb(offset: int) -> dict:
    return {"type": "line-break", "offset": offset, "content": "", "id": ""}


def _indent(offset: int, depth: int) -> dict:
    return {"type": "indent", "offset": offset, "content": "\u3000" * depth, "id": ""}


def test_empty_text_returns_no_markers():
    assert derive_voice_markers_from_indent(0, []) == []


def test_no_line_breaks_returns_no_markers():
    # An indent marker without a line-break opener carries no segment.
    markers = [_indent(0, 1)]
    assert derive_voice_markers_from_indent(10, markers) == []


def test_pure_root_one_span():
    # Three lines, all with depth-0 indent (i.e., no indent marker) merge
    # into one root span.
    markers = [_lb(0), _lb(5), _lb(10)]
    out = derive_voice_markers_from_indent(15, markers)
    assert out == [
        {"type": "voice", "offset": 0, "length": 15, "name": "root", "id": "r1"},
    ]


def test_pure_commentary_one_span_no_responds_to():
    # Commentary appears before any anchor — no responds-to.
    markers = [
        _lb(0), _indent(0, 1),
        _lb(8), _indent(8, 1),
    ]
    out = derive_voice_markers_from_indent(16, markers)
    assert out == [
        {"type": "voice", "offset": 0, "length": 16,
         "name": "commentary", "id": "c1"},
    ]


def test_root_then_commentary_responds_to_root():
    markers = [
        _lb(0),                       # root [0,10)
        _lb(10), _indent(10, 1),      # commentary [10,20)
    ]
    out = derive_voice_markers_from_indent(20, markers)
    assert out == [
        {"type": "voice", "offset": 0, "length": 10, "name": "root", "id": "r1"},
        {"type": "voice", "offset": 10, "length": 10,
         "name": "commentary", "id": "c1", "responds-to": "r1"},
    ]


def test_consecutive_commentary_lines_merge_into_one_span():
    # Three commentary lines between two roots merge.
    markers = [
        _lb(0),                       # root [0,5)
        _lb(5), _indent(5, 1),        # cmt
        _lb(10), _indent(10, 1),      # cmt
        _lb(15), _indent(15, 1),      # cmt — together [5,20)
        _lb(20),                      # root [20,25)
    ]
    out = derive_voice_markers_from_indent(25, markers)
    assert out == [
        {"type": "voice", "offset": 0, "length": 5, "name": "root", "id": "r1"},
        {"type": "voice", "offset": 5, "length": 15,
         "name": "commentary", "id": "c1", "responds-to": "r1"},
        {"type": "voice", "offset": 20, "length": 5, "name": "root", "id": "r2"},
    ]


def test_attribution_then_head_then_root_then_commentary():
    # Mirrors the opening of KR5c0095 juan 1.
    markers = [
        _lb(0), _indent(0, 4),         # attribution [0,9)
        _lb(9), _indent(9, 3),         # head        [9,15)
        _lb(15),                       # root        [15,27)
        _lb(27), _indent(27, 1),       # commentary  [27,40)
    ]
    out = derive_voice_markers_from_indent(40, markers)
    assert out == [
        {"type": "voice", "offset": 0, "length": 9,
         "name": "attribution", "id": "a1"},
        {"type": "voice", "offset": 9, "length": 6,
         "name": "head", "id": "h1", "responds-to": "a1"},
        {"type": "voice", "offset": 15, "length": 12,
         "name": "root", "id": "r1"},
        {"type": "voice", "offset": 27, "length": 13,
         "name": "commentary", "id": "c1", "responds-to": "r1"},
    ]


def test_commentary_attaches_to_head_when_no_root_before():
    # head [0,5) then commentary [5,10) — commentary responds to h1.
    markers = [
        _lb(0), _indent(0, 2),
        _lb(5), _indent(5, 1),
    ]
    out = derive_voice_markers_from_indent(10, markers)
    assert out == [
        {"type": "voice", "offset": 0, "length": 5,
         "name": "head", "id": "h1"},
        {"type": "voice", "offset": 5, "length": 5,
         "name": "commentary", "id": "c1", "responds-to": "h1"},
    ]


def test_unmapped_indent_depths_are_skipped():
    # depth 7 is not in the map.
    markers = [
        _lb(0),                        # root
        _lb(5), _indent(5, 7),         # unmapped — skipped
        _lb(10),                       # root again — adjacent run merges
    ]
    out = derive_voice_markers_from_indent(15, markers)
    # Two root segments (0-5) and (10-15) are *not* adjacent; the middle
    # segment was skipped, so they remain two separate runs.
    assert out == [
        {"type": "voice", "offset": 0, "length": 5, "name": "root", "id": "r1"},
        {"type": "voice", "offset": 10, "length": 5, "name": "root", "id": "r2"},
    ]


def test_empty_segments_are_skipped():
    # Two line-breaks at the same offset produce a zero-length segment.
    markers = [_lb(0), _lb(0), _lb(5)]
    out = derive_voice_markers_from_indent(10, markers)
    # Only the [0,5) and [5,10) root segments survive; they merge.
    assert out == [
        {"type": "voice", "offset": 0, "length": 10, "name": "root", "id": "r1"},
    ]


def test_alternating_root_commentary_with_id_increments():
    markers = [
        _lb(0),                        # root [0,5)
        _lb(5), _indent(5, 1),         # cmt  [5,10)
        _lb(10),                       # root [10,15)
        _lb(15), _indent(15, 1),       # cmt  [15,20)
    ]
    out = derive_voice_markers_from_indent(20, markers)
    assert out == [
        {"type": "voice", "offset": 0, "length": 5, "name": "root", "id": "r1"},
        {"type": "voice", "offset": 5, "length": 5,
         "name": "commentary", "id": "c1", "responds-to": "r1"},
        {"type": "voice", "offset": 10, "length": 5, "name": "root", "id": "r2"},
        {"type": "voice", "offset": 15, "length": 5,
         "name": "commentary", "id": "c2", "responds-to": "r2"},
    ]


def test_custom_indent_voice_map():
    # An alternate convention: depth 2 = root, depth 0 = commentary.
    markers = [
        _lb(0),                        # depth 0 → commentary
        _lb(5), _indent(5, 2),         # depth 2 → root
    ]
    out = derive_voice_markers_from_indent(
        10, markers, indent_voice_map={0: "commentary", 2: "root"},
    )
    assert out == [
        {"type": "voice", "offset": 0, "length": 5,
         "name": "commentary", "id": "c1"},
        {"type": "voice", "offset": 5, "length": 5,
         "name": "root", "id": "r1"},
    ]


def test_isolated_toc_line_skipped():
    # A single direct-TOC line forms a one-line cluster and is skipped;
    # surrounding plain lines voice normally.
    markers = [
        _lb(0),                                  # root [0,5)
        _lb(5), _indent(5, 2), _indent(7, 1),    # direct-TOC line — skip
        _lb(10),                                 # root [10,15)
    ]
    out = derive_voice_markers_from_indent(15, markers)
    assert out == [
        {"type": "voice", "offset": 0, "length": 5, "name": "root", "id": "r1"},
        {"type": "voice", "offset": 10, "length": 5, "name": "root", "id": "r2"},
    ]


def test_toc_cluster_swallows_intermediate_non_toc_lines():
    # Two direct-TOC lines separated by 2 non-TOC lines (within
    # gap_tolerance=3) form one cluster; every line in the cluster span
    # is excluded.
    markers = [
        _lb(0),                                       # root [0,5) — outside cluster
        _lb(5), _indent(5, 2), _indent(7, 1),         # direct-TOC
        _lb(10),                                      # non-TOC, in cluster — skipped
        _lb(15), _indent(15, 1),                      # non-TOC, in cluster — skipped
        _lb(20), _indent(20, 2), _indent(22, 1),      # direct-TOC
        _lb(25),                                      # root [25,30) — outside cluster
    ]
    out = derive_voice_markers_from_indent(30, markers)
    # The two root segments are not adjacent (cluster sits between
    # them), so they stay as two separate spans.
    assert out == [
        {"type": "voice", "offset": 0, "length": 5, "name": "root", "id": "r1"},
        {"type": "voice", "offset": 25, "length": 5, "name": "root", "id": "r2"},
    ]


def test_toc_cluster_breaks_when_gap_exceeds_tolerance():
    # Two direct-TOC lines separated by 5 non-TOC lines (over
    # gap_tolerance=4) form two separate one-line clusters; the five
    # intermediate lines voice normally.
    markers = [
        _lb(0), _indent(0, 2), _indent(2, 1),         # direct-TOC #1
        _lb(5),                                       # root
        _lb(10),                                      # root
        _lb(15),                                      # root
        _lb(20),                                      # root
        _lb(25),                                      # root
        _lb(30), _indent(30, 2), _indent(32, 1),      # direct-TOC #2
    ]
    out = derive_voice_markers_from_indent(35, markers)
    # Lines 1-5 (offsets 5-30) merge into one root run; the two TOC
    # lines on either side are skipped.
    assert out == [
        {"type": "voice", "offset": 5, "length": 25, "name": "root", "id": "r1"},
    ]


def test_toc_cluster_breaks_run_across_section():
    # Commentary lines flanking a TOC cluster: the surviving cmt
    # segments must not merge across the skipped section.
    markers = [
        _lb(0), _indent(0, 1),                        # cmt [0,5)
        _lb(5), _indent(5, 2), _indent(7, 1),         # direct-TOC
        _lb(10), _indent(10, 1),                      # in cluster — skipped
        _lb(15), _indent(15, 2), _indent(17, 1),      # direct-TOC
        _lb(20), _indent(20, 1),                      # cmt [20,25)
    ]
    out = derive_voice_markers_from_indent(25, markers)
    assert out == [
        {"type": "voice", "offset": 0, "length": 5,
         "name": "commentary", "id": "c1"},
        {"type": "voice", "offset": 20, "length": 5,
         "name": "commentary", "id": "c2"},
    ]


def test_start_indent_without_internal_indent_is_not_toc():
    # A line that opens with an indent but has no mid-line indent is a
    # normal indented line — not a TOC line.
    markers = [
        _lb(0), _indent(0, 1),                   # cmt [0,5)
        _lb(5), _indent(5, 1),                   # cmt [5,10)
    ]
    out = derive_voice_markers_from_indent(10, markers)
    assert out == [
        {"type": "voice", "offset": 0, "length": 10,
         "name": "commentary", "id": "c1"},
    ]


def test_internal_indent_without_start_indent_is_not_toc():
    # A depth-0 root line that happens to carry an internal indent is
    # *not* a TOC line — the rule requires a start indent too.
    markers = [
        _lb(0), _indent(3, 1),                   # root [0,5), interior indent only
        _lb(5),                                  # root [5,10)
    ]
    out = derive_voice_markers_from_indent(10, markers)
    assert out == [
        {"type": "voice", "offset": 0, "length": 10, "name": "root", "id": "r1"},
    ]


def test_high_indent_extends_strict_toc_cluster():
    # A strict-TOC line seeds a cluster; a depth>1 line within
    # gap_tolerance joins (and extends) it, even though it carries no
    # internal indent of its own. Mimics KR1a0042_001: post-cluster lines
    # alternate depth-1/depth-3 with no further strict markers.
    markers = [
        _lb(0),                                       # root [0,5) — outside cluster
        _lb(5), _indent(5, 2), _indent(7, 1),         # strict-TOC seed
        _lb(10), _indent(10, 3),                      # high-indent — extends cluster
        _lb(15), _indent(15, 1),                      # plain — in cluster span (gap=1)
        _lb(20), _indent(20, 3),                      # high-indent — extends again
        _lb(25),                                      # root [25,30) — outside cluster
    ]
    out = derive_voice_markers_from_indent(30, markers)
    # All four cluster lines (5..25) skipped; only the flanking roots emit.
    assert out == [
        {"type": "voice", "offset": 0, "length": 5, "name": "root", "id": "r1"},
        {"type": "voice", "offset": 25, "length": 5, "name": "root", "id": "r2"},
    ]


def test_pure_high_indent_cluster_without_strict_seed_still_voices():
    # KR5c0095-style: depth-2/3/4 lines stand in for head/attribution,
    # never accompanied by a strict-TOC line. Cluster must NOT fire.
    markers = [
        _lb(0), _indent(0, 4),                        # attribution [0,9)
        _lb(9), _indent(9, 3),                        # head        [9,15)
        _lb(15), _indent(15, 2),                      # head        [15,20)
        _lb(20),                                      # root        [20,25)
    ]
    out = derive_voice_markers_from_indent(25, markers)
    assert out == [
        {"type": "voice", "offset": 0, "length": 9,
         "name": "attribution", "id": "a1"},
        {"type": "voice", "offset": 9, "length": 11,
         "name": "head", "id": "h1", "responds-to": "a1"},
        {"type": "voice", "offset": 20, "length": 5,
         "name": "root", "id": "r1"},
    ]


def test_high_indent_only_cluster_after_strict_break_does_not_fire():
    # After a fired strict cluster ends (gap > tolerance), a subsequent
    # run of pure high-indent lines forms a separate cluster with no
    # strict seed — it must NOT fire.
    markers = [
        _lb(0), _indent(0, 2), _indent(2, 1),         # strict-TOC #1
        _lb(5),                                       # plain
        _lb(10),                                      # plain
        _lb(15),                                      # plain
        _lb(20),                                      # plain
        _lb(25),                                      # plain (5 plain lines > tol=4)
        _lb(30), _indent(30, 3),                      # high-indent (no strict in reach)
        _lb(35), _indent(35, 3),                      # high-indent
    ]
    out = derive_voice_markers_from_indent(40, markers)
    # Line 0 fires (strict cluster of 1). Lines 1..5 merge into one root run.
    # Lines 6,7 form a high-only cluster — does NOT fire, so they emit as head.
    assert out == [
        {"type": "voice", "offset": 5, "length": 25, "name": "root", "id": "r1"},
        {"type": "voice", "offset": 30, "length": 10,
         "name": "head", "id": "h1", "responds-to": "r1"},
    ]


def test_default_map_constant():
    # Pin the default convention so test fixtures and CLI docs stay aligned.
    assert DEFAULT_INDENT_VOICE_MAP == {
        0: "root",
        1: "commentary",
        2: "head",
        3: "head",
        4: "attribution",
    }
