"""Regression tests for ``bkk voice add`` marker-asset writes."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.marker_assets import build_marker_asset
from bkk.voice.cli import _process_one, _run_add
from bkk.voice.problems import (
    read_voice_problems_report,
    write_voice_problems_report,
)


TEXT_ID = "TSTV001"


def _self_hash(juan: dict) -> str:
    data = copy.deepcopy(juan)
    data["hash"] = ZERO_HASH
    return sha256_jcs(data)


def _write_bundle_with_marker_asset(bundle_dir: Path) -> tuple[Path, str]:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    juan = {
        "canonical_identifier": f"bkk:krp/{TEXT_ID}/v1/juan/1",
        "seq": 1,
        "body": {
            "text": "abcdefghij",
            "hash": "sha256:" + "0" * 64,
        },
        "hash": ZERO_HASH,
    }
    juan["hash"] = _self_hash(juan)
    juan_name = f"{TEXT_ID}_001.yaml"
    (bundle_dir / juan_name).write_text(dump(juan), encoding="utf-8")

    marker_asset = build_marker_asset(
        TEXT_ID,
        1,
        None,
        {
            "body": [
                {"type": "punctuation", "offset": 2, "content": "(", "id": ""},
                {"type": "punctuation", "offset": 8, "content": ")", "id": ""},
            ],
        },
    )
    marker_name = f"assets/{TEXT_ID}_001.markers.yaml"
    (bundle_dir / "assets").mkdir()
    (bundle_dir / marker_name).write_text(dump(marker_asset), encoding="utf-8")

    manifest = {
        "canonical_identifier": f"bkk:krp/{TEXT_ID}/v1",
        "assets": {
            "parts": [
                marker_to_flow({
                    "seq": 1,
                    "filename": juan_name,
                    "hash": juan["hash"],
                }),
            ],
            "markers": [
                marker_to_flow({
                    "seq": 1,
                    "role": "markers",
                    "filename": marker_name,
                    "hash": marker_asset["hash"],
                }),
            ],
        },
        "metadata": {
            "title": "Test",
            "identifiers": {"krp": TEXT_ID},
            "edition": {"short": "bkk"},
        },
        "hash": ZERO_HASH,
    }
    manifest["hash"] = manifest_hash(manifest)
    manifest_path = bundle_dir / f"{TEXT_ID}.manifest.yaml"
    manifest_path.write_text(dump(manifest), encoding="utf-8")
    return manifest_path, juan["hash"]


def test_add_writes_voices_to_existing_marker_asset(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    manifest_path, original_juan_hash = _write_bundle_with_marker_asset(bundle)

    stats = _process_one(
        bundle,
        manifest_path,
        TEXT_ID,
        short=None,
        source="parens",
        force=False,
        dry_run=False,
    )

    assert stats["by_name"] == {"note": 1}
    juan = yaml.safe_load((bundle / f"{TEXT_ID}_001.yaml").read_text(encoding="utf-8"))
    assert juan["hash"] == original_juan_hash
    assert "markers" not in juan["body"]

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    asset_entry = manifest["assets"]["markers"][0]
    asset = yaml.safe_load((bundle / asset_entry["filename"]).read_text(encoding="utf-8"))
    body_markers = asset["markers"]["body"]
    assert any(m.get("type") == "punctuation" for m in body_markers)
    assert {
        "type": "voice",
        "offset": 2,
        "length": 6,
        "name": "note",
        "id": "n1",
    } in body_markers
    assert manifest["assets"]["parts"][0]["hash"] == original_juan_hash
    assert asset_entry["hash"] == asset["hash"]
    assert manifest["hash"] == manifest_hash(manifest)


def _write_two_juan_inline_paren_bundle(bundle_dir: Path) -> Path:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    for seq, markers in (
        (1, [
            {"type": "punctuation", "offset": 2, "content": "(", "id": ""},
            {"type": "punctuation", "offset": 8, "content": ")", "id": ""},
        ]),
        (2, [
            {"type": "punctuation", "offset": 3, "content": "(", "id": ""},
        ]),
    ):
        juan = {
            "canonical_identifier": f"bkk:krp/{TEXT_ID}/v1/juan/{seq}",
            "seq": seq,
            "body": {
                "text": "abcdefghij",
                "hash": "sha256:" + "0" * 64,
                "markers": [marker_to_flow(marker) for marker in markers],
            },
            "hash": ZERO_HASH,
        }
        juan["hash"] = _self_hash(juan)
        juan_name = f"{TEXT_ID}_{seq:03d}.yaml"
        (bundle_dir / juan_name).write_text(dump(juan), encoding="utf-8")
        parts.append(marker_to_flow({
            "seq": seq,
            "filename": juan_name,
            "hash": juan["hash"],
        }))

    manifest = {
        "canonical_identifier": f"bkk:krp/{TEXT_ID}/v1",
        "assets": {"parts": parts},
        "metadata": {
            "title": "Test",
            "identifiers": {"krp": TEXT_ID},
            "edition": {"short": "bkk"},
        },
        "hash": ZERO_HASH,
    }
    manifest["hash"] = manifest_hash(manifest)
    manifest_path = bundle_dir / f"{TEXT_ID}.manifest.yaml"
    manifest_path.write_text(dump(manifest), encoding="utf-8")
    return manifest_path


def _asset_markers(bundle: Path, manifest: dict, seq: int) -> list[dict]:
    entry = next(
        item for item in manifest["assets"]["markers"]
        if isinstance(item, dict) and item.get("seq") == seq
    )
    asset = yaml.safe_load((bundle / entry["filename"]).read_text(encoding="utf-8"))
    return asset["markers"]["body"]


def test_add_marks_unresolved_juan_and_writes_resolvable_juans(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    _write_two_juan_inline_paren_bundle(bundle)

    rc = _run_add(
        bundle,
        None,
        source="parens",
        force=False,
        dry_run=False,
    )

    assert rc == 1
    manifest = yaml.safe_load((bundle / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8"))
    seq1_markers = _asset_markers(bundle, manifest, 1)
    assert any(marker.get("type") == "voice" for marker in seq1_markers)
    seq2_markers = _asset_markers(bundle, manifest, 2)
    problem = next(marker for marker in seq2_markers if marker.get("type") == "voice:problem")
    assert problem["offset"] == 3
    assert problem["source"] == "parens"
    assert problem["code"] == "unmatched-open"
    assert problem["id"].startswith(f"{TEXT_ID}_bkk_002-bkkvprob")
    assert manifest["hash"] == manifest_hash(manifest)


def test_add_force_clears_stale_problem_after_marker_fix(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    _write_two_juan_inline_paren_bundle(bundle)
    assert _run_add(bundle, None, source="parens", force=False, dry_run=False) == 1

    juan2_path = bundle / f"{TEXT_ID}_002.yaml"
    juan2 = yaml.safe_load(juan2_path.read_text(encoding="utf-8"))
    juan2["body"]["markers"].append(
        marker_to_flow({"type": "punctuation", "offset": 8, "content": ")", "id": ""})
    )
    juan2["hash"] = _self_hash(juan2)
    juan2_path.write_text(dump(juan2), encoding="utf-8")

    assert _run_add(bundle, None, source="parens", force=True, dry_run=False) == 0
    manifest = yaml.safe_load((bundle / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8"))
    seq2_markers = _asset_markers(bundle, manifest, 2)
    assert not any(marker.get("type") == "voice:problem" for marker in seq2_markers)
    assert any(marker.get("type") == "voice" for marker in seq2_markers)


def test_voice_problems_command_writes_report(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    _write_two_juan_inline_paren_bundle(bundle)
    assert _run_add(bundle, None, source="parens", force=False, dry_run=False) == 1

    report = tmp_path / "voice-problems.jsonl"
    from bkk.voice.cli import run

    rc = run([
        "problems",
        "--corpus", str(tmp_path),
        "--text-id", TEXT_ID,
        "--out", str(report),
    ])

    assert rc == 0
    rows = read_voice_problems_report(report)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == 1
    assert row["textid"] == TEXT_ID
    assert row["seq"] == 2
    assert row["bucket"] == "body"
    assert row["offset"] == 3
    assert row["code"] == "unmatched-open"


def test_voice_problems_text_id_errors_when_bundle_missing(tmp_path: Path) -> None:
    from bkk.voice.cli import run

    report = tmp_path / "voice-problems.jsonl"
    rc = run([
        "problems",
        "--corpus", str(tmp_path),
        "--text-id", "KR0a9999",
        "--out", str(report),
    ])

    assert rc == 2
    assert not report.exists()


def test_add_updates_configured_voice_problem_report(
    tmp_path: Path, monkeypatch,
) -> None:
    bundle = tmp_path / TEXT_ID
    _write_two_juan_inline_paren_bundle(bundle)
    report = tmp_path / "voice-problems.jsonl"
    write_voice_problems_report([
        {
            "id": 1,
            "textid": "OTHER001",
            "title": "Other",
            "edition": None,
            "seq": 1,
            "bucket": "body",
            "offset": 1,
            "length": 0,
            "marker_id": "OTHER001_bkk_001-bkkvprob1",
            "source": "parens",
            "code": "unmatched-open",
            "message": "other",
        },
    ], report)
    monkeypatch.setenv("BKK_VOICE_PROBLEMS_REPORT", str(report))

    assert _run_add(bundle, None, source="parens", force=False, dry_run=False) == 1

    rows = read_voice_problems_report(report)
    assert [row["textid"] for row in rows] == ["OTHER001", TEXT_ID]
    row = next(row for row in rows if row["textid"] == TEXT_ID)
    assert row["seq"] == 2
    assert row["offset"] == 3
    assert row["code"] == "unmatched-open"


def test_add_clears_configured_voice_problem_report_for_clean_bundle(
    tmp_path: Path, monkeypatch,
) -> None:
    bundle = tmp_path / TEXT_ID
    _write_two_juan_inline_paren_bundle(bundle)
    report = tmp_path / "voice-problems.jsonl"
    monkeypatch.setenv("BKK_VOICE_PROBLEMS_REPORT", str(report))
    assert _run_add(bundle, None, source="parens", force=False, dry_run=False) == 1

    juan2_path = bundle / f"{TEXT_ID}_002.yaml"
    juan2 = yaml.safe_load(juan2_path.read_text(encoding="utf-8"))
    juan2["body"]["markers"].append(
        marker_to_flow({"type": "punctuation", "offset": 8, "content": ")", "id": ""})
    )
    juan2["hash"] = _self_hash(juan2)
    juan2_path.write_text(dump(juan2), encoding="utf-8")

    assert _run_add(bundle, None, source="parens", force=True, dry_run=False) == 0

    assert read_voice_problems_report(report) == []
