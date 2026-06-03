"""Marker-ID snapshot + drift check.

Imports KR6q0053 once, freezes the baseline, then mutates a marker id and
confirms the drift report flags it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bkk.importer.cli import _find_tls_texts
from bkk.importer.read.tls import read_tls
from bkk.importer.write.bundle import write_bundle
from bkk.validator.marker_ids import (
    SNAPSHOT_SUFFIX,
    freeze_marker_ids,
    validate_marker_ids,
)


REPO = Path(__file__).resolve().parents[1]
TEXT_ID = "KR6q0053"


@pytest.fixture(scope="module")
def frozen_bundle(tmp_path_factory) -> Path:
    in_root = REPO / "input" / "tls"
    matches = _find_tls_texts(in_root, TEXT_ID)
    assert matches
    bundle = read_tls(
        matches[0],
        in_root / "tls-data" / "notes" / "swl" / f"{TEXT_ID}-ann.xml",
        in_root / "tls-data" / "notes" / "doc" / f"{TEXT_ID}-ann.xml",
        TEXT_ID,
    )
    out = tmp_path_factory.mktemp("validate-mid")
    write_bundle(bundle, out)
    bundle_dir = out / TEXT_ID
    freeze_marker_ids(bundle_dir)
    return bundle_dir


def test_snapshot_file_written(frozen_bundle: Path):
    snap = frozen_bundle / f"{TEXT_ID}{SNAPSHOT_SUFFIX}"
    assert snap.exists()
    data = yaml.safe_load(snap.read_text(encoding="utf-8"))
    assert data["text_id"] == TEXT_ID
    assert data["master"]
    first_juan = data["master"][0]
    assert first_juan["seq"] == 1
    assert all("id" in row and "type" in row for row in first_juan["ids"])


def test_clean_baseline_has_no_drift(frozen_bundle: Path):
    issues = validate_marker_ids(frozen_bundle)
    assert [i for i in issues if i.kind in ("missing", "repurposed")] == []


def test_freeze_refuses_to_overwrite(frozen_bundle: Path):
    with pytest.raises(FileExistsError):
        freeze_marker_ids(frozen_bundle)


def test_renamed_id_flagged_as_missing(frozen_bundle: Path):
    snap_path = frozen_bundle / f"{TEXT_ID}{SNAPSHOT_SUFFIX}"
    snap = yaml.safe_load(snap_path.read_text(encoding="utf-8"))
    try:
        original_id = snap["master"][0]["ids"][0]["id"]
        snap["master"][0]["ids"][0]["id"] = original_id + "_renamed"
        snap_path.write_text(
            yaml.safe_dump(snap, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        issues = validate_marker_ids(frozen_bundle)
        kinds = {(i.kind, i.id) for i in issues}
        assert ("missing", original_id + "_renamed") in kinds
    finally:
        # Restore so other tests using the fixture remain clean.
        freeze_marker_ids(frozen_bundle, force=True)


def test_repurposed_type_flagged(frozen_bundle: Path):
    snap_path = frozen_bundle / f"{TEXT_ID}{SNAPSHOT_SUFFIX}"
    snap = yaml.safe_load(snap_path.read_text(encoding="utf-8"))
    try:
        snap["master"][0]["ids"][0]["type"] = "wrong:type"
        snap_path.write_text(
            yaml.safe_dump(snap, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        issues = validate_marker_ids(frozen_bundle)
        assert any(i.kind == "repurposed" for i in issues)
    finally:
        freeze_marker_ids(frozen_bundle, force=True)
