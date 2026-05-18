"""Catalog-index builder tests."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import yaml

from bkk.index.catalog import (
    build_catalog_index,
    calculate_index_date,
    parse_year,
    parse_years,
)
from bkk.index.cli import run as cli_run


def _write_bundle(
    root: Path,
    textid: str,
    *,
    manifest_hash: str | None = None,
    title: str | None = None,
) -> Path:
    bundle_dir = root / textid
    bundle_dir.mkdir(parents=True)
    (bundle_dir / f"{textid}.manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "canonical_identifier": f"bkk:test/{textid}/v1",
                "hash": manifest_hash,
                "metadata": {"title": title} if title else {},
                "editions": [{"short": "X", "label": "x"}],
                "assets": {"parts": []},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return bundle_dir


def _write_frontmatter(path: Path, rows: list[dict[str, str]]) -> Path:
    fieldnames = [
        "id", "title", "titlePinyin", "titleEnglish",
        "notBefore", "notAfter", "dzt_date",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})
    return path


def _rows(conn: sqlite3.Connection, sql: str):
    return conn.execute(sql).fetchall()


def test_catalog_index_date_rules():
    assert calculate_index_date(1000, 1020, None) == (1010, "midpoint")
    assert calculate_index_date(618, 1279, None) == (618, "not_before_wide_range")
    assert calculate_index_date(618, 1279, 980) == (980, "dzt_date")
    assert calculate_index_date(618, 1279, (980, 1001)) == (
        990, "dzt_date_midpoint",
    )
    assert calculate_index_date(618, 1279, (390, 413)) == (
        401, "dzt_date_midpoint",
    )
    assert calculate_index_date(618, 1279, (-390, -245)) == (
        -390, "dzt_date_not_before_wide_range",
    )
    assert calculate_index_date(618, 1279, (245, 420)) == (
        245, "dzt_date_not_before_wide_range",
    )
    assert parse_year("- 390") == -390
    assert parse_years("- 390; - 245") == [-390, -245]
    assert parse_years("980-1001") == [980, 1001]
    assert parse_years("390; 410; 413") == [390, 410, 413]


def test_cli_catalog_builds_bkkc(tmp_path):
    _write_bundle(tmp_path, "KR1a0001", manifest_hash="sha256:one")
    _write_bundle(tmp_path, "KR3ea0001", manifest_hash="sha256:two")
    csv_path = _write_frontmatter(
        tmp_path / "frontmatter.csv",
        [
            {"id": "KR1", "title": "經部"},
            {"id": "KR1a", "title": "易類"},
            {"id": "KR3", "title": "子部"},
            {"id": "KR3e", "title": "醫家類"},
            {"id": "KR3ea", "title": "醫經"},
            {
                "id": "KR1a0001", "title": "周易", "titlePinyin": "Zhouyi",
                "titleEnglish": "Changes", "notBefore": "-900",
                "notAfter": "-100", "dzt_date": "",
            },
            {
                "id": "KR3ea0001", "title": "素問", "titlePinyin": "Suwen",
                "titleEnglish": "Basic Questions", "notBefore": "100",
                "notAfter": "120", "dzt_date": "110; 130",
            },
        ],
    )
    out = tmp_path / "_catalog.bkkc"

    assert cli_run([
        "catalog", str(tmp_path), "--csv", str(csv_path), "--out", str(out),
    ]) == 0

    conn = sqlite3.connect(out)
    try:
        assert _rows(
            conn,
            "SELECT textid, section_code, dzt_date, index_date, "
            "index_date_source, manifest_hash "
            "FROM catalog_bundle ORDER BY textid",
        ) == [
            ("KR1a0001", "KR1a", None, -900, "not_before_wide_range", "sha256:one"),
            ("KR3ea0001", "KR3ea", 120, 120, "dzt_date_midpoint", "sha256:two"),
        ]
        assert _rows(
            conn,
            "SELECT code, parent_code, direct_bundle_count, descendant_bundle_count "
            "FROM catalog_section WHERE code IN ('KR3', 'KR3e', 'KR3ea') "
            "ORDER BY code",
        ) == [
            ("KR3", None, 0, 1),
            ("KR3e", "KR3", 0, 1),
            ("KR3ea", "KR3e", 1, 1),
        ]
    finally:
        conn.close()


def test_catalog_missing_csv_row_uses_bundle_metadata_with_sentinel_date(tmp_path, caplog):
    _write_bundle(tmp_path, "KR1a0001")
    _write_bundle(tmp_path, "KR1a0002", title="Metadata Title")
    csv_path = _write_frontmatter(
        tmp_path / "frontmatter.csv",
        [
            {"id": "KR1", "title": "經部"},
            {"id": "KR1a", "title": "易類"},
            {
                "id": "KR1a0001", "title": "周易",
                "notBefore": "1000", "notAfter": "1020",
            },
        ],
    )

    with caplog.at_level("WARNING", logger="bkk.index"):
        out = build_catalog_index(tmp_path, csv_path, tmp_path / "_catalog.bkkc")

    assert "KR1a0002: catalog row missing" in caplog.text
    conn = sqlite3.connect(out)
    try:
        assert _rows(
            conn,
            "SELECT textid, title, index_date, index_date_source "
            "FROM catalog_bundle ORDER BY textid",
        ) == [
            ("KR1a0001", "周易", 1010, "midpoint"),
            ("KR1a0002", "Metadata Title", 9999, "missing"),
        ]
        assert _rows(
            conn,
            "SELECT code, direct_bundle_count, descendant_bundle_count "
            "FROM catalog_section WHERE code = 'KR1a'",
        ) == [("KR1a", 2, 2)]
    finally:
        conn.close()


def test_catalog_missing_date_uses_sentinel_date(tmp_path, caplog):
    _write_bundle(tmp_path, "KR1a0001")
    csv_path = _write_frontmatter(
        tmp_path / "frontmatter.csv",
        [
            {"id": "KR1", "title": "經部"},
            {"id": "KR1a", "title": "易類"},
            {"id": "KR1a0001", "title": "No Date"},
        ],
    )

    with caplog.at_level("WARNING", logger="bkk.index"):
        out = build_catalog_index(tmp_path, csv_path, tmp_path / "_catalog.bkkc")

    assert "KR1a0001: catalog row without usable date" in caplog.text
    conn = sqlite3.connect(out)
    try:
        assert _rows(
            conn,
            "SELECT textid, index_date, index_date_source FROM catalog_bundle",
        ) == [("KR1a0001", 9999, "missing")]
    finally:
        conn.close()


def test_catalog_duplicate_textid_warns_and_keeps_first(tmp_path, caplog):
    _write_bundle(tmp_path / "a", "KR1a0001", title="First")
    _write_bundle(tmp_path / "b", "KR1a0001", title="Second")
    csv_path = _write_frontmatter(
        tmp_path / "frontmatter.csv",
        [
            {"id": "KR1", "title": "經部"},
            {"id": "KR1a", "title": "易類"},
        ],
    )

    with caplog.at_level("WARNING", logger="bkk.index"):
        out = build_catalog_index(tmp_path, csv_path, tmp_path / "_catalog.bkkc")

    assert "KR1a0001: duplicate bundle discovered" in caplog.text
    conn = sqlite3.connect(out)
    try:
        assert _rows(
            conn,
            "SELECT textid, title, index_date, index_date_source FROM catalog_bundle",
        ) == [("KR1a0001", "First", 9999, "missing")]
    finally:
        conn.close()


def test_catalog_prefix_limits_current_corpus_counts(tmp_path):
    _write_bundle(tmp_path, "KR1a0001")
    _write_bundle(tmp_path, "KR3ea0001")
    csv_path = _write_frontmatter(
        tmp_path / "frontmatter.csv",
        [
            {"id": "KR1", "title": "經部"},
            {"id": "KR1a", "title": "易類"},
            {"id": "KR3", "title": "子部"},
            {"id": "KR3e", "title": "醫家類"},
            {"id": "KR3ea", "title": "醫經"},
            {"id": "KR1a0001", "title": "周易", "notBefore": "1", "notAfter": "1"},
            {"id": "KR3ea0001", "title": "素問", "notBefore": "2", "notAfter": "2"},
        ],
    )

    out = build_catalog_index(
        tmp_path, csv_path, tmp_path / "_catalog.bkkc", prefix="KR3"
    )

    conn = sqlite3.connect(out)
    try:
        assert _rows(conn, "SELECT textid FROM catalog_bundle") == [("KR3ea0001",)]
        assert _rows(
            conn,
            "SELECT code, descendant_bundle_count FROM catalog_section "
            "WHERE code IN ('KR1', 'KR3') ORDER BY code",
        ) == [("KR1", 0), ("KR3", 1)]
    finally:
        conn.close()
