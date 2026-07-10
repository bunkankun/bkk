from __future__ import annotations

from pathlib import Path

import pytest

from bkk.cli_common import resolve_bundle_dir


def _bundle(root: Path, textid: str, *, nested: bool = False) -> Path:
    path = root / textid if not nested else root / textid[:4] / textid
    path.mkdir(parents=True)
    (path / f"{textid}.manifest.yaml").write_text("metadata: {}\n", encoding="utf-8")
    return path


def test_resolve_bundle_dir_accepts_explicit_path(tmp_path: Path):
    bundle = _bundle(tmp_path, "KR1a0001")

    assert resolve_bundle_dir(bundle=bundle) == bundle.resolve()


def test_resolve_bundle_dir_finds_text_id_under_nested_corpus(tmp_path: Path):
    bundle = _bundle(tmp_path, "KR1a0001", nested=True)

    assert resolve_bundle_dir(text_id="KR1a0001", root=tmp_path) == bundle.resolve()


def test_resolve_bundle_dir_rejects_ambiguous_selector(tmp_path: Path):
    bundle = _bundle(tmp_path, "KR1a0001")

    with pytest.raises(ValueError, match="either --bundle or --text-id"):
        resolve_bundle_dir(bundle=bundle, text_id="KR1a0001", root=tmp_path)


def test_resolve_bundle_dir_missing_root_keeps_bundle_not_found_wording():
    with pytest.raises(FileNotFoundError, match="bundle directory not found"):
        resolve_bundle_dir(text_id="KR1a0001")
