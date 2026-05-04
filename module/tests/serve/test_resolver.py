"""Identifier resolution: indexed keys, disambiguation, mtime-based refresh."""

from __future__ import annotations

from pathlib import Path

import pytest

from bkk.serve.resolver import CorpusCache, IdentifierResolver, build_snapshot

from .conftest import write_bundle


def test_snapshot_indexes_textid_canonical_and_metadata_identifiers(tmp_path: Path):
    write_bundle(
        tmp_path,
        "TEST0001",
        "甲乙丙",
        identifiers={"krp": "TEST0001", "cbeta": "T01n0001", "slug": ["tiangan", "天干"]},
    )
    snap = build_snapshot(tmp_path)
    assert "TEST0001" in snap.by_identifier
    assert "bkk:test/TEST0001/v1" in snap.by_identifier
    assert "T01n0001" in snap.by_identifier
    assert "tiangan" in snap.by_identifier
    assert "天干" in snap.by_identifier


def test_disambiguate_prefers_no_base_edition(tmp_path: Path):
    # Same krp identifier, two bundles: one master (no base_edition), one edition.
    write_bundle(
        tmp_path, "TEST_A", "甲", identifiers={"krp": "SHARED"}
    )
    write_bundle(
        tmp_path,
        "TEST_B",
        "乙",
        identifiers={"krp": "SHARED"},
        base_edition="WYG",
    )
    cache = CorpusCache(tmp_path)
    resolver = IdentifierResolver(cache)

    candidates = resolver.lookup("SHARED")
    assert {c.textid for c in candidates} == {"TEST_A", "TEST_B"}
    chosen = resolver.disambiguate(candidates)
    assert chosen is not None
    assert chosen.textid == "TEST_A"


def test_disambiguate_returns_none_when_still_ambiguous(tmp_path: Path):
    write_bundle(
        tmp_path, "TEST_A", "甲", identifiers={"krp": "DUP"}, base_edition="WYG"
    )
    write_bundle(
        tmp_path, "TEST_B", "乙", identifiers={"krp": "DUP"}, base_edition="SBCK"
    )
    resolver = IdentifierResolver(CorpusCache(tmp_path))
    chosen = resolver.disambiguate(resolver.lookup("DUP"))
    assert chosen is None


def test_cache_refreshes_when_manifest_changes(tmp_path: Path):
    write_bundle(tmp_path, "TEST0001", "甲", identifiers={"krp": "ORIG"})
    cache = CorpusCache(tmp_path, ttl_seconds=0.0)
    snap1 = cache.get()
    assert "ORIG" in snap1.by_identifier

    # Bump mtime and rewrite identifier; force_refresh asserts independence
    # of TTL gating.
    manifest = tmp_path / "TEST0001" / "TEST0001.manifest.yaml"
    text = manifest.read_text(encoding="utf-8").replace("ORIG", "UPDATED")
    manifest.write_text(text, encoding="utf-8")

    snap2 = cache.force_refresh()
    assert "UPDATED" in snap2.by_identifier
    assert "ORIG" not in snap2.by_identifier


def test_cache_detects_new_bundle_after_refresh(tmp_path: Path):
    write_bundle(tmp_path, "TEST0001", "甲")
    cache = CorpusCache(tmp_path, ttl_seconds=0.0)
    snap1 = cache.get()
    assert "TEST0001" in snap1.by_textid
    assert "TEST0002" not in snap1.by_textid

    write_bundle(tmp_path, "TEST0002", "乙")
    snap2 = cache.get()
    assert "TEST0002" in snap2.by_textid
