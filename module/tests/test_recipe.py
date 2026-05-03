"""Recipe-loader tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from bkk.exporter.recipe import RecipeError, load_recipe


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_minimal_recipe(tmp_path: Path):
    p = _write(tmp_path / "r.yaml",
               "format: tls\nbundle: ./b\noutput_dir: ./out\n")
    recipe = load_recipe(p)
    assert recipe.format == "tls"
    assert recipe.bundle == (tmp_path / "b").resolve()
    assert recipe.output_dir == (tmp_path / "out").resolve()


def test_missing_required_key(tmp_path: Path):
    p = _write(tmp_path / "r.yaml", "format: tls\nbundle: ./b\n")
    with pytest.raises(RecipeError, match="missing required keys"):
        load_recipe(p)


def test_unknown_key_rejected(tmp_path: Path):
    p = _write(
        tmp_path / "r.yaml",
        "format: tls\nbundle: ./b\noutput_dir: ./out\nspice: cinnamon\n",
    )
    with pytest.raises(RecipeError, match="unknown keys"):
        load_recipe(p)


def test_unsupported_format(tmp_path: Path):
    p = _write(tmp_path / "r.yaml",
               "format: epub\nbundle: ./b\noutput_dir: ./out\n")
    with pytest.raises(RecipeError, match="unsupported format"):
        load_recipe(p)


def test_missing_file(tmp_path: Path):
    with pytest.raises(RecipeError, match="recipe not found"):
        load_recipe(tmp_path / "nope.yaml")


def test_non_mapping(tmp_path: Path):
    p = _write(tmp_path / "r.yaml", "- a\n- b\n")
    with pytest.raises(RecipeError, match="must be a YAML mapping"):
        load_recipe(p)
