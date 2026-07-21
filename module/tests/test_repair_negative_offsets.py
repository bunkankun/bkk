from __future__ import annotations

from pathlib import Path

from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.marker_assets import marker_asset_hash
from bkk.repair.cli import run as repair_run
from bkk.repair.negative_offsets import (
    find_negative_offset_markers_in_bundle,
    read_negative_offset_report,
)


def _self_hash(data: dict) -> str:
    zeroed = dict(data)
    zeroed["hash"] = ZERO_HASH
    return sha256_jcs(zeroed)


def _write_bundle(
    root: Path,
    *,
    text_id: str = "KR0n0001",
    inline_offset: int = -1,
    asset_offset: int = -2,
) -> Path:
    bundle_dir = root / text_id
    bundle_dir.mkdir()
    juan_name = f"{text_id}_001.yaml"
    asset_name = f"assets/{text_id}_001.markers.yaml"

    juan = {
        "canonical_identifier": f"bkk:krp/{text_id}/bkk/v1/juan/1",
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
                    "type": "punctuation",
                    "offset": inline_offset,
                    "content": "、",
                    "id": f"{text_id}_T_001-p",
                }),
            ],
        },
        "metadata": {"title": "Negative Offsets", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    juan["hash"] = _self_hash(juan)
    (bundle_dir / juan_name).write_text(dump(juan), encoding="utf-8")

    asset = {
        "canonical_identifier": f"bkk:krp/{text_id}/bkk/v1/markers/1",
        "seq": 1,
        "markers": {
            "body": [
                marker_to_flow({
                    "type": "line-break",
                    "offset": asset_offset,
                    "id": f"{text_id}_T_001-lb",
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
            }
        ],
        "metadata": {"title": "Negative Offsets", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    manifest["hash"] = manifest_hash(manifest)
    (bundle_dir / f"{text_id}.manifest.yaml").write_text(
        dump(manifest), encoding="utf-8",
    )
    return bundle_dir


def test_negative_offset_report_finds_inline_and_asset_markers(tmp_path: Path):
    bundle_dir = _write_bundle(tmp_path)

    summary = find_negative_offset_markers_in_bundle(bundle_dir)

    assert summary["errors"] == []
    rows = summary["rows"]
    assert [row["offset"] for row in rows] == [-2, -1]
    assert [row["source"] for row in rows] == ["asset", "inline"]
    assert [row["path"] for row in rows] == [
        "assets/KR0n0001_001.markers.yaml",
        "KR0n0001_001.yaml",
    ]
    assert {row["problem"] for row in rows} == {"negative-offset"}


def test_negative_offsets_cli_writes_report_for_corpus_prefix(
    tmp_path: Path, capsys,
):
    _write_bundle(tmp_path, text_id="KR0n0001")
    _write_bundle(tmp_path, text_id="KR1n0001", inline_offset=1, asset_offset=3)
    report = tmp_path / "negative-offsets.jsonl"

    rc = repair_run([
        "negative-offsets",
        "--out",
        str(tmp_path),
        "--text-prefix",
        "KR0n",
        "--report",
        str(report),
    ])

    assert rc == 0
    out = capsys.readouterr().out
    assert "wrote 2 negative-offset marker(s)" in out
    assert "scanned 1 bundles" in out
    rows = read_negative_offset_report(report)
    assert len(rows) == 2
    assert {row["textid"] for row in rows} == {"KR0n0001"}


def test_negative_offsets_cli_rejects_prefix_with_single_bundle(
    tmp_path: Path, capsys,
):
    bundle_dir = _write_bundle(tmp_path)

    rc = repair_run([
        "negative-offsets",
        "--bundle",
        str(bundle_dir),
        "--text-prefix",
        "KR0n",
    ])

    assert rc == 2
    assert "provide either --text-prefix or a single bundle/text id" in (
        capsys.readouterr().err
    )
