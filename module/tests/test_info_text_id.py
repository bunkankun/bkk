"""`bkk info --text-id`: focused per-text dossier.

Builds a minimal synthetic bundle (manifest + one juan YAML) and exercises
``_collect_text`` directly, then exercises the CLI's ``--text-id`` mode.
"""

from __future__ import annotations

import io
import json
import sqlite3
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest
import yaml

from bkk.info.cli import _collect_text, run


TEXT_ID = "KR0test01"


def _write_bundle(corpus: Path) -> Path:
    bundle_dir = corpus / TEXT_ID
    bundle_dir.mkdir(parents=True)
    # Two juans: body has CJK text, juan 2 has front + back.
    # PUA char 􄀁 = U+105001 (KR0001); used once in body of juan 1.
    body_juan1 = "一二三四五" + chr(0x105001) + "六七八"  # 9 cjk+pua chars
    body_juan2 = "甲乙丙"                                   # 3 cjk
    front_juan2 = "序文一二"                                # 4 cjk
    back_juan2 = "跋"                                       # 1 cjk

    parts = [
        {"seq": 1, "filename": f"{TEXT_ID}_001.yaml"},
        {"seq": 2, "filename": f"{TEXT_ID}_002.yaml"},
    ]
    manifest = {
        "canonical_identifier": f"bkk:test/{TEXT_ID}/v1",
        "assets": {"parts": parts},
        "metadata": {
            "title": "測試經",
            "source": {"repository": "test", "path": TEXT_ID},
        },
        "editions": [
            {"short": "A", "label": "Edition A"},
            {"short": "B", "label": "Edition B"},
        ],
    }
    (bundle_dir / f"{TEXT_ID}.manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8",
    )

    juan1 = {
        "seq": 1,
        "body": {
            "text": body_juan1,
            "markers": [
                {"type": "line-break", "offset": 0, "id": "01"},
                {"type": "page-break", "offset": 4, "id": ""},
                {"type": "line-break", "offset": 4, "id": "02"},
            ],
        },
    }
    juan2 = {
        "seq": 2,
        "front": {
            "text": front_juan2,
            "markers": [{"type": "head", "offset": 0, "content": "序"}],
        },
        "body": {
            "text": body_juan2,
            "markers": [{"type": "line-break", "offset": 0, "id": "01"}],
        },
        "back": {
            "text": back_juan2,
            "markers": [],
        },
    }
    (bundle_dir / f"{TEXT_ID}_001.yaml").write_text(
        yaml.safe_dump(juan1, allow_unicode=True), encoding="utf-8",
    )
    (bundle_dir / f"{TEXT_ID}_002.yaml").write_text(
        yaml.safe_dump(juan2, allow_unicode=True), encoding="utf-8",
    )
    return bundle_dir


def _write_catalog(catalog_path: Path) -> None:
    conn = sqlite3.connect(catalog_path)
    try:
        conn.executescript(
            "CREATE TABLE catalog_bundle ("
            "textid TEXT PRIMARY KEY, section_code TEXT NOT NULL, "
            "title TEXT, title_pinyin TEXT, title_english TEXT, "
            "not_before INTEGER, not_after INTEGER, index_date INTEGER);"
        )
        conn.execute(
            "INSERT INTO catalog_bundle "
            "(textid, section_code, title, title_pinyin, title_english, "
            " not_before, not_after, index_date) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (TEXT_ID, "KR0", "測試經", "ce shi jing",
             "Test Sutra", 600, 700, 650),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def corpus_and_catalog(tmp_path: Path) -> tuple[Path, Path]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    _write_bundle(corpus)
    catalog = tmp_path / "_catalog.bkkc"
    _write_catalog(catalog)
    return corpus, catalog


def test_collect_text_basic_shape(corpus_and_catalog):
    corpus, catalog = corpus_and_catalog
    report = _collect_text(TEXT_ID, corpus, catalog)
    assert report["textid"] == TEXT_ID
    assert report["title"] == "測試經"
    assert report["titlePinyin"] == "ce shi jing"
    assert report["titleEnglish"] == "Test Sutra"
    assert report["notBefore"] == 600
    assert report["notAfter"] == 700
    assert report["indexYear"] == 650
    assert report["juanCount"] == 2
    assert [e["short"] for e in report["editions"]] == ["A", "B"]
    assert "manifestDate" in report and report["manifestDate"]


def test_collect_text_char_counts(corpus_and_catalog):
    corpus, _ = corpus_and_catalog
    report = _collect_text(TEXT_ID, corpus, Path("/nonexistent"))
    chars = report["chars"]
    # front: only juan 2 front "序文一二" = 4 chars
    assert chars["front"]["total"] == 4
    assert chars["front"]["unique"] == 4
    # body: juan1 "一二三四五" + PUA + "六七八" = 9; juan2 "甲乙丙" = 3 -> 12
    assert chars["body"]["total"] == 12
    assert chars["body"]["unique"] == 12
    # back: juan2 "跋" = 1
    assert chars["back"]["total"] == 1
    assert chars["back"]["unique"] == 1
    assert chars["total"] == 4 + 12 + 1


def test_collect_text_pua_and_markers(corpus_and_catalog):
    corpus, _ = corpus_and_catalog
    report = _collect_text(TEXT_ID, corpus, Path("/nonexistent"))
    assert report["puaChars"]["total_unique"] == 1
    assert report["puaChars"]["total_occurrences"] == 1
    markers = report["markersByType"]
    # juan1 body: 2 line-break + 1 page-break
    # juan2 front: 1 head; body: 1 line-break
    assert markers == {
        "head": 1,
        "line-break": 3,
        "page-break": 1,
    }


def test_collect_text_falls_back_to_manifest_title(corpus_and_catalog):
    corpus, _ = corpus_and_catalog
    report = _collect_text(TEXT_ID, corpus, Path("/nonexistent"))
    # No catalog row, but manifest metadata.title is still surfaced.
    assert report["title"] == "測試經"
    assert report["titlePinyin"] is None
    assert report["notBefore"] is None
    assert report.get("catalogPresent") is False


def test_collect_text_missing_id_raises(corpus_and_catalog):
    corpus, catalog = corpus_and_catalog
    with pytest.raises(LookupError):
        _collect_text("NOPE9999", corpus, catalog)


def _capture(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = run(argv)
    return rc, out.getvalue(), err.getvalue()


def test_cli_text_id_json_mode(corpus_and_catalog, monkeypatch):
    corpus, catalog = corpus_and_catalog
    monkeypatch.setattr("bkk.info.cli.load_rc", lambda: {})
    rc, out, err = _capture([
        "--corpus", str(corpus),
        "--catalog", str(catalog),
        "--text-id", TEXT_ID,
        "--json",
    ])
    assert rc == 0, err
    payload = json.loads(out)
    assert list(payload.keys()) == ["text"]
    assert payload["text"]["textid"] == TEXT_ID
    assert payload["text"]["juanCount"] == 2


def test_cli_text_id_missing_returns_error(corpus_and_catalog, monkeypatch):
    corpus, catalog = corpus_and_catalog
    monkeypatch.setattr("bkk.info.cli.load_rc", lambda: {})
    rc, _, err = _capture([
        "--corpus", str(corpus),
        "--catalog", str(catalog),
        "--text-id", "NOPE9999",
    ])
    assert rc == 1
    assert "NOPE9999" in err
