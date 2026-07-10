"""Regression tests for ``bkk voice add`` marker-asset writes."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.marker_assets import build_marker_asset
from bkk.voice.cli import _process_one


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
