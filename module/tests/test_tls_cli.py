"""End-to-end CLI tests for the TLS importer.

Covers the single-text shape (regression for the original path) and the
bulk shape (discovery + confirmation prompt). Skips when the TLS fixture
is missing.
"""

from __future__ import annotations

import builtins
from pathlib import Path

import pytest

from bkk.importer.cli import run


REPO = Path(__file__).resolve().parents[1]
FIXTURE_TEXT_ID = "KR6q0053"
FIXTURE_ROOT = REPO / "input" / "tls"
FIXTURE_XML = FIXTURE_ROOT / "tls-texts" / "data" / "KR6" / "q" / f"{FIXTURE_TEXT_ID}.xml"


pytestmark = pytest.mark.skipif(
    not FIXTURE_XML.exists(),
    reason=f"tls fixture missing at {FIXTURE_XML}",
)


def test_cli_single_text_still_works(tmp_path: Path):
    rc = run([
        "--format", "tls",
        "--in", str(FIXTURE_ROOT),
        "--text-id", FIXTURE_TEXT_ID,
        "--out", str(tmp_path),
    ])
    assert rc == 0
    bundle_root = tmp_path / FIXTURE_TEXT_ID
    assert bundle_root.is_dir()
    assert (bundle_root / f"{FIXTURE_TEXT_ID}.manifest.yaml").is_file()


def test_cli_bulk_with_yes(tmp_path: Path):
    """No --text-id discovers everything under <in>/tls-texts/data/."""
    rc = run([
        "--format", "tls",
        "--in", str(FIXTURE_ROOT),
        "--out", str(tmp_path),
        "--yes",
    ])
    assert rc == 0
    assert (tmp_path / FIXTURE_TEXT_ID / f"{FIXTURE_TEXT_ID}.manifest.yaml").is_file()


def test_cli_bulk_aborts_on_no(tmp_path: Path, monkeypatch, capsys):
    """A 2+ text bulk import prompts; replying 'n' aborts with rc=1."""
    # Synthesize a tls root with two text xmls so len(pairs) > 1 trips the
    # confirmation prompt. The xmls are never read because we abort first.
    in_root = tmp_path / "src"
    data_dir = in_root / "tls-texts" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "FAKE001.xml").write_text("<root/>", encoding="utf-8")
    (data_dir / "FAKE002.xml").write_text("<root/>", encoding="utf-8")

    monkeypatch.setattr(builtins, "input", lambda _prompt="": "n")

    out_root = tmp_path / "out"
    rc = run([
        "--format", "tls",
        "--in", str(in_root),
        "--out", str(out_root),
    ])
    assert rc == 1
    assert "aborted" in capsys.readouterr().err
    assert not out_root.exists() or not any(out_root.iterdir())


def test_cli_bulk_no_in_errors(tmp_path: Path, capsys):
    rc = run([
        "--format", "tls",
        "--out", str(tmp_path),
    ])
    assert rc == 2
    assert "--in and --out are required" in capsys.readouterr().err
