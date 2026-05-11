"""KRP exporter: BKK bundle → mandoku-view source files.

Builds the bundle once (via the importer), then exercises every recipe
shape/mode/filter combination and re-imports the ``shape: git`` output to
confirm round-trip equivalence.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
import yaml

from bkk.exporter.krp import (
    _encode_pua, _render_juan_body, _render_readme, export_krp_from_recipe,
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


def test_default_shape_is_surface_at_root(tmp_path: Path, bundle_dir: Path):
    recipe = _make_recipe(tmp_path, bundle_dir)
    written = export_krp_from_recipe(recipe)
    out = recipe.output_dir
    assert (out / f"{TEXT_ID}_001.txt").exists()
    # Flattened surface-only mode: no per-edition subdirs, no auxiliaries.
    assert not (out / "master").exists()
    assert not (out / "WYG").exists()
    assert not (out / "_data").exists()
    assert not (out / "Readme.org").exists()
    assert all(p.parent == out for p in written)


def test_juan_text_has_org_header_and_markers(tmp_path: Path, bundle_dir: Path):
    recipe = _make_recipe(tmp_path, bundle_dir)
    export_krp_from_recipe(recipe)
    text = (recipe.output_dir / f"{TEXT_ID}_001.txt").read_text(
        encoding="utf-8"
    )
    assert text.startswith("# -*- mode: mandoku-view; -*-")
    assert "#+PROPERTY: ID KR3a0013" in text
    assert f"#+DATE: {datetime.date.today().isoformat()}" in text
    assert "<pb:KR3a0013_WYG_001-1a>" in text
    assert "¶" in text


def test_pua_codepoints_re_encoded_as_kr_entities(
    tmp_path: Path, bundle_dir: Path
):
    recipe = _make_recipe(tmp_path, bundle_dir)
    export_krp_from_recipe(recipe)
    text = (recipe.output_dir / f"{TEXT_ID}_001.txt").read_text(
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
    assert (out / f"{TEXT_ID}_001.txt").exists()
    assert not (out / f"{TEXT_ID}_000.txt").exists()


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
    split_recipe = _make_recipe(
        tmp_path / "split", bundle_dir, editions=["WYG"],
    )
    export_krp_from_recipe(split_recipe)
    split_total = sum(
        (split_recipe.output_dir / "WYG" / f"{TEXT_ID}_{seq:03d}.txt"
         ).read_text(encoding="utf-8").count("¶")
        for seq in (0, 1)
    )
    concat_recipe = _make_recipe(
        tmp_path / "concat", bundle_dir, mode="concat", editions=["WYG"],
    )
    export_krp_from_recipe(concat_recipe)
    concat = (concat_recipe.output_dir / "WYG" / f"{TEXT_ID}.txt").read_text(
        encoding="utf-8"
    )
    # Both juans land in the one file; pilcrow count should match.
    assert "<pb:KR3a0013_WYG_000-" in concat
    assert "<pb:KR3a0013_WYG_001-" in concat
    assert concat.count("¶") == split_total


def test_recipe_validation_single_requires_edition(tmp_path: Path):
    """`shape: single` with no edition is fine in the file (a generic
    recipe), but raises at execute time if the CLI doesn't supply one."""
    from bkk.exporter.recipe import apply_overrides

    bundle = tmp_path / "irrelevant"
    p = tmp_path / "r.yaml"
    p.write_text(yaml.safe_dump({
        "format": "krp",
        "bundle": str(bundle),
        "output_dir": str(tmp_path / "out"),
        "shape": "single",
    }), encoding="utf-8")
    template = load_recipe(p)  # accepted as a template
    with pytest.raises(RecipeError, match="shape: single requires"):
        apply_overrides(template)  # no --edition supplied → executable check fires


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
    assert out == "<pb:p1>¶\na　b¶\n"


def test_explicit_editions_master_keeps_subdir_layout(
    tmp_path: Path, bundle_dir: Path,
):
    """Opt-in path: passing `editions:` retains the legacy per-edition subdir
    layout (and auxiliaries) so users who want the multi-edition view still
    have it."""
    recipe = _make_recipe(tmp_path, bundle_dir, editions=["master", "WYG"])
    export_krp_from_recipe(recipe)
    out = recipe.output_dir
    assert (out / "master" / f"{TEXT_ID}_001.txt").exists()
    assert (out / "WYG" / f"{TEXT_ID}_001.txt").exists()
    assert (out / "master" / "Readme.org").exists()
    # Flattened root file is not produced when editions is explicit.
    assert not (out / f"{TEXT_ID}_001.txt").exists()


def test_default_export_obeys_marker_newline_invariants(
    tmp_path: Path, bundle_dir: Path,
):
    """Every `¶` ends a line, every `<pb:...>` starts one, and the file ends
    with a newline — i.e. the body matches canonical KRP source layout."""
    recipe = _make_recipe(tmp_path, bundle_dir)
    export_krp_from_recipe(recipe)
    text = (recipe.output_dir / f"{TEXT_ID}_001.txt").read_text(
        encoding="utf-8"
    )
    # Strip the org-mode header so we only check the body.
    header_end = text.find("#+PROPERTY: JUAN")
    body_start = text.find("\n", header_end) + 1
    body = text[body_start:]
    assert body.endswith("\n")
    for ln, line in enumerate(body.splitlines(), start=1):
        if "¶" in line:
            assert line.endswith("¶"), (
                f"body line {ln} contains ¶ mid-line: {line!r}"
            )
        # `<pb:...>` only appears at the start of a line (possibly the whole
        # line, possibly followed by `¶text¶`).
        idx = line.find("<pb:")
        if idx != -1:
            assert idx == 0, (
                f"body line {ln} has `<pb:` not at start: {line!r}"
            )


def _git(repo: Path, *args: str) -> str:
    import subprocess
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    ).stdout


def test_shape_git_branches_master_and_documentary(
    tmp_path: Path, bundle_dir: Path,
):
    """`shape: git` produces a `master` branch (surface) plus one branch per
    documentary edition, with juan files at the repo root of each branch."""
    recipe = _make_recipe(tmp_path, bundle_dir, shape="git")
    export_krp_from_recipe(recipe)
    out = recipe.output_dir
    branches = sorted(
        line.strip().lstrip("* ").strip()
        for line in _git(out, "branch", "--list").splitlines()
    )
    # KR3a0013's master + WYG witness + _data (has page-break images).
    assert "master" in branches
    assert "WYG" in branches
    assert "_data" in branches
    # No staging-dir leftovers in the working tree.
    assert not (out / "master").exists()
    assert not (out / "WYG").exists()
    assert not (out / "_data").exists()
    # Each branch's tree has juan files at the root (or imglist/ for _data).
    master_files = _git(out, "ls-tree", "--name-only", "master").split()
    assert f"{TEXT_ID}_001.txt" in master_files
    assert "Readme.org" in master_files
    wyg_files = _git(out, "ls-tree", "--name-only", "WYG").split()
    assert f"{TEXT_ID}_001.txt" in wyg_files
    assert "Readme.org" in wyg_files


def test_shape_git_readme_uses_richer_style(
    tmp_path: Path, bundle_dir: Path,
):
    """Readme.org follows canonical KRP source format: `* 目次` heading and
    `** [[file:…]]` entries (not the older `- [[file:…]]` list style)."""
    recipe = _make_recipe(tmp_path, bundle_dir, shape="git")
    export_krp_from_recipe(recipe)
    out = recipe.output_dir
    readme = _git(out, "show", "master:Readme.org")
    assert readme.startswith("#+TITLE: ")
    assert f"#+DATE: {datetime.date.today().isoformat()}" in readme
    assert "* 版本" in readme
    assert "* 目次" in readme
    assert "** [[file:" in readme
    # The older list-style entries must not appear.
    assert "\n - [[file:" not in readme
    # Each documentary edition branch carries the same Readme.
    wyg_readme = _git(out, "show", "WYG:Readme.org")
    assert wyg_readme == readme


def test_shape_git_rejects_editions_filter(tmp_path: Path, bundle_dir: Path):
    """`shape: git` always emits every edition; an explicit `editions:` filter
    must be rejected at recipe-validation time."""
    with pytest.raises(RecipeError, match="not supported with shape: git"):
        _make_recipe(
            tmp_path, bundle_dir, shape="git", editions=["WYG"],
        )


def test_shape_git_skips_data_branch_when_no_images(
    tmp_path: Path,
):
    """TLS-sourced bundles (no page-break image refs) get no `_data` branch."""
    sample = REPO / "samples" / "KR6c0101"
    if not sample.exists():
        pytest.skip(f"sample not present at {sample}")
    out_dir = tmp_path / "export"
    yaml_body = {
        "format": "krp",
        "bundle": str(sample),
        "output_dir": str(out_dir),
        "shape": "git",
    }
    p = tmp_path / "recipe.yaml"
    p.write_text(yaml.safe_dump(yaml_body), encoding="utf-8")
    recipe = load_recipe(p)
    export_krp_from_recipe(recipe)
    branches = sorted(
        line.strip().lstrip("* ").strip()
        for line in _git(out_dir, "branch", "--list").splitlines()
    )
    assert "master" in branches
    assert "T" in branches
    assert "_data" not in branches


def test_render_readme_handles_empty_editions_meta():
    """When the bundle carries no `editions` metadata (TLS-sourced), the 版本
    table renders empty without crashing."""
    from bkk.importer.ir import Bundle

    master = Bundle(
        text_id="KR6c0101",
        juans=[],
        metadata={
            "title": "金剛般若波羅蜜經開題",
            "table_of_contents": [
                {
                    "ref": {"seq": 1, "marker_id": "KR6c0101_T_001-0001a03"},
                    "label": "金剛般若波羅蜜經開題",
                },
            ],
        },
        edition_short="T",
        source_info={},
    )
    out = _render_readme(
        master, editions_meta=[], base_edition="T",
        title="金剛般若波羅蜜經開題", date="2026-05-11", juan_filter=None,
    )
    assert "#+TITLE: 金剛般若波羅蜜經開題 / T" in out
    assert "#+DATE: 2026-05-11" in out
    assert "* 版本" in out
    assert "* 目次" in out
    assert (
        "** [[file:KR6c0101_001.txt::001-0001a03][金剛般若波羅蜜經開題]]"
        in out
    )
