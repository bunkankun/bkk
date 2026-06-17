"""Tests for ``bkk validate chars``."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from bkk.chars.refs import (
    CanonicalizationContext,
    MappingAsset,
    MappingEntry,
)
from bkk.validator.chars_check import (
    CharFinding,
    _iter_manifest_strings,
    check_bundle,
    classify,
    render_json,
    render_text,
    run,
    scan_text,
)


def _toy_ctx() -> CanonicalizationContext:
    mapping = MappingAsset(
        canonical_identifier="bkk:mapping/test-v1",
        hash="sha256:" + "1" * 64,
        filename="test-mapping.yaml",
    )
    return CanonicalizationContext(
        charset_id="bkk:charset/test-v1",
        charset_hash="sha256:" + "2" * 64,
        charset_filename="test-charset.yaml",
        inclusion_blocks=[(0x4E00, 0x9FFF)],
        # 0x5434 has a mapping → silent. 0x5449 is in-block (target).
        # 0x39B3 is excluded with no mapping → warning.
        excluded={
            0x5434: {"reason": "kZVariant", "replaced_by": 0x5449},
            0x39B3: {"reason": "kZVariant", "replaced_by": 0x363D},
        },
        mappings=[mapping],
        mapping_entries={
            0x5434: MappingEntry(
                entry_id="tf-0001",
                replacement_cp=0x5449,
                reason="kZVariant",
                mapping_index=0,
            ),
        },
    )


def test_classify_ascii_strict_is_error():
    ctx = _toy_ctx()
    assert classify(ord("A"), ctx) == ("error", "out-of-charset")


def test_classify_ascii_allowed_when_flag_set():
    ctx = _toy_ctx()
    assert classify(ord("A"), ctx, allow_ascii=True) is None


def test_classify_in_block_silent():
    ctx = _toy_ctx()
    assert classify(0x4E00, ctx) is None  # 一


def test_classify_excluded_with_mapping_silent():
    ctx = _toy_ctx()
    assert classify(0x5434, ctx) is None


def test_classify_excluded_without_mapping_warns():
    ctx = _toy_ctx()
    # 0x39B3 is in the inclusion block but excluded without a mapping
    # entry, so falls into the warning branch.
    assert classify(0x39B3, ctx) == ("warning", "kZVariant")


def test_classify_out_of_block_errors():
    ctx = _toy_ctx()
    # 0xFB00 (ﬀ) is outside the single test inclusion block.
    assert classify(0xFB00, ctx) == ("error", "out-of-charset")


def test_scan_text_aggregates_counts():
    ctx = _toy_ctx()
    text = chr(0x39B3) + "周易" + chr(0x39B3) + chr(0xFB00)
    findings = scan_text(text, ctx, location="loc")
    by_cp = {f.cp: f for f in findings}
    assert by_cp[0x39B3].count == 2
    assert by_cp[0x39B3].severity == "warning"
    assert by_cp[0xFB00].count == 1
    assert by_cp[0xFB00].severity == "error"
    assert 0x5434 not in by_cp  # not present in input
    assert 0x4E00 not in by_cp  # in-block, silent


def test_scan_text_empty_returns_empty():
    ctx = _toy_ctx()
    assert scan_text("", ctx, location="x") == []


def test_iter_manifest_strings_skips_hash_keys_and_sha_values():
    manifest = {
        "metadata": {"title": "周易"},
        "hash": "sha256:" + "a" * 64,
        "canonical_set": {
            "identifier": "bkk:charset/cjk-v1",
            "hash": "sha256:" + "b" * 64,
        },
        "parts": [
            {"filename": "x.yaml", "hash": "sha256:" + "c" * 64},
        ],
    }
    pairs = dict(_iter_manifest_strings(manifest, ""))
    assert pairs["metadata.title"] == "周易"
    assert pairs["canonical_set.identifier"] == "bkk:charset/cjk-v1"
    assert pairs["parts[0].filename"] == "x.yaml"
    # All sha256 strings and 'hash' keys are dropped.
    assert not any(v.startswith("sha256:") for v in pairs.values())
    assert not any(p.endswith(".hash") for p in pairs)


def _write_bundle(
    root: Path, text_id: str, *, juan_text: str, title: str = "周易",
) -> Path:
    bundle_dir = root / text_id
    bundle_dir.mkdir(parents=True)
    manifest = {
        "canonical_identifier": f"bkk:krp/{text_id}/v1",
        "canonical_set": {"identifier": "bkk:charset/cjk-v1"},
        "metadata": {"title": title},
    }
    (bundle_dir / f"{text_id}.manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True),
        encoding="utf-8",
    )
    juan = {"body": {"text": juan_text}}
    (bundle_dir / f"{text_id}_001.yaml").write_text(
        yaml.safe_dump(juan, allow_unicode=True),
        encoding="utf-8",
    )
    return bundle_dir


def test_check_bundle_clean_bundle(tmp_path: Path):
    ctx = _toy_ctx()
    _write_bundle(tmp_path, "KR0", juan_text="周易")
    report = check_bundle(tmp_path / "KR0", ctx=ctx)
    assert report.findings == []
    assert report.errors == 0
    assert report.warnings == 0


def test_check_bundle_flags_warnings_and_errors(tmp_path: Path):
    ctx = _toy_ctx()
    juan_text = "周" + chr(0x39B3) + chr(0xFB00) + "易"
    _write_bundle(tmp_path, "KR1", juan_text=juan_text)
    report = check_bundle(tmp_path / "KR1", ctx=ctx)
    by_cp = {f.cp: f for f in report.findings}
    assert by_cp[0x39B3].severity == "warning"
    assert by_cp[0xFB00].severity == "error"
    assert report.errors == 1
    assert report.warnings == 1


def test_check_bundle_scans_manifest_title(tmp_path: Path):
    ctx = _toy_ctx()
    # Title contains an excluded-without-mapping codepoint.
    _write_bundle(tmp_path, "KR2", juan_text="周易", title="周" + chr(0x39B3))
    report = check_bundle(tmp_path / "KR2", ctx=ctx)
    assert any(
        f.location == "manifest metadata.title" and f.cp == 0x39B3
        for f in report.findings
    )


def test_run_in_dir_returns_zero_when_only_warnings(
    tmp_path: Path, capsys,
):
    _write_bundle(tmp_path, "KR0", juan_text="周易")
    _write_bundle(tmp_path, "KR1", juan_text=chr(0x5861))
    # We rely on the real shipped context for this end-to-end test;
    # 0x5861 ('塡') is excluded WITH a mapping in the shipped charset,
    # so it is silent — both bundles report clean.
    rc = run(["--in", str(tmp_path)])
    out = capsys.readouterr().out
    assert "KR0" in out and "KR1" in out
    assert "summary:" in out
    assert rc == 0


def test_run_in_dir_returns_one_when_errors(tmp_path: Path, capsys):
    # Latin 'A' is outside every inclusion block in the shipped charset.
    _write_bundle(tmp_path, "KR_BAD", juan_text="周A易")
    rc = run(["--in", str(tmp_path)])
    out = capsys.readouterr().out
    assert "U+0041" in out
    assert "out-of-charset" in out
    assert rc == 1


def test_run_text_id_resolves_against_bkkrc(tmp_path: Path, capsys, monkeypatch):
    _write_bundle(tmp_path, "KR_A", juan_text="周易")
    _write_bundle(tmp_path, "KR_B", juan_text="周A易")  # would fail
    monkeypatch.setattr(
        "bkk.validator.chars_check._resolve_corpus_root",
        lambda: tmp_path,
    )
    rc = run(["--text-id", "KR_A"])
    out = capsys.readouterr().out
    assert "KR_A" in out
    assert "KR_B" not in out
    assert rc == 0


def test_run_text_id_and_in_are_mutually_exclusive(tmp_path: Path, capsys):
    _write_bundle(tmp_path, "KR_A", juan_text="周易")
    import pytest
    with pytest.raises(SystemExit):
        run(["--text-id", "KR_A", "--in", str(tmp_path)])
    err = capsys.readouterr().err
    assert "not allowed with argument" in err


def test_run_json_output(tmp_path: Path, capsys):
    _write_bundle(tmp_path, "KR_J", juan_text="周A易")
    rc = run(["--in", str(tmp_path), "--json"])
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["totals"]["errors"] == 1
    assert payload["bundles"][0]["text_id"] == "KR_J"
    assert payload["bundles"][0]["findings"][0]["cp"] == 0x41
    assert rc == 1


def test_render_text_no_findings(tmp_path: Path):
    from bkk.validator.chars_check import BundleCharsReport
    rep = BundleCharsReport(text_id="X", bundle_dir=tmp_path)
    out = render_text([rep])
    assert "[X]" in out
    assert "ok" in out
    assert "summary:" in out


def test_render_json_shape(tmp_path: Path):
    from bkk.validator.chars_check import BundleCharsReport
    rep = BundleCharsReport(
        text_id="X",
        bundle_dir=tmp_path,
        findings=[
            CharFinding(
                location="juan 001 [body]",
                cp=0x41,
                char="A",
                severity="error",
                reason="out-of-charset",
                count=3,
            ),
        ],
    )
    payload = json.loads(render_json([rep]))
    assert payload["bundles"][0]["errors"] == 3
    assert payload["totals"]["errors"] == 3
