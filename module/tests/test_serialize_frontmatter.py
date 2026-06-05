"""Round-trip + format tests for bkk.serialize.frontmatter."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from bkk.serialize.frontmatter import parse_frontmatter, serialize_frontmatter


def test_parse_empty_text():
    fm, body = parse_frontmatter("")
    assert fm == {}
    assert body == ""


def test_parse_no_frontmatter():
    fm, body = parse_frontmatter("just body\n")
    assert fm == {}
    assert body == "just body\n"


def test_parse_basic():
    text = "---\nuuid: abc\nconcept: SUBJECTIVE\n---\nbody line\n"
    fm, body = parse_frontmatter(text)
    assert fm == {"uuid": "abc", "concept": "SUBJECTIVE"}
    assert body == "body line\n"


def test_parse_preserves_key_order():
    text = "---\nz: 1\na: 2\nm: 3\n---\nbody\n"
    fm, _ = parse_frontmatter(text)
    assert list(fm.keys()) == ["z", "a", "m"]


def test_serialize_basic_round_trip():
    text = (
        "---\n"
        "uuid: 00123688-6ca0-49c1-9edd-3712ab29cd2c\n"
        "type: concept\n"
        "concept: SUBJECTIVE\n"
        "zh: 主觀\n"
        "---\n"
        "# Concept: SUBJECTIVE\nbody\n"
    )
    fm, body = parse_frontmatter(text)
    assert serialize_frontmatter(fm, body) == text


def test_serialize_preserves_unicode_unescaped():
    fm = {"zh": "主觀", "en": "café"}
    out = serialize_frontmatter(fm, "")
    assert "主觀" in out
    assert "café" in out
    assert "\\u" not in out


def test_serialize_preserves_key_order():
    fm = {"z": 1, "a": 2, "m": 3}
    out = serialize_frontmatter(fm, "")
    body_start = out.find("---\n", 4)
    fm_text = out[len("---\n"):body_start]
    keys = [line.split(":", 1)[0] for line in fm_text.strip().splitlines()]
    assert keys == ["z", "a", "m"]


def test_serialize_appends_trailing_newline():
    out = serialize_frontmatter({"a": 1}, "body without newline")
    assert out.endswith("body without newline\n")


def test_serialize_leaves_existing_trailing_newline_alone():
    out = serialize_frontmatter({"a": 1}, "body\n")
    # exactly one trailing newline, not two
    assert out.endswith("body\n")
    assert not out.endswith("body\n\n")


def test_serialize_empty_fm_still_emits_fence():
    out = serialize_frontmatter({}, "just body\n")
    assert out == "---\n---\njust body\n"


def test_serialize_block_style_for_nested():
    fm = {"form": {"pronunciations": [{"lang": "zh", "value": "x"}]}}
    out = serialize_frontmatter(fm, "")
    # block style: nested structures span multiple lines, no { or [
    assert "{" not in out
    assert "[" not in out


def test_round_trip_unchanged_when_value_unchanged():
    text = (
        "---\n"
        "uuid: 00123688-6ca0-49c1-9edd-3712ab29cd2c\n"
        "type: concept\n"
        "concept: SUBJECTIVE\n"
        "zh: 主觀\n"
        "---\n"
        "# Concept: SUBJECTIVE\nbody line\n"
    )
    fm, body = parse_frontmatter(text)
    assert serialize_frontmatter(fm, body) == text


_CORE_ROOT = os.environ.get("BKK_CORE_ROOT_FOR_TESTS", "/home/Shared/bkk/bkk-core")


@pytest.mark.skipif(
    not Path(_CORE_ROOT).is_dir(),
    reason="bkk-core checkout not available for round-trip survey",
)
def test_round_trip_real_concepts_survey():
    """Parse → serialize a sample of real concept files; record failures.

    This is a survey, not a strict gate: we report how many records
    fail to round-trip byte-equal so the maintainer knows whether a
    normalize pass is needed before relying on the editor for clean
    diffs. Fails only if the sampler crashes — *not* on round-trip
    drift. (A strict gate would require shipping the normalize pass
    first.)
    """
    root = Path(_CORE_ROOT) / "concepts"
    sample: list[Path] = []
    for shard in sorted(root.iterdir())[:4]:
        if shard.is_dir():
            sample.extend(sorted(shard.glob("*.md"))[:20])
    assert sample, "no concept files found to sample"

    drifted: list[str] = []
    for md_path in sample:
        original = md_path.read_text(encoding="utf-8")
        fm, body = parse_frontmatter(original)
        rebuilt = serialize_frontmatter(fm, body)
        if rebuilt != original:
            drifted.append(md_path.name)

    # report only; never fails the suite.
    pct = 100 * len(drifted) / len(sample)
    print(
        f"frontmatter round-trip survey: {len(drifted)}/{len(sample)} "
        f"records drift ({pct:.0f}%); first few: {drifted[:5]}"
    )
