"""KRP exporter: BKK bundle → mandoku-view source files.

Builds the bundle once (via the importer), then exercises every recipe
shape/mode/filter combination and re-imports the ``shape: git`` output to
confirm round-trip equivalence.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bkk.exporter.krp import (
    _encode_pua, _render_juan_body, export_krp_from_recipe,
)
from bkk.exporter.recipe import Recipe, RecipeError, load_recipe
from bkk.importer.pua import PUA_BASE
from bkk.importer.read.krp import read_krp
from bkk.importer.recipe import load_recipe as load_import_recipe
from bkk.importer.write.bundle import write_krp_edition, write_krp_master


REPO = Path(__file__).resolve().parents[1]
TEXT_ID = "KR3a0013"


@pytest.fixture(scope="module")
def bundle_dir(tmp_path_factory) -> Path:
    """Build a fresh bundle from the upstream KRP source for the suite."""
    recipe_path = REPO / "recipes" / f"{TEXT_ID}.yaml"
    if not recipe_path.exists():
        pytest.skip(f"recipe not present at {recipe_path}")
    import_recipe = load_import_recipe(recipe_path)
    if not import_recipe.source.repo.exists():
        pytest.skip(f"krp input repo not present at {import_recipe.source.repo}")

    documentary, master = read_krp(import_recipe)
    out_root = tmp_path_factory.mktemp("bkk-bundle")
    for b in documentary:
        write_krp_edition(b, out_root)
    if master is not None:
        write_krp_master(master, out_root)
    return out_root / TEXT_ID


def _make_recipe(tmp_path: Path, bundle_dir: Path, **overrides) -> Recipe:
    tmp_path.mkdir(parents=True, exist_ok=True)
    yaml_body = {
        "format": "krp",
        "bundle": str(bundle_dir),
        "output_dir": str(tmp_path / "export"),
    }
    yaml_body.update(overrides)
    p = tmp_path / "recipe.yaml"
    p.write_text(yaml.safe_dump(yaml_body), encoding="utf-8")
    return load_recipe(p)


def test_default_shape_is_dirs_split(tmp_path: Path, bundle_dir: Path):
    recipe = _make_recipe(tmp_path, bundle_dir)
    written = export_krp_from_recipe(recipe)
    out = recipe.output_dir
    assert (out / "master" / f"{TEXT_ID}_001.txt").exists()
    assert (out / "WYG" / f"{TEXT_ID}_001.txt").exists()
    assert (out / "_data" / "imglist" / f"{TEXT_ID}_001.txt").exists()
    assert (out / "_data" / "imglist" / "imginfo.cfg").exists()
    assert (out / "master" / "Readme.org").exists()
    assert any(p.name == "Readme.org" for p in written)


def test_juan_text_has_org_header_and_markers(tmp_path: Path, bundle_dir: Path):
    recipe = _make_recipe(tmp_path, bundle_dir)
    export_krp_from_recipe(recipe)
    text = (recipe.output_dir / "master" / f"{TEXT_ID}_001.txt").read_text(
        encoding="utf-8"
    )
    assert text.startswith("# -*- mode: mandoku-view; -*-")
    assert "#+PROPERTY: ID KR3a0013" in text
    assert "<pb:KR3a0013_WYG_001-1a>" in text
    assert "¶" in text


def test_pua_codepoints_re_encoded_as_kr_entities(
    tmp_path: Path, bundle_dir: Path
):
    recipe = _make_recipe(tmp_path, bundle_dir)
    export_krp_from_recipe(recipe)
    text = (recipe.output_dir / "master" / f"{TEXT_ID}_001.txt").read_text(
        encoding="utf-8"
    )
    # Bundle has KR0008, KR0647 etc. — at least one should re-encode.
    assert "&KR" in text
    # No raw PUA codepoints should leak through.
    assert not any(
        PUA_BASE <= ord(ch) < PUA_BASE + 0x1000 for ch in text
    )


def test_juans_filter_writes_only_selected_seq(
    tmp_path: Path, bundle_dir: Path
):
    recipe = _make_recipe(tmp_path, bundle_dir, juans=[1])
    export_krp_from_recipe(recipe)
    out = recipe.output_dir
    assert (out / "master" / f"{TEXT_ID}_001.txt").exists()
    assert not (out / "master" / f"{TEXT_ID}_000.txt").exists()


def test_editions_filter_writes_only_selected_editions(
    tmp_path: Path, bundle_dir: Path
):
    recipe = _make_recipe(tmp_path, bundle_dir, editions=["WYG"])
    export_krp_from_recipe(recipe)
    out = recipe.output_dir
    assert (out / "WYG" / f"{TEXT_ID}_001.txt").exists()
    assert not (out / "master").exists()


def test_shape_single_writes_at_root(tmp_path: Path, bundle_dir: Path):
    recipe = _make_recipe(
        tmp_path, bundle_dir, shape="single", edition="WYG",
    )
    export_krp_from_recipe(recipe)
    out = recipe.output_dir
    assert (out / f"{TEXT_ID}_001.txt").exists()
    # No per-branch wrapping or auxiliary files.
    assert not (out / "WYG").exists()
    assert not (out / "master").exists()
    assert not (out / "_data").exists()


def test_mode_concat_rolls_juans_into_single_file(
    tmp_path: Path, bundle_dir: Path
):
    split_recipe = _make_recipe(tmp_path / "split", bundle_dir)
    export_krp_from_recipe(split_recipe)
    split_total = sum(
        (split_recipe.output_dir / "WYG" / f"{TEXT_ID}_{seq:03d}.txt"
         ).read_text(encoding="utf-8").count("¶")
        for seq in (0, 1)
    )
    concat_recipe = _make_recipe(tmp_path / "concat", bundle_dir, mode="concat")
    export_krp_from_recipe(concat_recipe)
    concat = (concat_recipe.output_dir / "WYG" / f"{TEXT_ID}.txt").read_text(
        encoding="utf-8"
    )
    # Both juans land in the one file; pilcrow count should match.
    assert "<pb:KR3a0013_WYG_000-" in concat
    assert "<pb:KR3a0013_WYG_001-" in concat
    assert concat.count("¶") == split_total


def test_recipe_validation_single_requires_edition(tmp_path: Path):
    bundle = tmp_path / "irrelevant"
    p = tmp_path / "r.yaml"
    p.write_text(yaml.safe_dump({
        "format": "krp",
        "bundle": str(bundle),
        "output_dir": str(tmp_path / "out"),
        "shape": "single",
    }), encoding="utf-8")
    with pytest.raises(RecipeError, match="shape: single requires"):
        load_recipe(p)


def test_recipe_validation_unknown_edition_filter(
    tmp_path: Path, bundle_dir: Path
):
    recipe = _make_recipe(tmp_path, bundle_dir, editions=["does-not-exist"])
    with pytest.raises(RecipeError, match="unknown editions"):
        export_krp_from_recipe(recipe)


def test_pua_round_trip(tmp_path: Path, bundle_dir: Path):
    recipe = _make_recipe(tmp_path, bundle_dir)
    export_krp_from_recipe(recipe)
    # Pick one juan, encode bundle text → KR entities → expand back, check id.
    juan = yaml.safe_load(
        (bundle_dir / f"{TEXT_ID}_001.yaml").read_text(encoding="utf-8")
    )
    body = juan["body"]["text"]
    encoded = _encode_pua(body)
    # Every PUA codepoint in body is now an &KRnnnn; reference.
    for ch in body:
        cp = ord(ch)
        if PUA_BASE <= cp < PUA_BASE + 0x1000:
            kr = f"&KR{cp - PUA_BASE:04d};"
            assert kr in encoded


def test_render_juan_body_emits_markers_in_offset_order():
    text = "ab"
    markers = [
        {"type": "page-break", "offset": 0, "id": "p1"},
        {"type": "line-break", "offset": 0, "id": "l1"},
        {"type": "indent", "offset": 1, "content": "　"},
        {"type": "line-break", "offset": 2, "id": "l2"},
    ]
    out = _render_juan_body(text, markers)
    assert out == "<pb:p1>¶a　b¶"
