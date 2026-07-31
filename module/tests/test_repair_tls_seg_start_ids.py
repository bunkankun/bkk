from __future__ import annotations

from pathlib import Path

import yaml

from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.marker_assets import load_marker_asset, marker_asset_hash
from bkk.repair.cli import run as repair_run
from bkk.repair.tls_seg_start_ids import repair_tls_seg_start_ids


TEXT_ID = "KR0ts001"


def _self_hash(data: dict) -> str:
    zeroed = dict(data)
    zeroed["hash"] = ZERO_HASH
    return sha256_jcs(zeroed)


def _write_bundle(
    root: Path,
    *,
    text_id: str = TEXT_ID,
    duplicated: bool = True,
) -> Path:
    bundle_dir = root / text_id
    bundle_dir.mkdir()
    juan_name = f"{text_id}_001.yaml"
    asset_name = f"assets/{text_id}_001.markers.yaml"

    juan = {
        "canonical_identifier": f"bkk:krp/{text_id}/bkk/v1/juan/1",
        "seq": 1,
        "body": {
            "text": "甲乙",
            "hash": sha256_text("甲乙"),
        },
        "metadata": {"title": "TLS Seg Start", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    juan["hash"] = _self_hash(juan)
    (bundle_dir / juan_name).write_text(dump(juan), encoding="utf-8")

    start_id = "A" if duplicated else "A_start"
    asset = {
        "canonical_identifier": f"bkk:krp/{text_id}/bkk/v1/markers/1",
        "seq": 1,
        "markers": {
            "front": [],
            "body": [
                marker_to_flow({
                    "type": "tls:seg-start",
                    "offset": 0,
                    "content": "",
                    "id": start_id,
                    "seg_type": "comm",
                    "member_ids": ["A", "B"],
                }),
                marker_to_flow({
                    "type": "tls:seg",
                    "offset": 0,
                    "content": "",
                    "id": "A",
                }),
                marker_to_flow({
                    "type": "tls:seg",
                    "offset": 1,
                    "content": "",
                    "id": "B",
                }),
                marker_to_flow({
                    "type": "tls:seg-end",
                    "offset": 2,
                    "content": "",
                    "id": "B_end",
                    "seg_type": "comm",
                }),
            ],
            "back": [],
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
        "metadata": {"title": "TLS Seg Start", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    manifest["hash"] = manifest_hash(manifest)
    (bundle_dir / f"{text_id}.manifest.yaml").write_text(dump(manifest), encoding="utf-8")
    return bundle_dir


def test_tls_seg_start_ids_dry_run_reports_without_writing(tmp_path: Path):
    bundle_dir = _write_bundle(tmp_path)
    before_asset = (bundle_dir / f"assets/{TEXT_ID}_001.markers.yaml").read_text("utf-8")
    before_manifest = (bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text("utf-8")

    summary = repair_tls_seg_start_ids(bundle_dir)

    assert summary["dry_run"] is True
    assert summary["scopes"][0]["renamed"] == 1
    assert (bundle_dir / f"assets/{TEXT_ID}_001.markers.yaml").read_text("utf-8") == before_asset
    assert (bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text("utf-8") == before_manifest


def test_tls_seg_start_ids_write_renames_asset_marker_and_updates_hashes(tmp_path: Path):
    bundle_dir = _write_bundle(tmp_path)

    summary = repair_tls_seg_start_ids(bundle_dir, dry_run=False)

    assert summary["scopes"][0]["renamed"] == 1
    manifest = yaml.safe_load((bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text("utf-8"))
    asset = load_marker_asset(bundle_dir, manifest, 1)
    assert asset is not None
    body = asset["markers"]["body"]
    assert body[0]["id"] == "A_start"
    assert [marker["id"] for marker in body if marker.get("id")] == [
        "A_start",
        "A",
        "B",
        "B_end",
    ]
    assert manifest["assets"]["markers"][0]["hash"] == marker_asset_hash(asset)
    assert manifest["hash"] == manifest_hash(manifest)


def test_tls_seg_start_ids_skips_current_import_shape(tmp_path: Path):
    bundle_dir = _write_bundle(tmp_path, duplicated=False)

    summary = repair_tls_seg_start_ids(bundle_dir, dry_run=False)

    assert summary["scopes"][0]["renamed"] == 0
    manifest = yaml.safe_load((bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text("utf-8"))
    asset = load_marker_asset(bundle_dir, manifest, 1)
    assert asset is not None
    assert asset["markers"]["body"][0]["id"] == "A_start"


def test_tls_seg_start_ids_cli_text_prefix_scans_corpus_root(tmp_path: Path, capsys):
    changed = _write_bundle(tmp_path, text_id="KR0ts001")
    unchanged = _write_bundle(tmp_path, text_id="KR0ts002", duplicated=False)

    rc = repair_run([
        "tls-seg-start-ids",
        "--out",
        str(tmp_path),
        "--text-prefix",
        "KR0ts",
        "--write",
    ])

    assert rc == 0
    out = capsys.readouterr().out
    assert "KR0ts001:" in out
    assert "KR0ts002:" not in out
    assert "1 tls:seg-start marker id(s)" in out
    changed_manifest = yaml.safe_load((changed / "KR0ts001.manifest.yaml").read_text("utf-8"))
    unchanged_manifest = yaml.safe_load((unchanged / "KR0ts002.manifest.yaml").read_text("utf-8"))
    changed_asset = load_marker_asset(changed, changed_manifest, 1)
    unchanged_asset = load_marker_asset(unchanged, unchanged_manifest, 1)
    assert changed_asset is not None
    assert unchanged_asset is not None
    assert changed_asset["markers"]["body"][0]["id"] == "A_start"
    assert unchanged_asset["markers"]["body"][0]["id"] == "A_start"
