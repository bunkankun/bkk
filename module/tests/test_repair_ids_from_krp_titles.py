"""Tests for ``bkk repair ids-from-krp-titles``."""

from __future__ import annotations

from pathlib import Path

import yaml

from bkk.importer.hashing import ZERO_HASH, manifest_hash
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.repair.cli import run as repair_run
from bkk.repair.identifiers import apply_alt_ids, purge_non_alt_ids
from bkk.repair.krp_titles import parse_alt_ids


CATALOG_SAMPLE = """\
KR5 道部
KR5a 洞真部
KR5a0001 @DZ0001 @JY001 @ZB5a0001 靈寶無量度人上品妙經--
KR5a0003 @DZ0003 @JY005 @ZB5a0003 元始說先天道德經註解--李嘉謀
KR6a0001 @T01n0001 @ZB6a0001 長阿含經-後秦-佛陀耶舍
KR1a0002 @SK1a0003 @ZB1a0001 子夏易傳-周-卜商
KR1b0049 @TODO @SK1b0215 @ZB1b0044 古文尚書寃詞-清-毛奇齡
KR1c0005 @CH1c0877 @TODO @H15-24-0085 some-title--
"""


def test_parse_alt_ids_filters_correctly(tmp_path: Path) -> None:
    p = tmp_path / "krp-titles.txt"
    p.write_text(CATALOG_SAMPLE, encoding="utf-8")

    out = parse_alt_ids(p)

    # Section headers skipped.
    assert "KR5" not in out and "KR5a" not in out
    # Normal lines.
    assert out["KR5a0001"] == ["DZ0001", "JY001"]
    assert out["KR5a0003"] == ["DZ0003", "JY005"]
    assert out["KR6a0001"] == ["T01n0001"]
    # All-filtered lines (only SK/ZB) drop out of the map entirely.
    assert "KR1a0002" not in out
    assert "KR1b0049" not in out
    # @TODO interleaved with a kept id: keep the rest.
    assert out["KR1c0005"] == ["CH1c0877", "H15-24-0085"]


def _write_minimal_manifest(bundle_dir: Path, text_id: str) -> Path:
    """Write a manifest with the BKK shape (flow-style parts) so we can
    confirm the patcher preserves that style after round-trip."""
    bundle_dir.mkdir(parents=True)
    manifest = {
        "canonical_identifier": f"bkk:krp/{text_id}/v1",
        "canonical_location": f"https://kanripo.org/bkk/{text_id}/v1",
        "canonical_set": {
            "identifier": "bkk:charset/cjk-v1",
            "hash": ZERO_HASH,
        },
        "assets": {
            "parts": [
                marker_to_flow({
                    "seq": 0,
                    "filename": f"{text_id}_000.yaml",
                    "hash": "sha256:" + "0" * 64,
                }),
            ],
        },
        "metadata": {
            "title": "Test title",
            "source": {"repository": "kanripo", "path": text_id},
        },
        "hash": ZERO_HASH,
    }
    manifest["hash"] = manifest_hash(manifest)
    path = bundle_dir / f"{text_id}.manifest.yaml"
    path.write_text(dump(manifest), encoding="utf-8")
    return path


def test_apply_alt_ids_inserts_under_metadata(tmp_path: Path) -> None:
    text_id = "KR5x0001"
    bundle_dir = tmp_path / text_id
    manifest_path = _write_minimal_manifest(bundle_dir, text_id)

    result = apply_alt_ids(bundle_dir, ["DZ0001", "JY001"])
    assert result["changed"] is True
    assert result["before"] == []
    assert result["after"] == ["DZ0001", "JY001"]

    written = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert written["metadata"]["identifiers"]["alt_id"] == ["DZ0001", "JY001"]
    # identifiers placed right after title in the metadata bag.
    md_keys = list(written["metadata"].keys())
    assert md_keys.index("identifiers") == md_keys.index("title") + 1
    # Hash recomputes to match content.
    assert written["hash"] == manifest_hash(written)
    # Flow-style parts preserved.
    assert "{seq: 0, filename:" in manifest_path.read_text(encoding="utf-8")


def test_apply_alt_ids_overwrites_and_is_idempotent(tmp_path: Path) -> None:
    text_id = "KR5x0002"
    bundle_dir = tmp_path / text_id
    _write_minimal_manifest(bundle_dir, text_id)

    apply_alt_ids(bundle_dir, ["OLD1"])
    second = apply_alt_ids(bundle_dir, ["NEW1", "NEW2"])
    assert second["before"] == ["OLD1"]
    assert second["after"] == ["NEW1", "NEW2"]
    assert second["changed"] is True

    third = apply_alt_ids(bundle_dir, ["NEW1", "NEW2"])
    assert third["changed"] is False


def test_cli_dry_run_does_not_write(tmp_path: Path) -> None:
    text_id = "KR5x0003"
    bundle_dir = tmp_path / text_id
    manifest_path = _write_minimal_manifest(bundle_dir, text_id)
    before = manifest_path.read_text(encoding="utf-8")

    catalog = tmp_path / "krp-titles.txt"
    catalog.write_text(f"{text_id} @DZ9999 @ZB9999 title--\n", encoding="utf-8")

    rc = repair_run([
        "ids-from-krp-titles",
        "--section", "KR5x",
        "--titles", str(catalog),
        "--out", str(tmp_path),
        "--dry-run",
    ])
    assert rc == 0
    assert manifest_path.read_text(encoding="utf-8") == before


def test_cli_writes_alt_ids(tmp_path: Path) -> None:
    text_id = "KR6x0001"
    bundle_dir = tmp_path / text_id
    manifest_path = _write_minimal_manifest(bundle_dir, text_id)

    catalog = tmp_path / "krp-titles.txt"
    catalog.write_text(
        f"{text_id} @T99n0001 @ZB6x0001 title--\n", encoding="utf-8",
    )

    rc = repair_run([
        "ids-from-krp-titles",
        "--section", "KR6",
        "--titles", str(catalog),
        "--out", str(tmp_path),
    ])
    assert rc == 0
    written = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert written["metadata"]["identifiers"]["alt_id"] == ["T99n0001"]


def test_cli_finds_bundles_in_by_section_layout(tmp_path: Path) -> None:
    """The corpus is often arranged as ``<root>/<section>/<text_id>/`` by
    the importer's ``--by-section`` flag. Repair must descend into the
    section directory rather than only scanning the root's children."""
    # By-section bundle.
    section_dir = tmp_path / "KR5c"
    nested = section_dir / "KR5c0095"
    _write_minimal_manifest(nested, "KR5c0095")
    # Flat-layout bundle in the same corpus root, to confirm both layouts
    # coexist.
    flat = tmp_path / "KR6d0001"
    _write_minimal_manifest(flat, "KR6d0001")

    catalog = tmp_path / "krp-titles.txt"
    catalog.write_text(
        "KR5c0095 @DZ0707 @JY049 title--\n"
        "KR6d0001 @T09n0262 other--\n",
        encoding="utf-8",
    )

    rc = repair_run([
        "ids-from-krp-titles",
        "--section", "KR5",
        "--section", "KR6",
        "--titles", str(catalog),
        "--out", str(tmp_path),
    ])
    assert rc == 0

    nested_manifest = yaml.safe_load(
        (nested / "KR5c0095.manifest.yaml").read_text(encoding="utf-8")
    )
    assert nested_manifest["metadata"]["identifiers"]["alt_id"] == ["DZ0707", "JY049"]

    flat_manifest = yaml.safe_load(
        (flat / "KR6d0001.manifest.yaml").read_text(encoding="utf-8")
    )
    assert flat_manifest["metadata"]["identifiers"]["alt_id"] == ["T09n0262"]


def test_cli_skips_bundles_not_in_catalog(tmp_path: Path) -> None:
    text_id = "KR5x0009"
    bundle_dir = tmp_path / text_id
    manifest_path = _write_minimal_manifest(bundle_dir, text_id)
    before = manifest_path.read_text(encoding="utf-8")

    catalog = tmp_path / "krp-titles.txt"
    catalog.write_text("KR5x0008 @DZ0001 other--\n", encoding="utf-8")

    rc = repair_run([
        "ids-from-krp-titles",
        "--section", "KR5",
        "--titles", str(catalog),
        "--out", str(tmp_path),
    ])
    assert rc == 0
    assert manifest_path.read_text(encoding="utf-8") == before


# ---------- remove-ids ------------------------------------------------------


def _write_manifest_with_identifiers(
    bundle_dir: Path, text_id: str, identifiers: dict,
) -> Path:
    bundle_dir.mkdir(parents=True)
    manifest = {
        "canonical_identifier": f"bkk:krp/{text_id}/v1",
        "canonical_location": f"https://kanripo.org/bkk/{text_id}/v1",
        "canonical_set": {
            "identifier": "bkk:charset/cjk-v1",
            "hash": ZERO_HASH,
        },
        "assets": {
            "parts": [
                marker_to_flow({
                    "seq": 0,
                    "filename": f"{text_id}_000.yaml",
                    "hash": "sha256:" + "0" * 64,
                }),
            ],
        },
        "metadata": {
            "title": "Test title",
            "identifiers": dict(identifiers),
            "source": {"repository": "kanripo", "path": text_id},
        },
        "hash": ZERO_HASH,
    }
    manifest["hash"] = manifest_hash(manifest)
    path = bundle_dir / f"{text_id}.manifest.yaml"
    path.write_text(dump(manifest), encoding="utf-8")
    return path


def test_purge_keeps_alt_id_and_drops_the_rest(tmp_path: Path) -> None:
    text_id = "KR6y0001"
    bundle_dir = tmp_path / text_id
    path = _write_manifest_with_identifiers(
        bundle_dir, text_id,
        {
            "krp": text_id,
            "cbeta": "T01n0001",
            "cbeta_old_id": "T01n0001",
            "alt_id": ["T01n0001"],
        },
    )

    result = purge_non_alt_ids(bundle_dir)
    assert result["changed"] is True
    assert sorted(result["removed"]) == ["cbeta", "cbeta_old_id", "krp"]

    written = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert written["metadata"]["identifiers"] == {"alt_id": ["T01n0001"]}
    assert written["hash"] == manifest_hash(written)


def test_purge_drops_section_when_only_other_keys_present(tmp_path: Path) -> None:
    text_id = "KR6y0002"
    bundle_dir = tmp_path / text_id
    path = _write_manifest_with_identifiers(
        bundle_dir, text_id, {"krp": text_id, "cbeta": "T01n0002"},
    )

    result = purge_non_alt_ids(bundle_dir)
    assert result["changed"] is True

    written = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "identifiers" not in written["metadata"]
    assert written["hash"] == manifest_hash(written)


def test_purge_is_noop_when_only_alt_id_present(tmp_path: Path) -> None:
    text_id = "KR6y0003"
    bundle_dir = tmp_path / text_id
    path = _write_manifest_with_identifiers(
        bundle_dir, text_id, {"alt_id": ["T01n0003"]},
    )
    before = path.read_text(encoding="utf-8")

    result = purge_non_alt_ids(bundle_dir)
    assert result["changed"] is False
    assert path.read_text(encoding="utf-8") == before


def test_remove_ids_cli_dry_run_does_not_write(tmp_path: Path) -> None:
    text_id = "KR6y0004"
    bundle_dir = tmp_path / text_id
    path = _write_manifest_with_identifiers(
        bundle_dir, text_id, {"krp": text_id, "alt_id": ["X1"]},
    )
    before = path.read_text(encoding="utf-8")

    rc = repair_run([
        "remove-ids", "--section", "KR6",
        "--out", str(tmp_path), "--dry-run",
    ])
    assert rc == 0
    assert path.read_text(encoding="utf-8") == before


def test_remove_ids_cli_writes_through_by_section_layout(tmp_path: Path) -> None:
    text_id = "KR6y0005"
    nested = tmp_path / "KR6y" / text_id
    path = _write_manifest_with_identifiers(
        nested, text_id,
        {"krp": text_id, "cbeta": "T01n0005", "alt_id": ["T01n0005"]},
    )

    rc = repair_run([
        "remove-ids", "--section", "KR6",
        "--out", str(tmp_path),
    ])
    assert rc == 0
    written = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert written["metadata"]["identifiers"] == {"alt_id": ["T01n0005"]}
