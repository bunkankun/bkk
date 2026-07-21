from __future__ import annotations

from pathlib import Path

import yaml

from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.marker_assets import load_marker_asset, marker_asset_hash
from bkk.repair.cli import run as repair_run
from bkk.repair.front_body import move_front_to_empty_body
from bkk.validator import validate_bundle


TEXT_ID = "KR0fb001"


def _self_hash(data: dict) -> str:
    zeroed = dict(data)
    zeroed["hash"] = ZERO_HASH
    return sha256_jcs(zeroed)


def _write_bundle(
    root: Path, *, text_id: str = TEXT_ID, body_text: str = "",
    invalid_front_marker: bool = False,
) -> Path:
    bundle_dir = root / text_id
    bundle_dir.mkdir()
    head_id = f"{text_id}_T_001-head"
    juan = {
        "canonical_identifier": f"bkk:krp/{text_id}/bkk/v1/juan/1",
        "seq": 1,
        "front": {
            "text": "甲乙",
            "hash": sha256_text("甲乙"),
            "markers": [
                marker_to_flow({
                    "type": "tls:head",
                    "offset": 0,
                    "content": "卷一",
                    "id": head_id,
                }),
            ],
        },
        "body": {
            "text": body_text,
            "hash": sha256_text(body_text) if body_text else ZERO_HASH,
        },
        "metadata": {"title": "Front Body", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    juan["hash"] = _self_hash(juan)
    juan_name = f"{text_id}_001.yaml"
    (bundle_dir / juan_name).write_text(dump(juan), encoding="utf-8")

    asset = {
        "canonical_identifier": f"bkk:krp/{text_id}/bkk/v1/markers/1",
        "seq": 1,
        "markers": {
            "front": [
                marker_to_flow({
                    "type": "punctuation",
                    "offset": 3 if invalid_front_marker else 1,
                    "content": "、",
                    "id": f"{text_id}_T_001-p",
                }),
            ],
            "body": [
                marker_to_flow({
                    "type": "line-break",
                    "offset": len(body_text),
                    "id": f"{text_id}_T_001-lb",
                }),
            ],
        },
        "hash": ZERO_HASH,
    }
    asset["hash"] = marker_asset_hash(asset)
    asset_name = f"assets/{text_id}_001.markers.yaml"
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
        "table_of_contents": [
            {
                "ref": marker_to_flow({
                    "seq": 1,
                    "marker_id": head_id,
                    "span": ["front", 0, 2],
                }),
                "label": "卷一",
                "type": "section",
                "level": 1,
            }
        ],
        "metadata": {"title": "Front Body", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    manifest["hash"] = manifest_hash(manifest)
    (bundle_dir / f"{text_id}.manifest.yaml").write_text(
        dump(manifest), encoding="utf-8",
    )
    return bundle_dir


def test_front_to_body_dry_run_reports_without_writing(tmp_path: Path):
    bundle_dir = _write_bundle(tmp_path)
    before_juan = (bundle_dir / f"{TEXT_ID}_001.yaml").read_text("utf-8")
    before_manifest = (bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text("utf-8")

    summary = move_front_to_empty_body(bundle_dir)

    assert summary["dry_run"] is True
    assert summary["scopes"][0]["moved"] == 1
    assert summary["scopes"][0]["chars"] == 2
    assert (bundle_dir / f"{TEXT_ID}_001.yaml").read_text("utf-8") == before_juan
    assert (bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text("utf-8") == before_manifest


def test_front_to_body_write_moves_text_markers_toc_and_hashes(tmp_path: Path):
    bundle_dir = _write_bundle(tmp_path)

    summary = move_front_to_empty_body(bundle_dir, dry_run=False)

    assert summary["scopes"][0]["moved"] == 1
    juan = yaml.safe_load((bundle_dir / f"{TEXT_ID}_001.yaml").read_text("utf-8"))
    manifest = yaml.safe_load((bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text("utf-8"))
    asset = load_marker_asset(bundle_dir, manifest, 1)

    assert "front" not in juan
    assert juan["body"]["text"] == "甲乙"
    assert juan["body"]["hash"] == sha256_text("甲乙")
    assert [m["type"] for m in juan["body"]["markers"]] == ["tls:head"]
    assert manifest["table_of_contents"][0]["ref"]["span"] == ["body", 0, 2]
    assert manifest["assets"]["parts"][0]["hash"] == juan["hash"]
    assert manifest["hash"] == manifest_hash(manifest)
    assert asset is not None
    assert "front" not in asset["markers"]
    assert asset["markers"]["body"] == [
        {"type": "punctuation", "offset": 1, "content": "、", "id": f"{TEXT_ID}_T_001-p"},
        {"type": "line-break", "offset": 2, "id": f"{TEXT_ID}_T_001-lb"},
    ]
    assert manifest["assets"]["markers"][0]["hash"] == marker_asset_hash(asset)
    report = validate_bundle(bundle_dir)
    assert not report.has_errors, report.render_text()


def test_front_to_body_skips_non_empty_body(tmp_path: Path):
    bundle_dir = _write_bundle(tmp_path, body_text="丙")

    summary = move_front_to_empty_body(bundle_dir, dry_run=False)

    assert summary["scopes"][0]["moved"] == 0
    juan = yaml.safe_load((bundle_dir / f"{TEXT_ID}_001.yaml").read_text("utf-8"))
    assert juan["front"]["text"] == "甲乙"
    assert juan["body"]["text"] == "丙"


def test_front_to_body_cli_defaults_to_dry_run(tmp_path: Path, capsys):
    bundle_dir = _write_bundle(tmp_path)

    rc = repair_run(["front-to-body", "--bundle", str(bundle_dir)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "would move" in out
    assert "pass --write" in out
    juan = yaml.safe_load((bundle_dir / f"{TEXT_ID}_001.yaml").read_text("utf-8"))
    assert "front" in juan


def test_front_to_body_cli_write_updates_bundle(tmp_path: Path):
    bundle_dir = _write_bundle(tmp_path)

    rc = repair_run(["front-to-body", "--bundle", str(bundle_dir), "--write"])

    assert rc == 0
    juan = yaml.safe_load((bundle_dir / f"{TEXT_ID}_001.yaml").read_text("utf-8"))
    assert "front" not in juan
    assert juan["body"]["text"] == "甲乙"


def test_front_to_body_cli_text_prefix_scans_corpus_root(tmp_path: Path, capsys):
    changed = _write_bundle(tmp_path, text_id="KR0fb001")
    unchanged = _write_bundle(tmp_path, text_id="KR0fb002", body_text="丙")

    rc = repair_run([
        "front-to-body",
        "--out",
        str(tmp_path),
        "--text-prefix",
        "KR0fb",
        "--write",
    ])

    assert rc == 0
    out = capsys.readouterr().out
    assert "KR0fb001:" in out
    assert "KR0fb002:" not in out
    assert "1 juans, 2 chars" in out
    changed_juan = yaml.safe_load((changed / "KR0fb001_001.yaml").read_text("utf-8"))
    unchanged_juan = yaml.safe_load((unchanged / "KR0fb002_001.yaml").read_text("utf-8"))
    assert "front" not in changed_juan
    assert unchanged_juan["front"]["text"] == "甲乙"


def test_front_to_body_cli_defaults_to_whole_corpus(tmp_path: Path, capsys):
    changed = _write_bundle(tmp_path, text_id="KR0fb001")
    unchanged = _write_bundle(tmp_path, text_id="KR1fb001", body_text="丙")

    rc = repair_run(["front-to-body", "--out", str(tmp_path), "--write"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "KR0fb001:" in out
    assert "KR1fb001:" not in out
    assert "(scanned 2 bundles in corpus)" in out
    changed_juan = yaml.safe_load((changed / "KR0fb001_001.yaml").read_text("utf-8"))
    unchanged_juan = yaml.safe_load((unchanged / "KR1fb001_001.yaml").read_text("utf-8"))
    assert "front" not in changed_juan
    assert unchanged_juan["front"]["text"] == "甲乙"


def test_front_to_body_cli_skips_bad_juan_and_continues_corpus(
    tmp_path: Path, capsys,
):
    bad = _write_bundle(tmp_path, text_id="KR0fb001", invalid_front_marker=True)
    good = _write_bundle(tmp_path, text_id="KR0fb002")

    rc = repair_run(["front-to-body", "--out", str(tmp_path), "--write"])

    assert rc == 1
    out = capsys.readouterr().out
    assert "KR0fb001:" in out
    assert "skipped" in out
    assert "id='KR0fb001_T_001-p'" in out
    assert "offset=3" in out
    assert "front.text length 2" in out
    assert "KR0fb002:" in out
    assert "(1 skipped; scanned 2 bundles in corpus)" in out
    bad_juan = yaml.safe_load((bad / "KR0fb001_001.yaml").read_text("utf-8"))
    good_juan = yaml.safe_load((good / "KR0fb002_001.yaml").read_text("utf-8"))
    assert bad_juan["front"]["text"] == "甲乙"
    assert "front" not in good_juan
