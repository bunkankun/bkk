"""Bundle mutations for the duplications editor."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bkk.edit.sections import (
    EditError,
    _merge_spans,
    _rebase_markers,
    _rebase_offset,
    _rebase_range,
    _splice_text,
    delete_juan_bucket,
    delete_spans,
)
from bkk.importer.hashing import manifest_hash, sha256_jcs


# ---------- pure helpers ----------------------------------------------------


def test_merge_spans_normalizes_and_collapses():
    assert _merge_spans([]) == ()
    assert _merge_spans([(0, 5)]) == ((0, 5),)
    assert _merge_spans([(5, 10), (0, 5)]) == ((0, 10),)
    assert _merge_spans([(0, 5), (3, 8)]) == ((0, 8),)
    assert _merge_spans([(0, 5), (10, 15)]) == ((0, 5), (10, 15))
    # Empty/inverted ranges are dropped.
    assert _merge_spans([(0, 0), (5, 5), (10, 8)]) == ()


def test_splice_text():
    out, deleted = _splice_text("0123456789", ((2, 5),))
    assert out == "0156789" and deleted == 3
    out, deleted = _splice_text("0123456789", ((0, 2), (7, 10)))
    assert out == "23456" and deleted == 5
    out, deleted = _splice_text("abcdef", ())
    assert out == "abcdef" and deleted == 0


def test_rebase_offset_inside_span_returns_none():
    assert _rebase_offset(3, ((2, 5),)) is None
    # Boundary: hi is exclusive, so offset == hi survives and shifts.
    assert _rebase_offset(5, ((2, 5),)) == 2
    assert _rebase_offset(1, ((2, 5),)) == 1
    assert _rebase_offset(10, ((2, 5), (7, 9))) == 5


def test_rebase_range():
    # Range entirely before deletions: untouched.
    assert _rebase_range((0, 2), ((5, 10),)) == (0, 2)
    # Range entirely after deletions: shifted left.
    assert _rebase_range((12, 20), ((5, 10),)) == (7, 15)
    # Range fully inside a deletion: dropped.
    assert _rebase_range((6, 8), ((5, 10),)) is None
    # Range straddles the start of a deletion: end clips to deletion start.
    assert _rebase_range((3, 8), ((5, 10),)) == (3, 5)
    # Range straddles the end of a deletion: start snaps forward.
    assert _rebase_range((8, 14), ((5, 10),)) == (5, 9)
    # Range completely covers a deletion: shrinks by the deletion length.
    assert _rebase_range((0, 20), ((5, 10),)) == (0, 15)


def test_rebase_markers_drops_inside_and_shifts_outside():
    markers = [
        {"type": "x", "offset": 1, "id": "a"},
        {"type": "x", "offset": 7, "id": "b"},  # inside (5,10)
        {"type": "x", "offset": 12, "id": "c"},
    ]
    kept = _rebase_markers(markers, ((5, 10),))
    assert [m["id"] for m in kept] == ["a", "c"]
    assert kept[1]["offset"] == 7


# ---------- end-to-end fixtures --------------------------------------------


def _write_bundle(
    root: Path,
    text_id: str,
    *,
    front: str | None,
    body: str,
    back: str | None,
    body_markers: list[dict] | None = None,
    front_markers: list[dict] | None = None,
    toc: list[dict] | None = None,
    marker_asset: dict | None = None,
) -> Path:
    bundle_dir = root / text_id
    bundle_dir.mkdir(parents=True)
    juan: dict = {
        "canonical_identifier": f"bkk:test/{text_id}/v1/juan/1",
        "seq": 1,
    }
    if front is not None:
        b: dict = {"text": front, "hash": "sha256:0"}
        if front_markers:
            b["markers"] = front_markers
        juan["front"] = b
    juan["body"] = {"text": body, "hash": "sha256:0"}
    if body_markers:
        juan["body"]["markers"] = body_markers
    if back is not None:
        juan["back"] = {"text": back, "hash": "sha256:0"}
    juan["hash"] = "sha256:0"
    (bundle_dir / f"{text_id}_001.yaml").write_text(
        yaml.safe_dump(juan, allow_unicode=True), encoding="utf-8",
    )

    assets: dict = {
        "parts": [
            {"seq": 1, "filename": f"{text_id}_001.yaml", "hash": "sha256:0"},
        ],
    }
    if marker_asset is not None:
        assets_dir = bundle_dir / "assets"
        assets_dir.mkdir()
        (assets_dir / f"{text_id}_001.markers.yaml").write_text(
            yaml.safe_dump(marker_asset, allow_unicode=True), encoding="utf-8",
        )
        assets["markers"] = [{
            "seq": 1,
            "role": "markers",
            "filename": f"assets/{text_id}_001.markers.yaml",
            "hash": "sha256:0",
        }]

    manifest: dict = {
        "canonical_identifier": f"bkk:test/{text_id}/v1",
        "editions": [{"short": "X", "label": "x"}],
        "assets": assets,
        "table_of_contents": toc or [
            {
                "ref": {
                    "seq": 1,
                    "marker_id": f"{text_id}_001-body",
                    "span": ["body", 0, len(body)],
                },
                "label": "body section",
            },
        ],
        "metadata": {"title": text_id, "edition": {"short": "X"}},
        "hash": "sha256:0",
    }
    (bundle_dir / f"{text_id}.manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8",
    )
    return bundle_dir


def _load_manifest(bundle_dir: Path) -> dict:
    text_id = bundle_dir.name
    return yaml.safe_load(
        (bundle_dir / f"{text_id}.manifest.yaml").read_text(encoding="utf-8")
    )


def _load_juan(bundle_dir: Path) -> dict:
    text_id = bundle_dir.name
    return yaml.safe_load(
        (bundle_dir / f"{text_id}_001.yaml").read_text(encoding="utf-8")
    )


# ---------- delete_juan_bucket ---------------------------------------------


def test_delete_juan_bucket_drops_juan_when_only_body(tmp_path: Path):
    bundle = _write_bundle(tmp_path, "BKK0001", front=None, body="abcdef", back=None)
    result = delete_juan_bucket(bundle, "BKK0001", 1, "body")
    assert result["juan_removed"] is True
    assert not (bundle / "BKK0001_001.yaml").exists()
    manifest = _load_manifest(bundle)
    assert manifest["assets"]["parts"] == []
    assert manifest["table_of_contents"] == []
    # Manifest hash refreshed (no longer the placeholder).
    assert manifest["hash"] != "sha256:0"
    assert manifest["hash"] == manifest_hash(manifest)


def test_delete_juan_bucket_keeps_juan_when_front_remains(tmp_path: Path):
    bundle = _write_bundle(
        tmp_path, "BKK0002",
        front="序文", body="本文",
        back=None,
        toc=[
            {"ref": {"seq": 1, "marker_id": "x", "span": ["front", 0, 2]}, "label": "f"},
            {"ref": {"seq": 1, "marker_id": "y", "span": ["body", 0, 2]}, "label": "b"},
        ],
    )
    result = delete_juan_bucket(bundle, "BKK0002", 1, "body")
    assert result["juan_removed"] is False
    juan = _load_juan(bundle)
    assert "body" not in juan
    assert juan["front"]["text"] == "序文"
    # Juan hash recomputed.
    expected = sha256_jcs({**juan, "hash": "sha256:" + "0" * 64})
    assert juan["hash"] == expected
    manifest = _load_manifest(bundle)
    # Part entry's hash mirrors the juan hash.
    assert manifest["assets"]["parts"][0]["hash"] == juan["hash"]
    labels = [e["label"] for e in manifest["table_of_contents"]]
    assert labels == ["f"]
    assert manifest["hash"] == manifest_hash(manifest)


def test_delete_juan_bucket_rejects_missing_bucket(tmp_path: Path):
    bundle = _write_bundle(tmp_path, "BKK0003", front=None, body="abc", back=None)
    with pytest.raises(EditError):
        delete_juan_bucket(bundle, "BKK0003", 1, "front")


def test_delete_juan_bucket_rejects_unknown_bucket(tmp_path: Path):
    bundle = _write_bundle(tmp_path, "BKK0004", front=None, body="abc", back=None)
    with pytest.raises(EditError):
        delete_juan_bucket(bundle, "BKK0004", 1, "middle")


def test_delete_juan_bucket_unknown_seq(tmp_path: Path):
    bundle = _write_bundle(tmp_path, "BKK0005", front=None, body="abc", back=None)
    with pytest.raises(EditError):
        delete_juan_bucket(bundle, "BKK0005", 99, "body")


# ---------- delete_spans ----------------------------------------------------


def test_delete_spans_excises_text_and_rebases_markers_and_toc(tmp_path: Path):
    body = "0123456789ABCDEFGHIJ"  # 20 chars
    markers = [
        {"type": "x", "offset": 1, "id": "before"},
        {"type": "x", "offset": 7, "id": "inside"},
        {"type": "x", "offset": 12, "id": "between"},
        {"type": "x", "offset": 18, "id": "after"},
    ]
    toc = [
        {"ref": {"seq": 1, "marker_id": "t1", "span": ["body", 0, 5]}, "label": "head"},
        {"ref": {"seq": 1, "marker_id": "t2", "span": ["body", 5, 10]}, "label": "mid"},
        {"ref": {"seq": 1, "marker_id": "t3", "span": ["body", 10, 20]}, "label": "tail"},
    ]
    bundle = _write_bundle(
        tmp_path, "BKK0010",
        front=None, body=body, back=None,
        body_markers=markers, toc=toc,
    )

    result = delete_spans(bundle, "BKK0010", 1, "body", [(5, 10)])
    assert result["deleted_chars"] == 5
    assert result["new_bucket_length"] == 15

    juan = _load_juan(bundle)
    assert juan["body"]["text"] == "01234ABCDEFGHIJ"
    kept_markers = juan["body"]["markers"]
    assert [m["id"] for m in kept_markers] == ["before", "between", "after"]
    assert [m["offset"] for m in kept_markers] == [1, 7, 13]

    manifest = _load_manifest(bundle)
    labels_and_spans = [
        (e["label"], e["ref"]["span"]) for e in manifest["table_of_contents"]
    ]
    assert labels_and_spans == [
        ("head", ["body", 0, 5]),
        ("tail", ["body", 5, 15]),
    ]
    assert manifest["assets"]["parts"][0]["hash"] == juan["hash"]
    assert manifest["hash"] == manifest_hash(manifest)


def test_delete_spans_two_disjoint_ranges(tmp_path: Path):
    body = "abcdefghij"  # 10 chars
    bundle = _write_bundle(tmp_path, "BKK0011", front=None, body=body, back=None)
    result = delete_spans(bundle, "BKK0011", 1, "body", [(0, 2), (7, 10)])
    assert result["deleted_chars"] == 5
    juan = _load_juan(bundle)
    assert juan["body"]["text"] == "cdefg"


def test_delete_spans_rejects_empty_input(tmp_path: Path):
    bundle = _write_bundle(tmp_path, "BKK0012", front=None, body="abc", back=None)
    with pytest.raises(EditError):
        delete_spans(bundle, "BKK0012", 1, "body", [])


def test_delete_spans_rejects_full_bucket_deletion(tmp_path: Path):
    bundle = _write_bundle(tmp_path, "BKK0013", front=None, body="abc", back=None)
    with pytest.raises(EditError):
        delete_spans(bundle, "BKK0013", 1, "body", [(0, 3)])


def test_delete_spans_updates_external_marker_asset(tmp_path: Path):
    body = "0123456789"
    asset = {
        "canonical_identifier": "bkk:test/BKK0014/X/v1/markers/1",
        "seq": 1,
        "markers": {
            "front": [],
            "body": [
                {"type": "ann", "offset": 1, "id": "keep"},
                {"type": "ann", "offset": 5, "id": "drop"},
                {"type": "ann", "offset": 9, "id": "shift"},
            ],
            "back": [],
        },
        "hash": "sha256:0",
    }
    bundle = _write_bundle(
        tmp_path, "BKK0014",
        front=None, body=body, back=None,
        marker_asset=asset,
    )
    delete_spans(bundle, "BKK0014", 1, "body", [(4, 7)])
    reloaded = yaml.safe_load(
        (bundle / "assets" / "BKK0014_001.markers.yaml").read_text(encoding="utf-8")
    )
    kept = reloaded["markers"]["body"]
    assert [m["id"] for m in kept] == ["keep", "shift"]
    assert [m["offset"] for m in kept] == [1, 6]
    # Marker asset hash refreshed and mirrored in the manifest entry.
    manifest = _load_manifest(bundle)
    entry = manifest["assets"]["markers"][0]
    assert entry["hash"] == reloaded["hash"]


def test_delete_juan_bucket_clears_external_marker_bucket(tmp_path: Path):
    asset = {
        "canonical_identifier": "bkk:test/BKK0015/X/v1/markers/1",
        "seq": 1,
        "markers": {
            "front": [{"type": "ann", "offset": 0, "id": "f"}],
            "body": [{"type": "ann", "offset": 1, "id": "b"}],
            "back": [],
        },
        "hash": "sha256:0",
    }
    bundle = _write_bundle(
        tmp_path, "BKK0015",
        front="abc", body="defghi", back=None,
        toc=[
            {"ref": {"seq": 1, "marker_id": "t1", "span": ["front", 0, 3]}, "label": "f"},
            {"ref": {"seq": 1, "marker_id": "t2", "span": ["body", 0, 6]}, "label": "b"},
        ],
        marker_asset=asset,
    )
    delete_juan_bucket(bundle, "BKK0015", 1, "body")
    reloaded = yaml.safe_load(
        (bundle / "assets" / "BKK0015_001.markers.yaml").read_text(encoding="utf-8")
    )
    assert "body" not in reloaded["markers"]
    assert reloaded["markers"]["front"][0]["id"] == "f"
    juan = _load_juan(bundle)
    assert "body" not in juan
    manifest = _load_manifest(bundle)
    assert [e["label"] for e in manifest["table_of_contents"]] == ["f"]
