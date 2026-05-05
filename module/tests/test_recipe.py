"""Recipe-loader tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from bkk.exporter.recipe import Recipe, RecipeError, apply_overrides, load_recipe


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


def test_partial_recipe_accepted(tmp_path: Path):
    """Generic recipes omit bundle/output_dir; the loader returns them as
    None and the caller fills them in via apply_overrides."""
    p = _write(tmp_path / "r.yaml", "format: tls\n")
    recipe = load_recipe(p)
    assert recipe.format == "tls"
    assert recipe.bundle is None
    assert recipe.output_dir is None


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


def test_apply_overrides_fills_missing_fields(tmp_path: Path):
    p = _write(tmp_path / "r.yaml", "format: krp\nshape: single\nedition: WYG\n")
    template = load_recipe(p)
    bundle = tmp_path / "bundle"
    out = tmp_path / "out"
    recipe = apply_overrides(template, bundle=bundle, output_dir=out)
    assert recipe.format == "krp"
    assert recipe.bundle == bundle.resolve()
    assert recipe.output_dir == out.resolve()
    assert recipe.shape == "single"
    assert recipe.edition == "WYG"


def test_apply_overrides_cli_wins_over_recipe(tmp_path: Path):
    p = _write(
        tmp_path / "r.yaml",
        "format: krp\nbundle: ./recipe-bundle\noutput_dir: ./recipe-out\n",
    )
    template = load_recipe(p)
    cli_bundle = tmp_path / "cli-bundle"
    cli_out = tmp_path / "cli-out"
    recipe = apply_overrides(template, bundle=cli_bundle, output_dir=cli_out)
    assert recipe.bundle == cli_bundle.resolve()
    assert recipe.output_dir == cli_out.resolve()


def test_apply_overrides_no_recipe(tmp_path: Path):
    """Building a Recipe entirely from CLI flags."""
    bundle = tmp_path / "b"
    out = tmp_path / "o"
    recipe = apply_overrides(
        None, format="krp", bundle=bundle, output_dir=out,
        shape="single", edition="WYG",
    )
    assert recipe.format == "krp"
    assert recipe.shape == "single"
    assert recipe.edition == "WYG"
    assert isinstance(recipe, Recipe)


def test_apply_overrides_missing_format(tmp_path: Path):
    with pytest.raises(RecipeError, match="no format set"):
        apply_overrides(None, bundle=tmp_path / "b", output_dir=tmp_path / "o")


def test_apply_overrides_missing_bundle(tmp_path: Path):
    with pytest.raises(RecipeError, match="no bundle set"):
        apply_overrides(None, format="krp", output_dir=tmp_path / "o")


def test_apply_overrides_missing_output_dir(tmp_path: Path):
    with pytest.raises(RecipeError, match="no output_dir set"):
        apply_overrides(None, format="krp", bundle=tmp_path / "b")


def test_apply_overrides_single_requires_edition(tmp_path: Path):
    with pytest.raises(RecipeError, match="shape: single requires"):
        apply_overrides(
            None, format="krp", bundle=tmp_path / "b", output_dir=tmp_path / "o",
            shape="single",
        )


def test_apply_overrides_rejects_krp_options_for_tls(tmp_path: Path):
    with pytest.raises(RecipeError, match="krp-only options"):
        apply_overrides(
            None, format="tls", bundle=tmp_path / "b", output_dir=tmp_path / "o",
            shape="single", edition="WYG",
        )
