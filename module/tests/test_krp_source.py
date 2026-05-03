"""Unit tests for :mod:`bkk.importer.source`.

Covers the recipe-less KRP path: local repo lookup (3-step order +
ambiguous warning), branch discovery against a fixture repo, and recipe
synthesis (the synthesized Recipe must round-trip through ``read_krp``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bkk.importer import source
from bkk.importer.read.krp import read_krp


REPO = Path(__file__).resolve().parents[1]
FIXTURE_TEXT_ID = "KR3a0013"
FIXTURE_REPO = REPO / "input" / "krp" / FIXTURE_TEXT_ID


def _has_fixture() -> bool:
    return FIXTURE_REPO.exists() and (FIXTURE_REPO / ".git").exists()


pytestmark = pytest.mark.skipif(
    not _has_fixture(),
    reason=f"krp fixture repo missing at {FIXTURE_REPO}",
)


# ---------- naming ----------------------------------------------------------


def test_section_prefix():
    assert source.section_prefix("KR3a0013") == "KR3a"
    assert source.section_prefix("KR6q0053") == "KR6q"


# ---------- local resolution ------------------------------------------------


def _link_clone(parent: Path, text_id: str) -> Path:
    """Create ``parent/text_id`` whose ``.git`` points at the fixture repo's git
    dir. Cheap enough for fanned-out lookup tests."""
    target = parent / text_id
    target.mkdir(parents=True)
    (target / ".git").symlink_to(FIXTURE_REPO / ".git")
    return target


def test_resolve_local_repo_prefix_layout(tmp_path: Path):
    """Mirror layout: <root>/<prefix>/<text-id>/."""
    section = tmp_path / "KR3a"
    section.mkdir()
    expected = _link_clone(section, FIXTURE_TEXT_ID)
    assert source.resolve_local_repo(tmp_path, FIXTURE_TEXT_ID) == expected


def test_resolve_local_repo_flat_layout(tmp_path: Path):
    """Flat layout: <root>/<text-id>/."""
    expected = _link_clone(tmp_path, FIXTURE_TEXT_ID)
    assert source.resolve_local_repo(tmp_path, FIXTURE_TEXT_ID) == expected


def test_resolve_local_repo_recursive_fallback(tmp_path: Path):
    """Buried layout: somewhere deeper under the root."""
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    expected = _link_clone(deep, FIXTURE_TEXT_ID)
    assert source.resolve_local_repo(tmp_path, FIXTURE_TEXT_ID) == expected


def test_resolve_local_repo_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        source.resolve_local_repo(tmp_path, "KRzz9999")


def test_resolve_local_repo_ambiguous_picks_shallowest(tmp_path: Path, capsys):
    """Two recursive matches at different depths: shallower wins + warn.

    The prefix and flat candidates are deliberately absent so the rglob
    branch runs.
    """
    shallow_parent = tmp_path / "a"
    shallow_parent.mkdir()
    deep_parent = tmp_path / "b" / "c"
    deep_parent.mkdir(parents=True)
    expected = _link_clone(shallow_parent, FIXTURE_TEXT_ID)
    _link_clone(deep_parent, FIXTURE_TEXT_ID)

    chosen = source.resolve_local_repo(tmp_path, FIXTURE_TEXT_ID)
    err = capsys.readouterr().err
    assert "multiple matches" in err
    assert chosen == expected


# ---------- branch discovery ------------------------------------------------


def test_discover_branches_includes_master_and_data():
    branches = set(source.discover_branches(FIXTURE_REPO))
    assert "master" in branches
    assert "_data" in branches
    assert "WYG" in branches


# ---------- bulk local discovery -------------------------------------------


def test_list_local_text_ids_section_layout(tmp_path: Path):
    section = tmp_path / "KR3a"
    section.mkdir()
    _link_clone(section, FIXTURE_TEXT_ID)
    assert source.list_local_text_ids(tmp_path, "KR3a") == [FIXTURE_TEXT_ID]


def test_list_local_text_ids_traverse_all(tmp_path: Path):
    section = tmp_path / "KR3a"
    section.mkdir()
    _link_clone(section, FIXTURE_TEXT_ID)
    assert source.list_local_text_ids(tmp_path, None) == [FIXTURE_TEXT_ID]


def test_list_local_text_ids_empty(tmp_path: Path):
    assert source.list_local_text_ids(tmp_path, "KR3a") == []


# ---------- recipe synthesis -----------------------------------------------


def test_synthesize_recipe_shape():
    recipe = source.synthesize_recipe(FIXTURE_REPO, FIXTURE_TEXT_ID)
    assert recipe.format == "krp"
    assert recipe.text_id == FIXTURE_TEXT_ID
    assert recipe.source is not None
    assert recipe.source.repo == FIXTURE_REPO
    edition_shorts = [e.short for e in recipe.source.editions]
    assert edition_shorts == ["WYG"]
    assert recipe.source.master is not None
    assert recipe.source.master.branch == "master"
    assert recipe.source.master.witnesses == ["WYG"]
    assert recipe.source.imglist is not None
    assert recipe.source.imglist.branch == "_data"
    # Title pulled from Readme.org, with the trailing "/ WYG" stripped.
    assert recipe.metadata.get("title") == "傅子"
    assert recipe.metadata.get("date")


def test_synthesize_recipe_round_trips_through_read_krp():
    """The synthesized Recipe must drive read_krp end-to-end."""
    recipe = source.synthesize_recipe(FIXTURE_REPO, FIXTURE_TEXT_ID)
    documentary, master = read_krp(recipe)
    assert documentary, "expected at least one documentary edition"
    assert master is not None, "expected master bundle"
    assert master.text_id == FIXTURE_TEXT_ID
    assert master.metadata.get("title") == "傅子"
