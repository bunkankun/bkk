from __future__ import annotations

import copy
from pathlib import Path

import yaml

from bkk.chars.run import run_lemma_repeat_apply
from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.marker_assets import build_marker_asset


TEXT_ID = "TSTLR001"


def _self_hash(juan: dict) -> str:
    data = copy.deepcopy(juan)
    data["hash"] = ZERO_HASH
    return sha256_jcs(data)


def _write_lemma_repeat_bundle(bundle_dir: Path) -> None:
    bundle_dir.mkdir(parents=True)
    _write_lemma_repeat_scope(
        bundle_dir,
        text_id=TEXT_ID,
        short=None,
        text="北東書丨丨又丨",
        lemma="北東",
    )

    edition_dir = bundle_dir / "editions" / "ed"
    edition_dir.mkdir(parents=True)
    _write_lemma_repeat_scope(
        edition_dir,
        text_id=TEXT_ID,
        short="ed",
        text="南西書丨丨",
        lemma="南西",
    )


def _write_lemma_repeat_scope(
    root: Path,
    *,
    text_id: str,
    short: str | None,
    text: str,
    lemma: str,
) -> None:
    juan = {
        "canonical_identifier": f"bkk:krp/{text_id}/v1/juan/1",
        "seq": 1,
        "body": {
            "text": text,
            "hash": sha256_text(text),
        },
        "metadata": {"title": "Lemma Repeat", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    juan["hash"] = _self_hash(juan)
    suffix = f"-{short}" if short else ""
    juan_name = f"{text_id}_001{suffix}.yaml"
    (root / juan_name).write_text(dump(juan), encoding="utf-8")

    asset = build_marker_asset(
        text_id,
        1,
        short,
        {
            "body": [
                {
                    "type": "voice",
                    "offset": 2,
                    "length": len(text) - 2,
                    "name": "def",
                    "id": "dn1",
                    "source": "dictionary",
                    "lemma": lemma,
                    "lemma_offset": 0,
                    "lemma_length": 2,
                },
            ],
        },
    )
    marker_name = f"assets/{text_id}_001{suffix}.markers.yaml"
    (root / "assets").mkdir()
    (root / marker_name).write_text(dump(asset), encoding="utf-8")

    manifest = {
        "canonical_identifier": f"bkk:krp/{text_id}/v1",
        "assets": {
            "parts": [
                marker_to_flow({"seq": 1, "filename": juan_name, "hash": juan["hash"]}),
            ],
            "markers": [
                marker_to_flow({
                    "seq": 1,
                    "role": "markers",
                    "filename": marker_name,
                    "hash": asset["hash"],
                }),
            ],
        },
        "metadata": {"title": "Lemma Repeat", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    manifest["hash"] = manifest_hash(manifest)
    manifest_name = f"{text_id}{suffix}.manifest.yaml"
    (root / manifest_name).write_text(dump(manifest), encoding="utf-8")


def test_lemma_repeat_apply_rewrites_text_and_marker_asset(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    _write_lemma_repeat_bundle(bundle)

    assert run_lemma_repeat_apply(bundle_dir=bundle) == 0

    juan = yaml.safe_load((bundle / f"{TEXT_ID}_001.yaml").read_text(encoding="utf-8"))
    assert juan["body"]["text"] == "北東書北東又北"
    assert juan["body"]["hash"] == sha256_text("北東書北東又北")
    assert juan["hash"] == _self_hash(juan)

    manifest = yaml.safe_load((bundle / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["assets"]["parts"][0]["hash"] == juan["hash"]
    assert manifest["hash"] == manifest_hash(manifest)

    asset_entry = manifest["assets"]["markers"][0]
    asset = yaml.safe_load((bundle / asset_entry["filename"]).read_text(encoding="utf-8"))
    body_markers = asset["markers"]["body"]
    substitutions = [
        marker for marker in body_markers
        if marker.get("type") == "substitution:lemma-repeat"
    ]
    assert [marker["offset"] for marker in substitutions] == [3, 4, 6]
    assert [marker["replacement"] for marker in substitutions] == ["北", "東", "北"]
    assert all(marker["original"] == "丨" for marker in substitutions)
    assert asset_entry["hash"] == asset["hash"]

    edition = yaml.safe_load(
        (bundle / "editions" / "ed" / f"{TEXT_ID}_001-ed.yaml").read_text(
            encoding="utf-8",
        )
    )
    assert edition["body"]["text"] == "南西書南西"
    assert edition["body"]["hash"] == sha256_text("南西書南西")
    assert edition["hash"] == _self_hash(edition)

    edition_manifest = yaml.safe_load(
        (bundle / "editions" / "ed" / f"{TEXT_ID}-ed.manifest.yaml").read_text(
            encoding="utf-8",
        )
    )
    assert edition_manifest["assets"]["parts"][0]["hash"] == edition["hash"]
    assert edition_manifest["hash"] == manifest_hash(edition_manifest)

    edition_asset_entry = edition_manifest["assets"]["markers"][0]
    edition_asset = yaml.safe_load(
        (
            bundle / "editions" / "ed" / edition_asset_entry["filename"]
        ).read_text(encoding="utf-8")
    )
    edition_substitutions = [
        marker for marker in edition_asset["markers"]["body"]
        if marker.get("type") == "substitution:lemma-repeat"
    ]
    assert [marker["offset"] for marker in edition_substitutions] == [3, 4]
    assert [marker["replacement"] for marker in edition_substitutions] == ["南", "西"]
    assert edition_asset_entry["hash"] == edition_asset["hash"]
