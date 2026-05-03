"""End-to-end CLI test for the recipe-less KRP path.

Covers the new ``--in --text-id`` shape and the bulk ``--section`` shape
with ``--yes`` (so no TTY prompt is needed). Github + interactive bulk
modes are validated manually per ``import/PLAN.md``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bkk.importer.cli import run


REPO = Path(__file__).resolve().parents[1]
FIXTURE_TEXT_ID = "KR3a0013"
FIXTURE_REPO = REPO / "input" / "krp" / FIXTURE_TEXT_ID
FIXTURE_ROOT = REPO / "input" / "krp"


pytestmark = pytest.mark.skipif(
    not FIXTURE_REPO.exists(),
    reason=f"krp fixture missing at {FIXTURE_REPO}",
)


def test_cli_recipe_less_single_text(tmp_path: Path):
    rc = run([
        "--format", "krp",
        "--in", str(FIXTURE_ROOT),
        "--text-id", FIXTURE_TEXT_ID,
        "--out", str(tmp_path),
    ])
    assert rc == 0
    bundle_root = tmp_path / FIXTURE_TEXT_ID
    assert bundle_root.is_dir()
    assert (bundle_root / f"{FIXTURE_TEXT_ID}.manifest.yaml").is_file()


def test_cli_recipe_still_works(tmp_path: Path):
    """Legacy --recipe path stays untouched."""
    recipe_path = REPO / "recipes" / f"{FIXTURE_TEXT_ID}.yaml"
    if not recipe_path.exists():
        pytest.skip(f"recipe missing at {recipe_path}")
    rc = run([
        "--format", "krp",
        "--recipe", str(recipe_path),
        "--out", str(tmp_path),
    ])
    assert rc == 0
    assert (tmp_path / FIXTURE_TEXT_ID / f"{FIXTURE_TEXT_ID}.manifest.yaml").is_file()


def test_cli_section_with_yes(tmp_path: Path):
    """--section + --yes runs without prompting."""
    rc = run([
        "--format", "krp",
        "--in", str(FIXTURE_ROOT),
        "--section", "KR3a",
        "--out", str(tmp_path),
        "--yes",
    ])
    assert rc == 0
    assert (tmp_path / FIXTURE_TEXT_ID / f"{FIXTURE_TEXT_ID}.manifest.yaml").is_file()


def test_cli_text_id_and_section_mutually_exclusive(tmp_path: Path, capsys):
    rc = run([
        "--format", "krp",
        "--in", str(FIXTURE_ROOT),
        "--text-id", FIXTURE_TEXT_ID,
        "--section", "KR3a",
        "--out", str(tmp_path),
    ])
    assert rc == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_cli_in_and_github_mutually_exclusive(tmp_path: Path, capsys):
    rc = run([
        "--format", "krp",
        "--in", str(FIXTURE_ROOT),
        "--github", "kanripo",
        "--text-id", FIXTURE_TEXT_ID,
        "--out", str(tmp_path),
    ])
    assert rc == 2
    assert "mutually exclusive" in capsys.readouterr().err
