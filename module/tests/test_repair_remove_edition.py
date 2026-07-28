from __future__ import annotations

from pathlib import Path

import yaml

from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.marker_assets import marker_asset_hash
from bkk.repair.cli import run as repair_run
from bkk.repair.remove_edition import remove_edition


TEXT_ID = "KR0ed001"


def _self_hash(data: dict) -> str:
    zeroed = dict(data)
    zeroed["hash"] = ZERO_HASH
    return sha256_jcs(zeroed)


def _write_juan(root: Path, text_id: str, edition: str | None = None) -> tuple[str, str]:
    suffix = f"-{edition}" if edition else ""
    filename = f"{text_id}_001{suffix}.yaml"
    slug = edition or "bkk"
    juan = {
        "canonical_identifier": f"bkk:krp/{text_id}/{slug}/v1/juan/1",
        "seq": 1,
        "body": {
            "text": "甲乙丙",
            "hash": sha256_text("甲乙丙"),
            "markers": [
                marker_to_flow({
                    "type": "tls:head",
                    "offset": 0,
                    "content": "卷一",
                    "id": f"{text_id}_T_001-h",
                }),
                marker_to_flow({
                    "type": "variant",
                    "offset": 1,
                    "length": 1,
                    "content": "乙",
                    "A": "二",
                    "B": "三",
                }),
            ],
        },
        "metadata": {"title": "Remove Edition", "edition": {"short": slug}},
        "hash": ZERO_HASH,
    }
    juan["hash"] = _self_hash(juan)
    (root / filename).write_text(dump(juan), encoding="utf-8")
    return filename, juan["hash"]


def _write_manifest(
    root: Path,
    text_id: str,
    juan_name: str,
    juan_hash: str,
    *,
    edition: str | None = None,
    marker_name: str | None = None,
    marker_hash: str | None = None,
    editions: list[dict] | None = None,
) -> None:
    assets = {
        "parts": [
            marker_to_flow({"seq": 1, "filename": juan_name, "hash": juan_hash}),
        ],
    }
    if marker_name and marker_hash:
        assets["markers"] = [
            marker_to_flow({
                "seq": 1,
                "role": "markers",
                "filename": marker_name,
                "hash": marker_hash,
            }),
        ]
    manifest = {
        "canonical_identifier": (
            f"bkk:krp/{text_id}/{edition}/v1" if edition else f"bkk:krp/{text_id}/v1"
        ),
        "canonical_location": (
            f"https://kanripo.org/bkk/{text_id}/{edition}/v1"
            if edition
            else f"https://kanripo.org/bkk/{text_id}/v1"
        ),
        "canonical_set": {"identifier": "bkk:charset/cjk-v1", "hash": ZERO_HASH},
        "metadata": {
            "title": "Remove Edition",
            "edition": {"short": edition or "bkk"},
        },
        "assets": assets,
        "table_of_contents": [
            {
                "ref": marker_to_flow({
                    "seq": 1,
                    "marker_id": f"{text_id}_T_001-h",
                    "span": ["body", 0, 3],
                }),
                "label": "卷一",
                "type": "section",
                "level": 1,
            },
        ],
        "hash": ZERO_HASH,
    }
    if editions:
        manifest["editions"] = [marker_to_flow(entry) for entry in editions]
    manifest["hash"] = manifest_hash(manifest)
    manifest_name = (
        f"{text_id}-{edition}.manifest.yaml" if edition else f"{text_id}.manifest.yaml"
    )
    (root / manifest_name).write_text(dump(manifest), encoding="utf-8")


def _write_bundle(root: Path) -> Path:
    bundle = root / TEXT_ID
    bundle.mkdir()
    master_juan, master_hash = _write_juan(bundle, TEXT_ID)

    asset = {
        "canonical_identifier": f"bkk:krp/{TEXT_ID}/bkk/v1/markers/1",
        "seq": 1,
        "markers": {
            "body": [
                marker_to_flow({
                    "type": "variant",
                    "offset": 2,
                    "length": 1,
                    "content": "丙",
                    "A": "參",
                }),
            ],
        },
        "hash": ZERO_HASH,
    }
    asset["hash"] = marker_asset_hash(asset)
    asset_name = f"assets/{TEXT_ID}_001.markers.yaml"
    (bundle / "assets").mkdir()
    (bundle / asset_name).write_text(dump(asset), encoding="utf-8")

    _write_manifest(
        bundle,
        TEXT_ID,
        master_juan,
        master_hash,
        marker_name=asset_name,
        marker_hash=asset["hash"],
        editions=[
            {"short": "A", "label": "Edition A"},
            {"short": "B", "label": "Edition B"},
        ],
    )

    for edition in ("A", "B"):
        edition_root = bundle / "editions" / edition
        edition_root.mkdir(parents=True)
        juan_name, juan_hash = _write_juan(edition_root, TEXT_ID, edition)
        _write_manifest(
            edition_root,
            TEXT_ID,
            juan_name,
            juan_hash,
            edition=edition,
        )
    return bundle


def test_remove_edition_deletes_dir_manifest_entry_and_variants(tmp_path: Path):
    bundle = _write_bundle(tmp_path)

    summary = remove_edition(bundle, "A")

    assert summary["edition"] == "A"
    assert not (bundle / "editions" / "A").exists()
    assert (bundle / "editions" / "B").is_dir()

    manifest = yaml.safe_load((bundle / f"{TEXT_ID}.manifest.yaml").read_text("utf-8"))
    assert manifest["editions"] == [{"short": "B", "label": "Edition B"}]
    assert "markers" not in manifest["assets"]

    juan = yaml.safe_load((bundle / f"{TEXT_ID}_001.yaml").read_text("utf-8"))
    variants = [
        marker for marker in juan["body"]["markers"]
        if marker.get("type") == "variant"
    ]
    assert variants == [
        {
            "type": "variant",
            "offset": 1,
            "length": 1,
            "content": "乙",
            "B": "三",
        }
    ]
    assert not (bundle / "assets" / f"{TEXT_ID}_001.markers.yaml").exists()

    zeroed = dict(manifest)
    zeroed["hash"] = ZERO_HASH
    assert sha256_jcs(zeroed) == manifest["hash"]
    assert manifest["assets"]["parts"][0]["hash"] == juan["hash"]


def test_remove_edition_dry_run_writes_nothing(tmp_path: Path):
    bundle = _write_bundle(tmp_path)
    manifest_before = (bundle / f"{TEXT_ID}.manifest.yaml").read_text("utf-8")
    juan_before = (bundle / f"{TEXT_ID}_001.yaml").read_text("utf-8")

    summary = remove_edition(bundle, "A", dry_run=True)

    assert summary["dry_run"] is True
    assert summary["scopes"][0]["juans_changed"] == [1]
    assert (bundle / "editions" / "A").is_dir()
    assert (bundle / f"{TEXT_ID}.manifest.yaml").read_text("utf-8") == manifest_before
    assert (bundle / f"{TEXT_ID}_001.yaml").read_text("utf-8") == juan_before


def test_remove_edition_cli_resolves_text_id(tmp_path: Path, monkeypatch, capsys):
    bundle = _write_bundle(tmp_path)
    monkeypatch.setattr("bkk.config.load_rc", lambda: {"import": {"out": tmp_path}})

    rc = repair_run(["remove-edition", "--text-id", TEXT_ID, "A"])

    assert rc == 0
    assert not (bundle / "editions" / "A").exists()
    out = capsys.readouterr().out
    assert "removed edition A" in out
