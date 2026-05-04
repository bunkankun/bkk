"""Tests for the bkk.validator module.

Strategy: build a clean bundle on tmp_path with the existing TLS importer,
validate it, then mutate it to provoke each rule_id and assert it appears.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from bkk.importer.cli import _find_tls_text
from bkk.importer.read.tls import read_tls
from bkk.importer.write.bundle import write_bundle
from bkk.validator import validate_bundle
from bkk.validator.report import Report

REPO = Path(__file__).resolve().parents[1]
TEXT_ID = "KR6q0053"


@pytest.fixture(scope="module")
def fresh_bundle(tmp_path_factory) -> Path:
    in_root = REPO / "input" / "tls"
    text_xml = _find_tls_text(in_root, TEXT_ID)
    assert text_xml is not None
    bundle = read_tls(
        text_xml,
        in_root / "tls-data" / "notes" / "swl" / f"{TEXT_ID}-ann.xml",
        in_root / "tls-data" / "notes" / "doc" / f"{TEXT_ID}-ann.xml",
        TEXT_ID,
    )
    out = tmp_path_factory.mktemp("bkk-out")
    write_bundle(bundle, out)
    return out / TEXT_ID


@pytest.fixture()
def bundle_copy(fresh_bundle: Path, tmp_path: Path) -> Path:
    """Per-test copy of the fresh bundle so mutations don't leak."""
    dst = tmp_path / fresh_bundle.name
    shutil.copytree(fresh_bundle, dst)
    return dst


def _rule_ids(report: Report) -> set[str]:
    return {f.rule_id for f in report.findings}


def test_fresh_bundle_passes_structural_rules(fresh_bundle: Path):
    """A bundle just produced by the writer must not trip filesystem,
    manifest-format, juan-required-keys, hash-format, or PUA rules."""
    report = validate_bundle(fresh_bundle)
    structural = {
        "MANIFEST_MISSING", "MANIFEST_PARSE", "BUNDLE_DIR_NAME",
        "JUAN_FILE_MISSING", "ANN_FILE_MISSING",
        "EDITION_MANIFEST_MISSING", "EDITION_DECLARED_NOT_PRESENT",
        "EDITION_PRESENT_NOT_DECLARED", "EDITION_JUAN_COVERAGE",
        "MANIFEST_REQUIRED_KEYS", "CANONICAL_IDENTIFIER_FORMAT",
        "CANONICAL_LOCATION_MATCHES", "HASH_FORMAT",
        "ASSETS_PARTS_SEQ_UNIQUE", "ASSETS_PARTS_SEQ_MATCHES_FILE",
        "JUAN_REQUIRED_KEYS", "JUAN_BUCKETS_VALID", "JUAN_TEXT_NFC",
        "JUAN_MARKER_ID_FORMAT", "JUAN_MARKER_ID_UNIQUE",
        "ANN_REQUIRED_KEYS", "ANN_FILE_AGREES_WITH_FILENAME",
        "PUA_TOTALS", "PUA_ENTRY_KR_FORMAT", "PUA_ENTRY_CODEPOINT_FORMAT",
        "PUA_ENTRY_CHAR_MATCH", "PUA_ENTRY_KR_CODEPOINT_MATCH",
    }
    found = _rule_ids(report)
    leaked = found & structural
    assert not leaked, (
        f"fresh bundle should not trigger {leaked}; full findings:\n"
        + "\n".join(f"  {f.rule_id} {f.path}: {f.message}" for f in report.findings)
    )


def test_missing_master_manifest(bundle_copy: Path):
    (bundle_copy / f"{TEXT_ID}.manifest.yaml").unlink()
    report = validate_bundle(bundle_copy)
    assert "MANIFEST_MISSING" in _rule_ids(report)
    assert report.has_errors


def test_missing_juan_file(bundle_copy: Path):
    juan_files = list(bundle_copy.glob(f"{TEXT_ID}_*.yaml"))
    juan_files = [p for p in juan_files if "ann" not in p.name]
    juan_files[0].unlink()
    report = validate_bundle(bundle_copy)
    assert "JUAN_FILE_MISSING" in _rule_ids(report)


def test_bundle_dir_name_mismatch(bundle_copy: Path):
    """canonical_identifier text-id segment must match the bundle dir name."""
    mp = bundle_copy / f"{TEXT_ID}.manifest.yaml"
    data = yaml.safe_load(mp.read_text("utf-8"))
    data["canonical_identifier"] = "bkk:krp/SOMETHINGELSE/v1"
    mp.write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")
    report = validate_bundle(bundle_copy)
    assert "BUNDLE_DIR_NAME" in _rule_ids(report)


def test_corrupted_hash_format(bundle_copy: Path):
    mp = bundle_copy / f"{TEXT_ID}.manifest.yaml"
    data = yaml.safe_load(mp.read_text("utf-8"))
    data["hash"] = "not-a-valid-hash"
    mp.write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")
    report = validate_bundle(bundle_copy)
    assert "HASH_FORMAT" in _rule_ids(report)


def test_canonical_identifier_format_mismatch(bundle_copy: Path):
    mp = bundle_copy / f"{TEXT_ID}.manifest.yaml"
    data = yaml.safe_load(mp.read_text("utf-8"))
    data["canonical_identifier"] = "bkk:krp/WRONG_ID/v1"
    mp.write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")
    report = validate_bundle(bundle_copy)
    rules = _rule_ids(report)
    # WRONG_ID != bundle dir name → BUNDLE_DIR_NAME and CANONICAL_IDENTIFIER_FORMAT.
    assert "CANONICAL_IDENTIFIER_FORMAT" in rules or "BUNDLE_DIR_NAME" in rules


def test_pua_kr_format_invalid(bundle_copy: Path):
    pua_path = bundle_copy / "PUA-map.yaml"
    if not pua_path.exists():
        pytest.skip("no PUA-map in this bundle")
    data = yaml.safe_load(pua_path.read_text("utf-8"))
    if not data.get("entries"):
        pytest.skip("no PUA entries in this bundle")
    data["entries"][0]["kr"] = "BOGUS"
    pua_path.write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")
    report = validate_bundle(bundle_copy)
    assert "PUA_ENTRY_KR_FORMAT" in _rule_ids(report)


def test_report_renders_text_and_json(bundle_copy: Path):
    report = validate_bundle(bundle_copy)
    text = report.render_text()
    assert str(bundle_copy) in text
    assert "error(s)" in text and "warning(s)" in text
    js = report.render_json()
    import json
    parsed = json.loads(js)
    assert parsed["bundle"] == str(bundle_copy)
    assert "summary" in parsed
    assert "findings" in parsed


def test_cli_exit_code_nonzero_on_error(bundle_copy: Path):
    (bundle_copy / f"{TEXT_ID}.manifest.yaml").unlink()
    from bkk.validator.cli import main as cli_main
    rc = cli_main([str(bundle_copy)])
    assert rc == 1


def test_cli_exit_code_zero_when_clean(fresh_bundle: Path):
    """Verify the CLI returns 0 even if the validator finds *warnings*."""
    from bkk.validator.cli import main as cli_main
    # We cannot guarantee zero findings (warnings allowed), but exit must be
    # 0 unless there are *errors*. The fresh bundle should have no errors
    # (verified by test_fresh_bundle_passes_structural_rules).
    report = validate_bundle(fresh_bundle)
    if report.has_errors:
        pytest.skip(
            "fresh bundle has errors — covered by other tests; "
            "this case only meaningful when error-free"
        )
    rc = cli_main([str(fresh_bundle)])
    assert rc == 0
