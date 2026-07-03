"""Recipe shorthand parsing and ambiguity boundaries."""

from __future__ import annotations

import pytest

from bkk.serve.recipe_refs import parse_short_ref


@pytest.mark.parametrize(
    ("ref", "textid", "selection"),
    [
        ("1h4/1", "KR1h0004", {"juan": 1}),
        (
            "KR1h0004/001/body@0+86",
            "KR1h0004",
            {"juan": 1, "offset": 0, "length": 86},
        ),
        ("1h4/1/front", "KR1h0004", {"juan": 1, "bucket": "front"}),
        (
            "1h4/1/back@2+3",
            "KR1h0004",
            {"juan": 1, "bucket": "back", "offset": 2, "length": 3},
        ),
    ],
)
def test_parse_short_ref(ref, textid, selection):
    assert parse_short_ref(ref) == (textid, selection)


@pytest.mark.parametrize(
    "ref",
    [
        "1h00004/1/@0+1",
        "1h4/1/middle@0+1",
        "1h4/1/@0-86",
        "1h4/1/@-1+2",
        "1h4/1/@0+",
        "1h4/1@0+86",
        "1h4/1/",
        "kr1h4/1",
    ],
)
def test_parse_short_ref_rejects_ambiguous_or_unsupported_forms(ref):
    with pytest.raises(ValueError, match="invalid recipe ref"):
        parse_short_ref(ref)
