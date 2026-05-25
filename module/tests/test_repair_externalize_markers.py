from __future__ import annotations

from pathlib import Path

import yaml

from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.marker_assets import load_marker_asset, hydrate_juan_markers
from bkk.repair.markers import externalize_markers
from bkk.validator import validate_bundle


TEXT_ID = "KR0mig01"


def _self_hash(data: dict) -> str:
    zeroed = dict(data)
    zeroed["hash"] = ZERO_HASH
    return sha256_jcs(zeroed)


def _write_inline_bundle(root: Path) -> Path:
    bundle_dir = root / TEXT_ID
    bundle_dir.mkdir()
    head_id = f"{TEXT_ID}_T_001-h"
    juan = {
        "canonical_identifier": f"bkk:krp/{TEXT_ID}/bkk/v1/juan/1",
        "seq": 1,
        "body": {
            "text": "甲乙丙",
            "hash": sha256_text("甲乙丙"),
            "markers": [
                marker_to_flow({"type": "tls:head", "offset": 0, "content": "卷一", "id": head_id}),
                marker_to_flow({"type": "punctuation", "offset": 1, "content": "、"}),
                marker_to_flow({"type": "line-break", "offset": 3}),
            ],
        },
        "metadata": {"title": "Migration", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    juan["hash"] = _self_hash(juan)
    juan_name = f"{TEXT_ID}_001.yaml"
    (bundle_dir / juan_name).write_text(dump(juan), encoding="utf-8")

    manifest = {
        "canonical_identifier": f"bkk:krp/{TEXT_ID}/v1",
        "canonical_location": f"https://kanripo.org/bkk/{TEXT_ID}/v1",
        "canonical_set": {"identifier": "bkk:charset/cjk-v1", "hash": ZERO_HASH},
        "assets": {"parts": [marker_to_flow({"seq": 1, "filename": juan_name, "hash": juan["hash"]})]},
        "table_of_contents": [
            {
                "ref": marker_to_flow({"seq": 1, "marker_id": head_id, "span": ["body", 0, 3]}),
                "label": "卷一",
                "type": "section",
                "level": 1,
            }
        ],
        "metadata": {"title": "Migration", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    manifest["hash"] = manifest_hash(manifest)
    (bundle_dir / f"{TEXT_ID}.manifest.yaml").write_text(
        dump(manifest), encoding="utf-8",
    )
    return bundle_dir


def test_externalize_markers_migrates_inline_bundle(tmp_path: Path):
    bundle_dir = _write_inline_bundle(tmp_path)
    summary = externalize_markers(bundle_dir)
    assert summary["scopes"][0]["moved"] == 2
    assert summary["scopes"][0]["kept"] == 1

    manifest = yaml.safe_load((bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text("utf-8"))
    assert manifest["assets"]["markers"][0]["filename"] == f"assets/{TEXT_ID}_001.markers.yaml"
    juan = yaml.safe_load((bundle_dir / f"{TEXT_ID}_001.yaml").read_text("utf-8"))
    assert [m["type"] for m in juan["body"]["markers"]] == ["tls:head"]

    asset = load_marker_asset(bundle_dir, manifest, 1)
    hydrated = hydrate_juan_markers(juan, asset)
    assert [m["type"] for m in hydrated["body"]["markers"]] == [
        "tls:head",
        "punctuation",
        "line-break",
    ]
    report = validate_bundle(bundle_dir)
    assert not report.has_errors, report.render_text()


def test_externalize_markers_dry_run_writes_nothing(tmp_path: Path):
    bundle_dir = _write_inline_bundle(tmp_path)
    before = (bundle_dir / f"{TEXT_ID}_001.yaml").read_text("utf-8")
    summary = externalize_markers(bundle_dir, dry_run=True)
    assert summary["dry_run"] is True
    assert not (bundle_dir / "assets").exists()
    assert (bundle_dir / f"{TEXT_ID}_001.yaml").read_text("utf-8") == before
