from __future__ import annotations

from pathlib import Path

import yaml

from bkk.index.catalog import build_catalog_index
from bkk.index.translation import merge_translations
from bkk.serve.translations import align_translation, list_translation_bundles
from bkk.serve.translations import (
    list_translation_bundles_from_catalog,
    load_translation_bundle_from_catalog,
)


def _write_source(corpus: Path) -> dict:
    root = corpus / "KR1h" / "KR1h0004"
    root.mkdir(parents=True)
    (root / "KR1h0004.manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "canonical_identifier": "bkk:krp/KR1h0004/v1",
                "assets": {
                    "parts": [
                        {"seq": 1, "filename": "KR1h0004_001.yaml", "hash": "sha256:0"}
                    ]
                },
                "metadata": {"title": "論語", "edition": {"short": "bkk"}},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    juan = {
        "seq": 1,
        "body": {
            "text": "子曰學而時習之",
            "hash": "sha256:0",
            "markers": [
                {"type": "tls:seg", "offset": 0, "id": "KR1h0004_tls_001-1a.3"},
                {"type": "tls:seg", "offset": 2, "id": "KR1h0004_tls_001-1a.4"},
                {"type": "tls:seg", "offset": 7, "id": "KR1h0004_tls_001-1a.5"},
            ],
        },
    }
    (root / "KR1h0004_001.yaml").write_text(
        yaml.safe_dump(juan, allow_unicode=True),
        encoding="utf-8",
    )
    return juan


def _write_translation(corpus: Path) -> None:
    root = corpus / "translations" / "KR1h" / "KR1h0004" / "en" / "KR1h0004-en-test"
    root.mkdir(parents=True)
    (root / "KR1h0004-en-test.md").write_text(
        """---
canonical_identifier: bkk:translation/KR1h0004-en-test/v1
source:
  canonical_identifier: bkk:krp/KR1h0004/v1
language: en
title: Test Translation
responsibility:
- {role: translator, name: Tester}
juan:
- {seq: 1, label: '001', file: KR1h0004-en-test_001.md, hash: 'sha256:0'}
hash: sha256:0
---
# Test Translation
""",
        encoding="utf-8",
    )
    (root / "KR1h0004-en-test_001.md").write_text(
        """---
juan_seq: 1
juan_label: '001'
markers:
- {ref: 001-1a.3, corresp: [001-1a.3]}
- {ref: [001-1a.4, 001-1a.5], corresp: [001-1a.4, 001-1a.5]}
---
[The Master said:]{@001-1a.3}
[learning and practice]{@001-1a.4 @001-1a.5}
""",
        encoding="utf-8",
    )


def test_translation_bundle_search_and_alignment(tmp_path: Path):
    source_juan = _write_source(tmp_path)
    _write_translation(tmp_path)

    matches = list_translation_bundles(tmp_path, q="practice")

    assert len(matches) == 1
    assert matches[0].summary.id == "KR1h0004-en-test"
    assert matches[0].summary.source_textid == "KR1h0004"
    assert matches[0].summary.responsibility[0].name == "Tester"

    aligned = align_translation(
        textid="KR1h0004",
        seq=1,
        source_juan=source_juan,
        translation=matches[0],
    )

    assert aligned.status == "ok"
    assert [row.corresp for row in aligned.rows] == [
        "001-1a.3",
        "001-1a.4",
        "001-1a.5",
    ]
    assert aligned.rows[0].translation_text == "The Master said:"
    assert aligned.rows[1].translation_text == "learning and practice"
    assert aligned.rows[2].translation_text == ""
    assert aligned.rows[2].continued is True


def test_catalog_translation_index_supports_fast_lookup(tmp_path: Path):
    source_juan = _write_source(tmp_path)
    _write_translation(tmp_path)
    csv_path = tmp_path / "frontmatter.csv"
    csv_path.write_text(
        "id,title,titlePinyin,titleEnglish,notBefore,notAfter,dzt_date\n"
        "KR1h,經部,Jing,Classics,,,,\n"
        "KR1h0004,論語,Lunyu,Analects,-500,-400,\n",
        encoding="utf-8",
    )
    catalog_path = tmp_path / "_catalog.bkkc"
    translations_path = tmp_path / "_translations.bkkt"
    build_catalog_index(tmp_path, csv_path, catalog_path)
    merge_translations(tmp_path, translations_path)

    import sqlite3

    conn = sqlite3.connect(catalog_path)
    search_conn = sqlite3.connect(translations_path)
    try:
        matches, total = list_translation_bundles_from_catalog(
            conn, search_conn=search_conn, source_textid="KR1h0004", q="practice"
        )
        assert total == 1
        assert matches[0].summary.id == "KR1h0004-en-test"

        loaded = load_translation_bundle_from_catalog(
            conn,
            translation_id="KR1h0004-en-test",
            source_textid="KR1h0004",
            include_juans=True,
        )
    finally:
        conn.close()
        search_conn.close()

    assert loaded is not None
    aligned = align_translation(
        textid="KR1h0004",
        seq=1,
        source_juan=source_juan,
        translation=loaded,
    )
    assert aligned.rows[1].translation_text == "learning and practice"
