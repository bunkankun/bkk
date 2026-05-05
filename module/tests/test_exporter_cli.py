"""Exporter CLI: recipe optional + CLI overrides + corpus-walk batch.

Reuses the shared bundle fixture from test_krp_export so we don't rebuild
the KR3a0013 corpus per test.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest
import yaml

from bkk.exporter import cli as exporter_cli

# Reuse the fixture and TEXT_ID from the existing test module.
from .test_krp_export import TEXT_ID, bundle_dir  # noqa: F401


def _capture(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = exporter_cli.run(argv)
    return rc, out.getvalue(), err.getvalue()


def _write_recipe(path: Path, body: dict) -> Path:
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


# ---------- single-bundle modes -------------------------------------------


def test_generic_recipe_plus_cli_bundle_and_output(
    tmp_path: Path, bundle_dir: Path
):
    """A recipe pinning only format/shape/edition; --bundle and --output-dir
    on the CLI complete it."""
    recipe = _write_recipe(tmp_path / "r.yaml", {
        "format": "krp",
        "shape": "single",
        "edition": "WYG",
    })
    out_dir = tmp_path / "out"
    rc, _, err = _capture([
        "--recipe", str(recipe),
        "--bundle", str(bundle_dir),
        "--output-dir", str(out_dir),
    ])
    assert rc == 0, err
    assert (out_dir / f"{TEXT_ID}_001.txt").exists()


def test_cli_bundle_overrides_recipe_bundle(tmp_path: Path, bundle_dir: Path):
    """Recipe pins a bogus bundle; --bundle wins."""
    recipe = _write_recipe(tmp_path / "r.yaml", {
        "format": "krp",
        "bundle": "/nonexistent/recipe-bundle",
        "output_dir": "/nonexistent/recipe-out",
        "shape": "single",
        "edition": "WYG",
    })
    out_dir = tmp_path / "out"
    rc, _, err = _capture([
        "--recipe", str(recipe),
        "--bundle", str(bundle_dir),
        "--output-dir", str(out_dir),
    ])
    assert rc == 0, err
    assert (out_dir / f"{TEXT_ID}_001.txt").exists()


def test_no_recipe_at_all(tmp_path: Path, bundle_dir: Path):
    """Build a Recipe entirely from CLI flags — no --recipe."""
    out_dir = tmp_path / "out"
    rc, _, err = _capture([
        "--format", "krp", "--shape", "single", "--edition", "WYG",
        "--bundle", str(bundle_dir),
        "--output-dir", str(out_dir),
    ])
    assert rc == 0, err
    assert (out_dir / f"{TEXT_ID}_001.txt").exists()


def test_recipe_missing_bundle_no_override_errors(tmp_path: Path):
    recipe = _write_recipe(tmp_path / "r.yaml", {"format": "krp"})
    rc, _, err = _capture([
        "--recipe", str(recipe),
        "--output-dir", str(tmp_path / "out"),
    ])
    assert rc == 2
    assert "no bundle set" in err


def test_no_recipe_no_format_errors(tmp_path: Path, bundle_dir: Path):
    rc, _, err = _capture([
        "--bundle", str(bundle_dir),
        "--output-dir", str(tmp_path / "out"),
    ])
    assert rc == 2
    assert "no format set" in err


# ---------- corpus discovery (unit) ---------------------------------------


def test_iter_bundle_dirs_skips_dirs_without_manifest(tmp_path: Path):
    (tmp_path / "KR3a0001").mkdir()
    (tmp_path / "KR3a0001" / "KR3a0001.manifest.yaml").write_text("", "utf-8")
    (tmp_path / "KR3a0002").mkdir()  # no manifest → skipped
    (tmp_path / "stray.txt").write_text("", "utf-8")  # not a dir → skipped

    found = [p.name for p in
             exporter_cli._iter_bundle_dirs(tmp_path, text_id=None, section=None)]
    assert found == ["KR3a0001"]


def test_iter_bundle_dirs_text_id_filter(tmp_path: Path):
    for tid in ("KR3a0001", "KR3a0002", "KR9z0001"):
        d = tmp_path / tid
        d.mkdir()
        (d / f"{tid}.manifest.yaml").write_text("", "utf-8")

    found = [p.name for p in
             exporter_cli._iter_bundle_dirs(
                 tmp_path, text_id="KR3a0002", section=None)]
    assert found == ["KR3a0002"]


def test_iter_bundle_dirs_section_filter(tmp_path: Path):
    for tid in ("KR3a0001", "KR3a0002", "KR9z0001"):
        d = tmp_path / tid
        d.mkdir()
        (d / f"{tid}.manifest.yaml").write_text("", "utf-8")

    found = [p.name for p in
             exporter_cli._iter_bundle_dirs(
                 tmp_path, text_id=None, section="KR3a")]
    assert found == ["KR3a0001", "KR3a0002"]


# ---------- corpus mode (end-to-end) --------------------------------------


@pytest.fixture
def corpus_root(tmp_path: Path, bundle_dir: Path) -> Path:
    """A corpus dir holding a single real bundle (symlink) plus a stub
    bundle that will fail to export. Used to exercise the walk + skip
    behaviour without rebuilding the KR3a0013 fixture."""
    root = tmp_path / "corpus"
    root.mkdir()
    (root / TEXT_ID).symlink_to(bundle_dir, target_is_directory=True)

    bad = root / "KR0bad000"
    bad.mkdir()
    (bad / "KR0bad000.manifest.yaml").write_text(
        "text_id: KR0bad000\nencoding: utf-8\nparts: []\n",
        encoding="utf-8",
    )
    return root


def test_corpus_walk_exports_each_bundle(tmp_path: Path, corpus_root: Path):
    """Both bundles are attempted; the good one writes files, the bad one
    is reported to stderr but does not abort the walk."""
    out_root = tmp_path / "out"
    rc, _, err = _capture([
        "--format", "krp", "--shape", "single", "--edition", "WYG",
        "--corpus", str(corpus_root),
        "--output-dir", str(out_root),
        "--yes",
    ])
    assert rc == 1  # one bundle failed
    assert "KR0bad000" in err
    assert (out_root / TEXT_ID / f"{TEXT_ID}_001.txt").exists()


def test_corpus_text_id_picks_one(tmp_path: Path, corpus_root: Path):
    out_root = tmp_path / "out"
    rc, _, err = _capture([
        "--format", "krp", "--shape", "single", "--edition", "WYG",
        "--corpus", str(corpus_root),
        "--text-id", TEXT_ID,
        "--output-dir", str(out_root),
    ])
    assert rc == 0, err
    assert (out_root / TEXT_ID).is_dir()
    assert not (out_root / "KR0bad000").exists()


def test_corpus_section_filter_excludes_non_matching(
    tmp_path: Path, corpus_root: Path
):
    out_root = tmp_path / "out"
    rc, _, err = _capture([
        "--format", "krp", "--shape", "single", "--edition", "WYG",
        "--corpus", str(corpus_root),
        "--section", "KR3a",
        "--output-dir", str(out_root),
    ])
    assert rc == 0, err
    assert (out_root / TEXT_ID).is_dir()
    # The bad KR0* bundle is filtered out, so no failure is recorded.
    assert "KR0bad000" not in err


def test_corpus_requires_output_dir(corpus_root: Path):
    rc, _, err = _capture([
        "--format", "krp", "--corpus", str(corpus_root),
    ])
    assert rc == 2
    assert "--output-dir is required" in err


def test_corpus_and_bundle_mutually_exclusive(tmp_path: Path, corpus_root: Path):
    rc, _, err = _capture([
        "--format", "krp", "--corpus", str(corpus_root),
        "--bundle", str(tmp_path),
        "--output-dir", str(tmp_path / "out"),
    ])
    assert rc == 2
    assert "mutually exclusive" in err
