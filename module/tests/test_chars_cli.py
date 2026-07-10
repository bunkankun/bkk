from __future__ import annotations

from pathlib import Path

from bkk.chars import cli


def test_chars_default_corpus_matches_info_before_import_out(
    tmp_path: Path, monkeypatch,
):
    info_corpus = tmp_path / "info-corpus"
    import_out = tmp_path / "import-out"
    monkeypatch.setattr(
        "bkk.config.load_rc",
        lambda: {
            "info": {"corpus": info_corpus},
            "import": {"out": import_out},
        },
    )

    assert cli._resolve_corpus_root(corpus=None, out_root=None) == info_corpus


def test_chars_global_corpus_beats_legacy_import_out(tmp_path: Path, monkeypatch):
    global_corpus = tmp_path / "global-corpus"
    import_out = tmp_path / "import-out"
    monkeypatch.setattr(
        "bkk.config.load_rc",
        lambda: {
            "global": {"corpus": global_corpus},
            "import": {"out": import_out},
        },
    )

    assert cli._resolve_corpus_root(corpus=None, out_root=None) == global_corpus


def test_chars_legacy_import_out_is_last_resort(
    tmp_path: Path, monkeypatch, capsys,
):
    import_out = tmp_path / "import-out"
    monkeypatch.setattr(
        "bkk.config.load_rc",
        lambda: {"import": {"out": import_out}},
    )

    assert cli._resolve_corpus_root(corpus=None, out_root=None) == import_out
    assert "deprecated" in capsys.readouterr().err


def test_chars_rejects_corpus_and_out_root_together(tmp_path: Path):
    result = cli.run([
        "canonicalize",
        "--corpus", str(tmp_path / "a"),
        "--out-root", str(tmp_path / "b"),
    ])

    assert result == 2
