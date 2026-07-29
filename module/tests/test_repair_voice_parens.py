from __future__ import annotations

from pathlib import Path

import yaml

from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.marker_assets import load_marker_asset, marker_asset_hash
from bkk.repair.cli import run as repair_run
from bkk.repair.voice_parens import move_body_initial_close_parens_from_report
from bkk.validator import validate_bundle
from bkk.voice.problems import write_voice_problems_report


TEXT_ID = "KR0vp001"


def _self_hash(data: dict) -> str:
    zeroed = dict(data)
    zeroed["hash"] = ZERO_HASH
    return sha256_jcs(zeroed)


def _write_bundle(root: Path, *, text_id: str = TEXT_ID) -> Path:
    bundle_dir = root / text_id
    bundle_dir.mkdir()
    juan_name = f"{text_id}_001.yaml"
    asset_name = f"assets/{text_id}_001.markers.yaml"

    juan = {
        "canonical_identifier": f"bkk:krp/{text_id}/bkk/v1/juan/1",
        "seq": 1,
        "front": {
            "text": "甲乙",
            "hash": sha256_text("甲乙"),
        },
        "body": {
            "text": "丙丁",
            "hash": sha256_text("丙丁"),
        },
        "metadata": {"title": "Voice Parens", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    juan["hash"] = _self_hash(juan)
    (bundle_dir / juan_name).write_text(dump(juan), encoding="utf-8")

    asset = {
        "canonical_identifier": f"bkk:krp/{text_id}/bkk/v1/markers/1",
        "seq": 1,
        "markers": {
            "front": [
                marker_to_flow({
                    "type": "punctuation",
                    "offset": 1,
                    "content": "(",
                    "id": f"{text_id}_T_001-p1",
                }),
                marker_to_flow({
                    "type": "voice:problem",
                    "offset": 1,
                    "length": 0,
                    "id": f"{text_id}_bkk_001-bkkvprob1",
                    "source": "parens",
                    "bucket": "front",
                    "code": "unmatched-open",
                    "message": "unmatched '(' at offset 1",
                }),
            ],
            "body": [
                marker_to_flow({
                    "type": "punctuation",
                    "offset": 0,
                    "content": ")",
                    "id": f"{text_id}_T_001-p2",
                }),
                marker_to_flow({
                    "type": "line-break",
                    "offset": 0,
                    "id": f"{text_id}_T_001-lb",
                }),
                marker_to_flow({
                    "type": "voice:problem",
                    "offset": 0,
                    "length": 0,
                    "id": f"{text_id}_bkk_001-bkkvprob2",
                    "source": "parens",
                    "bucket": "body",
                    "code": "stray-close",
                    "message": "unexpected ')' at offset 0 with no matching '('",
                }),
            ],
        },
        "hash": ZERO_HASH,
    }
    asset["hash"] = marker_asset_hash(asset)
    (bundle_dir / "assets").mkdir()
    (bundle_dir / asset_name).write_text(dump(asset), encoding="utf-8")

    manifest = {
        "canonical_identifier": f"bkk:krp/{text_id}/v1",
        "canonical_location": f"https://kanripo.org/bkk/{text_id}/v1",
        "canonical_set": {"identifier": "bkk:charset/cjk-v1", "hash": ZERO_HASH},
        "assets": {
            "parts": [
                marker_to_flow({"seq": 1, "filename": juan_name, "hash": juan["hash"]}),
            ],
            "markers": [
                marker_to_flow({
                    "seq": 1,
                    "role": "markers",
                    "filename": asset_name,
                    "hash": asset["hash"],
                }),
            ],
        },
        "table_of_contents": [],
        "metadata": {"title": "Voice Parens", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    manifest["hash"] = manifest_hash(manifest)
    (bundle_dir / f"{text_id}.manifest.yaml").write_text(
        dump(manifest), encoding="utf-8",
    )
    return bundle_dir


def _write_report(path: Path, *, text_id: str = TEXT_ID) -> None:
    write_voice_problems_report([
        {
            "id": 1,
            "textid": text_id,
            "title": "Voice Parens",
            "edition": None,
            "seq": 1,
            "bucket": "body",
            "offset": 0,
            "length": 0,
            "marker_id": f"{text_id}_bkk_001-bkkvprob2",
            "source": "parens",
            "code": "stray-close",
            "message": "unexpected ')' at offset 0 with no matching '('",
        },
        {
            "id": 2,
            "textid": text_id,
            "title": "Voice Parens",
            "edition": None,
            "seq": 1,
            "bucket": "front",
            "offset": 1,
            "length": 0,
            "marker_id": f"{text_id}_bkk_001-bkkvprob1",
            "source": "parens",
            "code": "unmatched-open",
            "message": "unmatched '(' at offset 1",
        },
    ], path)


def test_voice_paren_boundary_dry_run_reports_without_writing(tmp_path: Path):
    bundle_dir = _write_bundle(tmp_path)
    report = tmp_path / "voice-problems.jsonl"
    _write_report(report)
    before_asset = (bundle_dir / f"assets/{TEXT_ID}_001.markers.yaml").read_text("utf-8")
    before_manifest = (bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text("utf-8")

    summary = move_body_initial_close_parens_from_report(tmp_path, report)

    assert summary["dry_run"] is True
    assert summary["moved"] == 1
    assert (bundle_dir / f"assets/{TEXT_ID}_001.markers.yaml").read_text("utf-8") == before_asset
    assert (bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text("utf-8") == before_manifest


def test_voice_paren_boundary_write_moves_close_to_front_and_updates_hashes(
    tmp_path: Path,
):
    bundle_dir = _write_bundle(tmp_path)
    report = tmp_path / "voice-problems.jsonl"
    _write_report(report)

    summary = move_body_initial_close_parens_from_report(
        tmp_path,
        report,
        dry_run=False,
    )

    assert summary["moved"] == 1
    manifest = yaml.safe_load((bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text("utf-8"))
    asset = load_marker_asset(bundle_dir, manifest, 1)
    assert asset is not None
    assert [
        (marker["content"], marker["offset"])
        for marker in asset["markers"]["front"]
        if marker["type"] == "punctuation"
    ] == [("(", 1), (")", 2)]
    assert [
        marker
        for marker in asset["markers"]["body"]
        if marker.get("type") == "punctuation" and marker.get("content") == ")"
    ] == []
    assert manifest["assets"]["markers"][0]["hash"] == marker_asset_hash(asset)
    assert manifest["hash"] == manifest_hash(manifest)
    report_result = validate_bundle(bundle_dir)
    assert not report_result.has_errors, report_result.render_text()


def test_voice_paren_boundary_requires_paired_report_rows(tmp_path: Path):
    bundle_dir = _write_bundle(tmp_path)
    report = tmp_path / "voice-problems.jsonl"
    write_voice_problems_report([
        {
            "id": 1,
            "textid": TEXT_ID,
            "title": "Voice Parens",
            "edition": None,
            "seq": 1,
            "bucket": "body",
            "offset": 0,
            "length": 0,
            "marker_id": f"{TEXT_ID}_bkk_001-bkkvprob2",
            "source": "parens",
            "code": "stray-close",
            "message": "unexpected ')' at offset 0 with no matching '('",
        },
    ], report)

    summary = move_body_initial_close_parens_from_report(
        tmp_path,
        report,
        dry_run=False,
    )

    assert summary["targets"] == 0
    manifest = yaml.safe_load((bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text("utf-8"))
    asset = load_marker_asset(bundle_dir, manifest, 1)
    assert asset is not None
    assert [
        marker["offset"]
        for marker in asset["markers"]["body"]
        if marker.get("type") == "punctuation" and marker.get("content") == ")"
    ] == [0]


def test_voice_paren_boundary_cli_text_id(tmp_path: Path, capsys):
    _write_bundle(tmp_path)
    report = tmp_path / "voice-problems.jsonl"
    _write_report(report)

    rc = repair_run([
        "voice-paren-boundary",
        "--out",
        str(tmp_path),
        "--report",
        str(report),
        "--text-id",
        TEXT_ID,
        "--write",
    ])

    assert rc == 0
    out = capsys.readouterr().out
    assert "1 close-paren marker(s) moved" in out
