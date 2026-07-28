from __future__ import annotations

from pathlib import Path

import yaml

from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.marker_assets import load_marker_asset, marker_asset_hash
from bkk.repair.cli import run as repair_run
from bkk.repair.page_break import synthesize_missing_page_breaks
from bkk.validator import validate_bundle


TEXT_ID = "KR0pb001"


def _self_hash(data: dict) -> str:
    zeroed = dict(data)
    zeroed["hash"] = ZERO_HASH
    return sha256_jcs(zeroed)


def _write_bundle(
    root: Path,
    *,
    text_id: str = TEXT_ID,
    include_existing_first_page: bool = False,
) -> Path:
    bundle_dir = root / text_id
    bundle_dir.mkdir()
    juan_name = f"{text_id}_001.yaml"
    asset_name = f"assets/{text_id}_001.markers.yaml"

    juan = {
        "canonical_identifier": f"bkk:krp/{text_id}/bkk/v1/juan/1",
        "seq": 1,
        "body": {
            "text": "甲乙丙丁",
            "hash": sha256_text("甲乙丙丁"),
        },
        "metadata": {"title": "Page Break", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    juan["hash"] = _self_hash(juan)
    (bundle_dir / juan_name).write_text(dump(juan), encoding="utf-8")

    body_markers = []
    if include_existing_first_page:
        body_markers.append(marker_to_flow({
            "type": "page-break",
            "offset": 0,
            "content": "",
            "id": f"{text_id}_WYG_001-1a",
            "image": "WYG0001/WYG0001-0001a.png",
        }))
    body_markers.extend([
        marker_to_flow({
            "type": "kr:newline",
            "offset": 0,
            "content": "\n\n",
            "id": f"{text_id}_krp_001-bkkkrnewl1",
        }),
        marker_to_flow({
            "type": "line-break",
            "offset": 0,
            "content": "",
            "id": f"{text_id}_WYG_001-1a01",
        }),
        marker_to_flow({
            "type": "line-break",
            "offset": 2,
            "content": "",
            "id": f"{text_id}_WYG_001-1a02",
        }),
        marker_to_flow({
            "type": "page-break",
            "offset": 4,
            "content": "",
            "id": f"{text_id}_WYG_001-1b",
            "image": "WYG0001/WYG0001-0001b.png",
        }),
        marker_to_flow({
            "type": "line-break",
            "offset": 4,
            "content": "",
            "id": f"{text_id}_WYG_001-1b01",
        }),
    ])
    asset = {
        "canonical_identifier": f"bkk:krp/{text_id}/bkk/v1/markers/1",
        "seq": 1,
        "markers": {
            "front": [],
            "body": body_markers,
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
        "metadata": {"title": "Page Break", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    manifest["hash"] = manifest_hash(manifest)
    (bundle_dir / f"{text_id}.manifest.yaml").write_text(
        dump(manifest), encoding="utf-8",
    )
    return bundle_dir


def test_page_break_dry_run_reports_without_writing(tmp_path: Path):
    bundle_dir = _write_bundle(tmp_path)
    before_asset = (bundle_dir / f"assets/{TEXT_ID}_001.markers.yaml").read_text("utf-8")
    before_manifest = (bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text("utf-8")

    summary = synthesize_missing_page_breaks(bundle_dir)

    assert summary["dry_run"] is True
    assert summary["scopes"][0]["inserted"] == 1
    assert (bundle_dir / f"assets/{TEXT_ID}_001.markers.yaml").read_text("utf-8") == before_asset
    assert (bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text("utf-8") == before_manifest


def test_page_break_write_inserts_before_first_line_break_and_updates_hashes(tmp_path: Path):
    bundle_dir = _write_bundle(tmp_path)

    summary = synthesize_missing_page_breaks(bundle_dir, dry_run=False)

    assert summary["scopes"][0]["inserted"] == 1
    manifest = yaml.safe_load((bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text("utf-8"))
    asset = load_marker_asset(bundle_dir, manifest, 1)
    assert asset is not None
    body = asset["markers"]["body"]
    assert [marker["type"] for marker in body[:4]] == [
        "kr:newline",
        "page-break",
        "line-break",
        "line-break",
    ]
    assert body[1] == {
        "type": "page-break",
        "offset": 0,
        "content": "",
        "id": f"{TEXT_ID}_WYG_001-1a",
        "image": "WYG0001/WYG0001-0001a.png",
    }
    assert [marker["id"] for marker in body if marker["type"] == "page-break"] == [
        f"{TEXT_ID}_WYG_001-1a",
        f"{TEXT_ID}_WYG_001-1b",
    ]
    assert manifest["assets"]["markers"][0]["hash"] == marker_asset_hash(asset)
    assert manifest["hash"] == manifest_hash(manifest)
    report = validate_bundle(bundle_dir)
    assert not report.has_errors, report.render_text()


def test_page_break_skips_when_matching_page_break_exists(tmp_path: Path):
    bundle_dir = _write_bundle(tmp_path, include_existing_first_page=True)

    summary = synthesize_missing_page_breaks(bundle_dir, dry_run=False)

    assert summary["scopes"][0]["inserted"] == 0
    manifest = yaml.safe_load((bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text("utf-8"))
    asset = load_marker_asset(bundle_dir, manifest, 1)
    assert asset is not None
    page_breaks = [
        marker for marker in asset["markers"]["body"]
        if marker["type"] == "page-break"
    ]
    assert [marker["id"] for marker in page_breaks] == [
        f"{TEXT_ID}_WYG_001-1a",
        f"{TEXT_ID}_WYG_001-1b",
    ]


def test_page_break_cli_text_prefix_scans_corpus_root(tmp_path: Path, capsys):
    changed = _write_bundle(tmp_path, text_id="KR0pb001")
    unchanged = _write_bundle(
        tmp_path,
        text_id="KR0pb002",
        include_existing_first_page=True,
    )

    rc = repair_run([
        "page-break",
        "--out",
        str(tmp_path),
        "--text-prefix",
        "KR0pb",
        "--write",
    ])

    assert rc == 0
    out = capsys.readouterr().out
    assert "KR0pb001:" in out
    assert "KR0pb002:" not in out
    assert "1 page-break marker(s)" in out
    changed_manifest = yaml.safe_load((changed / "KR0pb001.manifest.yaml").read_text("utf-8"))
    unchanged_manifest = yaml.safe_load((unchanged / "KR0pb002.manifest.yaml").read_text("utf-8"))
    changed_asset = load_marker_asset(changed, changed_manifest, 1)
    unchanged_asset = load_marker_asset(unchanged, unchanged_manifest, 1)
    assert changed_asset is not None
    assert unchanged_asset is not None
    assert len([
        marker for marker in changed_asset["markers"]["body"]
        if marker["type"] == "page-break"
    ]) == 2
    assert len([
        marker for marker in unchanged_asset["markers"]["body"]
        if marker["type"] == "page-break"
    ]) == 2
