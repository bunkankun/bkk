"""Regression tests for ``bkk voice add`` marker-asset writes."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.marker_assets import build_marker_asset
from bkk.voice.cli import _process_one, _run_add, _selected_add_args
from bkk.voice.problems import (
    read_voice_problems_report,
    write_voice_problems_report,
)


TEXT_ID = "TSTV001"


def _self_hash(juan: dict) -> str:
    data = copy.deepcopy(juan)
    data["hash"] = ZERO_HASH
    return sha256_jcs(data)


def _write_bundle_with_marker_asset(bundle_dir: Path) -> tuple[Path, str]:
    return _write_bundle_with_marker_asset_for(bundle_dir, TEXT_ID)


def _write_tls_note_bundle_with_marker_asset(bundle_dir: Path) -> tuple[Path, str]:
    return _write_bundle_with_marker_asset_for(
        bundle_dir,
        TEXT_ID,
        markers=[
            {"type": "tls:note-start", "offset": 2, "content": "(", "id": "n-start"},
            {"type": "tls:note-end", "offset": 8, "content": ")", "id": "n-end"},
        ],
    )


def _write_bundle_with_marker_asset_for(
    bundle_dir: Path,
    text_id: str,
    markers: list[dict] | None = None,
    body_text: str = "abcdefghij",
) -> tuple[Path, str]:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    juan = {
        "canonical_identifier": f"bkk:krp/{text_id}/v1/juan/1",
        "seq": 1,
        "body": {
            "text": body_text,
            "hash": "sha256:" + "0" * 64,
        },
        "hash": ZERO_HASH,
    }
    juan["hash"] = _self_hash(juan)
    juan_name = f"{text_id}_001.yaml"
    (bundle_dir / juan_name).write_text(dump(juan), encoding="utf-8")

    marker_asset = build_marker_asset(
        text_id,
        1,
        None,
        {
            "body": markers if markers is not None else [
                {"type": "punctuation", "offset": 2, "content": "(", "id": ""},
                {"type": "punctuation", "offset": 8, "content": ")", "id": ""},
            ],
        },
    )
    marker_name = f"assets/{text_id}_001.markers.yaml"
    (bundle_dir / "assets").mkdir()
    (bundle_dir / marker_name).write_text(dump(marker_asset), encoding="utf-8")

    manifest = {
        "canonical_identifier": f"bkk:krp/{text_id}/v1",
        "assets": {
            "parts": [
                marker_to_flow({
                    "seq": 1,
                    "filename": juan_name,
                    "hash": juan["hash"],
                }),
            ],
            "markers": [
                marker_to_flow({
                    "seq": 1,
                    "role": "markers",
                    "filename": marker_name,
                    "hash": marker_asset["hash"],
                }),
            ],
        },
        "metadata": {
            "title": "Test",
            "identifiers": {"krp": text_id},
            "edition": {"short": "bkk"},
        },
        "hash": ZERO_HASH,
    }
    manifest["hash"] = manifest_hash(manifest)
    manifest_path = bundle_dir / f"{text_id}.manifest.yaml"
    manifest_path.write_text(dump(manifest), encoding="utf-8")
    return manifest_path, juan["hash"]


def test_add_writes_voices_to_existing_marker_asset(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    manifest_path, original_juan_hash = _write_bundle_with_marker_asset(bundle)

    stats = _process_one(
        bundle,
        manifest_path,
        TEXT_ID,
        short=None,
        source="parens",
        force=False,
        dry_run=False,
    )

    assert stats["by_name"] == {"note": 1}
    juan = yaml.safe_load((bundle / f"{TEXT_ID}_001.yaml").read_text(encoding="utf-8"))
    assert juan["hash"] == original_juan_hash
    assert "markers" not in juan["body"]

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    asset_entry = manifest["assets"]["markers"][0]
    asset = yaml.safe_load((bundle / asset_entry["filename"]).read_text(encoding="utf-8"))
    body_markers = asset["markers"]["body"]
    assert any(m.get("type") == "punctuation" for m in body_markers)
    assert {
        "type": "voice",
        "offset": 2,
        "length": 6,
        "name": "note",
        "id": "n1",
    } in body_markers
    assert manifest["assets"]["parts"][0]["hash"] == original_juan_hash
    assert asset_entry["hash"] == asset["hash"]
    assert manifest["hash"] == manifest_hash(manifest)


def test_add_keeps_valid_voices_when_same_juan_has_problem(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    manifest_path, _original_juan_hash = _write_bundle_with_marker_asset_for(
        bundle,
        TEXT_ID,
        markers=[
            {"type": "punctuation", "offset": 1, "content": "(", "id": ""},
            {"type": "punctuation", "offset": 3, "content": ")", "id": ""},
            {"type": "punctuation", "offset": 5, "content": "(", "id": ""},
        ],
    )

    stats = _process_one(
        bundle,
        manifest_path,
        TEXT_ID,
        short=None,
        source="parens",
        force=False,
        dry_run=False,
    )

    assert stats["by_name"] == {"note": 1}
    assert stats["problems"] == 1
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    markers = _asset_markers(bundle, manifest, 1)
    assert {
        "type": "voice",
        "offset": 1,
        "length": 2,
        "name": "note",
        "id": "n1",
    } in markers
    problem = next(
        marker for marker in markers if marker.get("type") == "voice:problem"
    )
    assert problem["offset"] == 5
    assert problem["source"] == "parens"
    assert problem["code"] == "unmatched-open"
    assert problem["id"].startswith(f"{TEXT_ID}_bkk_001-bkkvprob")


def test_add_includes_tls_note_markers_by_default(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    manifest_path, _original_juan_hash = _write_tls_note_bundle_with_marker_asset(bundle)

    stats = _process_one(
        bundle,
        manifest_path,
        TEXT_ID,
        short=None,
        source="parens",
        force=False,
        dry_run=False,
    )

    assert stats["by_name"] == {"note": 1}
    manifest = yaml.safe_load(
        (bundle / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8")
    )
    markers = _asset_markers(bundle, manifest, 1)
    assert {
        "type": "voice",
        "offset": 2,
        "length": 6,
        "name": "note",
        "id": "n1",
    } in markers


def test_add_can_exclude_tls_note_markers(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    manifest_path, _original_juan_hash = _write_tls_note_bundle_with_marker_asset(bundle)

    stats = _process_one(
        bundle,
        manifest_path,
        TEXT_ID,
        short=None,
        source="parens",
        force=False,
        dry_run=False,
        include_tls_notes=False,
    )

    assert stats["by_name"] == {}
    manifest = yaml.safe_load(
        (bundle / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8")
    )
    markers = _asset_markers(bundle, manifest, 1)
    assert not any(marker.get("type") == "voice" for marker in markers)


def test_add_parens_also_derives_punctuation_title_voice(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    manifest_path, _original_juan_hash = _write_bundle_with_marker_asset_for(
        bundle,
        TEXT_ID,
        markers=[
            {"type": "punctuation", "offset": 1, "content": "(", "id": ""},
            {"type": "punctuation", "offset": 3, "content": ")", "id": ""},
            {"type": "punctuation", "offset": 5, "content": "《", "id": ""},
            {"type": "punctuation", "offset": 8, "content": "》", "id": ""},
        ],
    )

    stats = _process_one(
        bundle,
        manifest_path,
        TEXT_ID,
        short=None,
        source="parens",
        force=False,
        dry_run=False,
    )

    assert stats["by_name"] == {"note": 1, "title": 1}
    manifest = yaml.safe_load(
        (bundle / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8")
    )
    markers = _asset_markers(bundle, manifest, 1)
    assert {
        "type": "voice",
        "offset": 1,
        "length": 2,
        "name": "note",
        "id": "n1",
    } in markers
    assert {
        "type": "voice",
        "offset": 5,
        "length": 3,
        "name": "title",
        "id": "t1",
        "source": "punctuation",
    } in markers


def test_add_punctuation_derives_title_voice(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    manifest_path, _original_juan_hash = _write_bundle_with_marker_asset_for(
        bundle,
        TEXT_ID,
        markers=[
            {"type": "punctuation", "offset": 2, "content": "《", "id": ""},
            {"type": "punctuation", "offset": 8, "content": "》", "id": ""},
        ],
    )

    stats = _process_one(
        bundle,
        manifest_path,
        TEXT_ID,
        short=None,
        source="punctuation",
        force=False,
        dry_run=False,
    )

    assert stats["by_name"] == {"title": 1}
    manifest = yaml.safe_load(
        (bundle / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8")
    )
    markers = _asset_markers(bundle, manifest, 1)
    assert {
        "type": "voice",
        "offset": 2,
        "length": 6,
        "name": "title",
        "id": "t1",
        "source": "punctuation",
    } in markers


def test_add_punctuation_preserves_existing_non_punctuation_voice(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / TEXT_ID
    manifest_path, _original_juan_hash = _write_bundle_with_marker_asset_for(
        bundle,
        TEXT_ID,
        markers=[
            {"type": "voice", "offset": 0, "length": 2, "name": "note", "id": "n1"},
            {"type": "punctuation", "offset": 2, "content": "《", "id": ""},
            {"type": "punctuation", "offset": 8, "content": "》", "id": ""},
        ],
    )

    stats = _process_one(
        bundle,
        manifest_path,
        TEXT_ID,
        short=None,
        source="punctuation",
        force=False,
        dry_run=False,
    )

    assert stats["by_name"] == {"title": 1}
    manifest = yaml.safe_load(
        (bundle / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8")
    )
    markers = _asset_markers(bundle, manifest, 1)
    assert {
        "type": "voice",
        "offset": 0,
        "length": 2,
        "name": "note",
        "id": "n1",
    } in markers
    assert {
        "type": "voice",
        "offset": 2,
        "length": 6,
        "name": "title",
        "id": "t1",
        "source": "punctuation",
    } in markers


def test_add_tls_seg_derives_root_and_commentary(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    manifest_path, _original_juan_hash = _write_bundle_with_marker_asset_for(
        bundle,
        TEXT_ID,
        markers=[
            {
                "type": "tls:seg-start",
                "offset": 0,
                "content": "",
                "id": "s1",
                "seg_type": "root",
            },
            {"type": "tls:seg", "offset": 0, "content": "", "id": "s1"},
            {
                "type": "tls:seg-end",
                "offset": 4,
                "content": "",
                "id": "s1_end",
                "seg_type": "root",
            },
            {
                "type": "tls:seg-start",
                "offset": 4,
                "content": "",
                "id": "s2",
                "seg_type": "comm",
            },
            {"type": "tls:seg", "offset": 4, "content": "", "id": "s2"},
            {
                "type": "tls:seg-end",
                "offset": 10,
                "content": "",
                "id": "s2_end",
                "seg_type": "comm",
            },
        ],
    )

    stats = _process_one(
        bundle,
        manifest_path,
        TEXT_ID,
        short=None,
        source="tls-seg",
        force=False,
        dry_run=False,
    )

    assert stats["by_name"] == {"root": 1, "commentary": 1}
    manifest = yaml.safe_load(
        (bundle / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8")
    )
    markers = _asset_markers(bundle, manifest, 1)
    assert {
        "type": "voice",
        "offset": 0,
        "length": 4,
        "name": "root",
        "id": "r1",
    } in markers
    assert {
        "type": "voice",
        "offset": 4,
        "length": 6,
        "name": "commentary",
        "id": "c1",
        "responds-to": "r1",
    } in markers


def test_add_indent_headings_preserves_existing_note_voice(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    manifest_path, _original_juan_hash = _write_bundle_with_marker_asset_for(
        bundle,
        TEXT_ID,
        markers=[
            {"type": "line-break", "offset": 0, "content": "", "id": "l1"},
            {"type": "indent", "offset": 0, "content": "\u3000", "id": "i1"},
            {"type": "line-break", "offset": 2, "content": "", "id": "l2"},
            {"type": "indent", "offset": 2, "content": "\u3000\u3000", "id": "i2"},
            {"type": "line-break", "offset": 5, "content": "", "id": "l3"},
            {"type": "voice", "offset": 5, "length": 2, "name": "note", "id": "n1"},
        ],
        body_text="傅子正心篇本文",
    )

    stats = _process_one(
        bundle,
        manifest_path,
        TEXT_ID,
        short=None,
        source="indent-headings",
        force=False,
        dry_run=False,
    )

    assert stats["by_name"] == {"head": 2}
    manifest = yaml.safe_load(
        (bundle / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8")
    )
    markers = _asset_markers(bundle, manifest, 1)
    assert {"type": "voice", "offset": 5, "length": 2, "name": "note", "id": "n1"} in markers
    assert [
        marker for marker in markers
        if marker.get("type") == "voice" and marker.get("name") == "head"
    ] == [
        {
            "type": "voice",
            "offset": 0,
            "length": 2,
            "name": "head",
            "id": "h1",
            "source": "indent-headings",
            "indent_depth": 1,
        },
        {
            "type": "voice",
            "offset": 2,
            "length": 3,
            "name": "head",
            "id": "h2",
            "source": "indent-headings",
            "indent_depth": 2,
        },
    ]


def test_add_indent_headings_force_replaces_only_heading_source(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / TEXT_ID
    manifest_path, _original_juan_hash = _write_bundle_with_marker_asset_for(
        bundle,
        TEXT_ID,
        markers=[
            {"type": "line-break", "offset": 0, "content": "", "id": "l1"},
            {"type": "indent", "offset": 0, "content": "\u3000", "id": "i1"},
            {"type": "line-break", "offset": 2, "content": "", "id": "l2"},
            {"type": "indent", "offset": 2, "content": "\u3000\u3000", "id": "i2"},
            {"type": "voice", "offset": 2, "length": 3, "name": "note", "id": "n1"},
        ],
        body_text="傅子正心篇",
    )
    assert _process_one(
        bundle,
        manifest_path,
        TEXT_ID,
        short=None,
        source="indent-headings",
        force=False,
        dry_run=False,
    )["by_name"] == {"head": 2}

    stats = _process_one(
        bundle,
        manifest_path,
        TEXT_ID,
        short=None,
        source="indent-headings",
        force=True,
        dry_run=False,
    )

    assert stats["by_name"] == {"head": 2}
    manifest = yaml.safe_load(
        (bundle / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8")
    )
    markers = _asset_markers(bundle, manifest, 1)
    assert [
        marker for marker in markers
        if marker.get("type") == "voice" and marker.get("name") == "note"
    ] == [
        {"type": "voice", "offset": 2, "length": 3, "name": "note", "id": "n1"},
    ]
    assert len([
        marker for marker in markers
        if marker.get("type") == "voice" and marker.get("name") == "head"
    ]) == 2


def test_add_auto_chooses_indent_headings_profile(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    manifest_path, _original_juan_hash = _write_bundle_with_marker_asset_for(
        bundle,
        TEXT_ID,
        markers=[
            {"type": "line-break", "offset": 0, "content": "", "id": "l1"},
            {"type": "indent", "offset": 0, "content": "\u3000", "id": "i1"},
            {"type": "line-break", "offset": 2, "content": "", "id": "l2"},
            {"type": "indent", "offset": 2, "content": "\u3000\u3000", "id": "i2"},
            {"type": "line-break", "offset": 5, "content": "", "id": "l3"},
            {"type": "indent", "offset": 5, "content": "\u3000\u3000", "id": "i3"},
        ],
        body_text="傅子正心篇仁論篇",
    )

    stats = _process_one(
        bundle,
        manifest_path,
        TEXT_ID,
        short=None,
        source="auto",
        force=False,
        dry_run=False,
    )

    assert stats["by_name"] == {"head": 3}
    manifest = yaml.safe_load(
        (bundle / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8")
    )
    markers = _asset_markers(bundle, manifest, 1)
    assert all(
        marker.get("source") == "indent-headings"
        for marker in markers
        if marker.get("type") == "voice" and marker.get("name") == "head"
    )


def test_add_dictionary_derives_lemma_from_existing_note_voice(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    manifest_path, _original_juan_hash = _write_bundle_with_marker_asset_for(
        bundle,
        TEXT_ID,
        markers=[
            {"type": "voice", "offset": 2, "length": 3, "name": "note", "id": "n1"},
        ],
        body_text="北東書丨丨",
    )

    stats = _process_one(
        bundle,
        manifest_path,
        TEXT_ID,
        short=None,
        source="dictionary",
        force=False,
        dry_run=False,
    )

    assert stats["by_name"] == {"def": 1, "lemma": 1}
    manifest = yaml.safe_load(
        (bundle / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8")
    )
    markers = _asset_markers(bundle, manifest, 1)
    assert {"type": "voice", "offset": 2, "length": 3, "name": "note", "id": "n1"} not in markers
    assert {
        "type": "voice",
        "offset": 0,
        "length": 2,
        "name": "lemma",
        "id": "dl1",
        "source": "dictionary",
    } in markers
    assert {
        "type": "voice",
        "offset": 2,
        "length": 3,
        "name": "def",
        "id": "n1",
        "source": "dictionary",
        "responds-to": "dl1",
        "lemma": "北東",
        "lemma_offset": 0,
        "lemma_length": 2,
    } in markers
    assert not any(marker.get("name") == "dict" for marker in markers)


def test_add_dictionary_force_replaces_only_dictionary_lemmas(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    manifest_path, _original_juan_hash = _write_bundle_with_marker_asset_for(
        bundle,
        TEXT_ID,
        markers=[
            {"type": "voice", "offset": 2, "length": 3, "name": "note", "id": "n1"},
        ],
        body_text="北東書丨丨",
    )
    assert _process_one(
        bundle,
        manifest_path,
        TEXT_ID,
        short=None,
        source="dictionary",
        force=False,
        dry_run=False,
    )["by_name"] == {"def": 1, "lemma": 1}

    stats = _process_one(
        bundle,
        manifest_path,
        TEXT_ID,
        short=None,
        source="dictionary",
        force=True,
        dry_run=False,
    )

    assert stats["by_name"] == {"def": 1, "lemma": 1}
    manifest = yaml.safe_load(
        (bundle / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8")
    )
    markers = _asset_markers(bundle, manifest, 1)
    assert [
        marker for marker in markers
        if marker.get("type") == "voice" and marker.get("name") == "note"
    ] == []
    assert [
        marker for marker in markers
        if marker.get("type") == "voice" and marker.get("name") == "lemma"
    ] == [
        {
            "type": "voice",
            "offset": 0,
            "length": 2,
            "name": "lemma",
            "id": "dl1",
            "source": "dictionary",
        },
    ]
    assert [
        marker for marker in markers
        if marker.get("type") == "voice" and marker.get("name") == "def"
    ] == [
        {
            "type": "voice",
            "offset": 2,
            "length": 3,
            "name": "def",
            "id": "n1",
            "source": "dictionary",
            "responds-to": "dl1",
            "lemma": "北東",
            "lemma_offset": 0,
            "lemma_length": 2,
        },
    ]


def test_add_parser_accepts_tls_note_toggle() -> None:
    from bkk.voice.cli import build_parser

    parser = build_parser()
    disabled = parser.parse_args(["add", "--bundle", "/tmp/TSTV001", "--no-tls-notes"])
    enabled = parser.parse_args(["add", "--bundle", "/tmp/TSTV001", "--tls-notes"])

    assert disabled.tls_notes is False
    assert enabled.tls_notes is True


def test_add_parser_accepts_punctuation_source() -> None:
    from bkk.voice.cli import build_parser

    args = build_parser().parse_args([
        "add",
        "--bundle",
        "/tmp/TSTV001",
        "--source",
        "punctuation",
    ])

    assert args.source == "punctuation"


def test_add_juan_selector_can_stand_in_for_text_id() -> None:
    from bkk.voice.cli import build_parser

    args = build_parser().parse_args(["add", "--juan", "KR3k0059/147"])

    assert _selected_add_args(args) == (None, "KR3k0059", None, {147})


def test_add_juan_selector_accepts_local_seq_with_bundle() -> None:
    from bkk.voice.cli import build_parser

    args = build_parser().parse_args([
        "add",
        "--bundle",
        "/tmp/KR3k0059",
        "--juan",
        "147",
    ])

    assert _selected_add_args(args) == (Path("/tmp/KR3k0059"), None, None, {147})


def test_add_skips_occupied_id_scan_when_no_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = tmp_path / TEXT_ID
    manifest_path, _original_juan_hash = _write_bundle_with_marker_asset(bundle)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("occupied id scan should be problem-only")

    monkeypatch.setattr("bkk.voice.cli._occupied_marker_ids_for_juan", fail_if_called)

    stats = _process_one(
        bundle,
        manifest_path,
        TEXT_ID,
        short=None,
        source="parens",
        force=False,
        dry_run=False,
    )

    assert stats["by_name"] == {"note": 1}


def _write_two_juan_inline_paren_bundle(bundle_dir: Path) -> Path:
    return _write_two_juan_inline_paren_bundle_for(bundle_dir, TEXT_ID)


def _write_two_juan_inline_paren_bundle_for(
    bundle_dir: Path,
    text_id: str,
) -> Path:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    for seq, markers in (
        (1, [
            {"type": "punctuation", "offset": 2, "content": "(", "id": ""},
            {"type": "punctuation", "offset": 8, "content": ")", "id": ""},
        ]),
        (2, [
            {"type": "punctuation", "offset": 3, "content": "(", "id": ""},
        ]),
    ):
        juan = {
            "canonical_identifier": f"bkk:krp/{text_id}/v1/juan/{seq}",
            "seq": seq,
            "body": {
                "text": "abcdefghij",
                "hash": "sha256:" + "0" * 64,
                "markers": [marker_to_flow(marker) for marker in markers],
            },
            "hash": ZERO_HASH,
        }
        juan["hash"] = _self_hash(juan)
        juan_name = f"{text_id}_{seq:03d}.yaml"
        (bundle_dir / juan_name).write_text(dump(juan), encoding="utf-8")
        parts.append(marker_to_flow({
            "seq": seq,
            "filename": juan_name,
            "hash": juan["hash"],
        }))

    manifest = {
        "canonical_identifier": f"bkk:krp/{text_id}/v1",
        "assets": {"parts": parts},
        "metadata": {
            "title": "Test",
            "identifiers": {"krp": text_id},
            "edition": {"short": "bkk"},
        },
        "hash": ZERO_HASH,
    }
    manifest["hash"] = manifest_hash(manifest)
    manifest_path = bundle_dir / f"{text_id}.manifest.yaml"
    manifest_path.write_text(dump(manifest), encoding="utf-8")
    return manifest_path


def _asset_markers(bundle: Path, manifest: dict, seq: int) -> list[dict]:
    entry = next(
        item for item in manifest["assets"]["markers"]
        if isinstance(item, dict) and item.get("seq") == seq
    )
    asset = yaml.safe_load((bundle / entry["filename"]).read_text(encoding="utf-8"))
    return asset["markers"]["body"]


def _bundle_asset_body_markers(bundle: Path, text_id: str) -> list[dict]:
    manifest = yaml.safe_load(
        (bundle / f"{text_id}.manifest.yaml").read_text(encoding="utf-8")
    )
    return _asset_markers(bundle, manifest, 1)


def test_add_marks_unresolved_juan_and_writes_resolvable_juans(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    _write_two_juan_inline_paren_bundle(bundle)

    rc = _run_add(
        bundle,
        None,
        source="parens",
        force=False,
        dry_run=False,
    )

    assert rc == 1
    manifest = yaml.safe_load((bundle / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8"))
    seq1_markers = _asset_markers(bundle, manifest, 1)
    assert any(marker.get("type") == "voice" for marker in seq1_markers)
    seq2_markers = _asset_markers(bundle, manifest, 2)
    problem = next(marker for marker in seq2_markers if marker.get("type") == "voice:problem")
    assert problem["offset"] == 3
    assert problem["source"] == "parens"
    assert problem["code"] == "unmatched-open"
    assert problem["id"].startswith(f"{TEXT_ID}_bkk_002-bkkvprob")
    assert manifest["hash"] == manifest_hash(manifest)


def test_add_selected_juan_skips_unselected_problem_juan(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    manifest_path = _write_two_juan_inline_paren_bundle(bundle)

    stats = _process_one(
        bundle,
        manifest_path,
        TEXT_ID,
        short=None,
        source="parens",
        force=False,
        dry_run=False,
        selected_juans={1},
    )

    assert stats["juans"] == 1
    assert stats["by_name"] == {"note": 1}
    manifest = yaml.safe_load((bundle / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8"))
    marker_entries = [
        item for item in manifest["assets"].get("markers", [])
        if isinstance(item, dict)
    ]
    assert [item["seq"] for item in marker_entries] == [1]
    assert any(marker.get("type") == "voice" for marker in _asset_markers(bundle, manifest, 1))


def test_add_text_prefix_processes_matching_bundles_only(tmp_path: Path) -> None:
    for text_id in ("KR1a0001", "KR1a0002", "KR3a0001"):
        _write_bundle_with_marker_asset_for(tmp_path / text_id, text_id)

    rc = _run_add(
        None,
        tmp_path,
        text_prefix="KR1a",
        source="parens",
        force=False,
        dry_run=False,
    )

    assert rc == 0
    for text_id in ("KR1a0001", "KR1a0002"):
        markers = _bundle_asset_body_markers(tmp_path / text_id, text_id)
        assert any(marker.get("type") == "voice" for marker in markers)
    markers = _bundle_asset_body_markers(tmp_path / "KR3a0001", "KR3a0001")
    assert not any(marker.get("type") == "voice" for marker in markers)


def test_add_force_clears_stale_problem_after_marker_fix(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    _write_two_juan_inline_paren_bundle(bundle)
    assert _run_add(bundle, None, source="parens", force=False, dry_run=False) == 1

    juan2_path = bundle / f"{TEXT_ID}_002.yaml"
    juan2 = yaml.safe_load(juan2_path.read_text(encoding="utf-8"))
    juan2["body"]["markers"].append(
        marker_to_flow({"type": "punctuation", "offset": 8, "content": ")", "id": ""})
    )
    juan2["hash"] = _self_hash(juan2)
    juan2_path.write_text(dump(juan2), encoding="utf-8")

    assert _run_add(bundle, None, source="parens", force=True, dry_run=False) == 0
    manifest = yaml.safe_load((bundle / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8"))
    seq2_markers = _asset_markers(bundle, manifest, 2)
    assert not any(marker.get("type") == "voice:problem" for marker in seq2_markers)
    assert any(marker.get("type") == "voice" for marker in seq2_markers)


def test_add_default_rerun_resumes_failed_juans_only(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    _write_two_juan_inline_paren_bundle(bundle)
    assert _run_add(bundle, None, source="parens", force=False, dry_run=False) == 1

    juan2_path = bundle / f"{TEXT_ID}_002.yaml"
    juan2 = yaml.safe_load(juan2_path.read_text(encoding="utf-8"))
    juan2["body"]["markers"].append(
        marker_to_flow({"type": "punctuation", "offset": 8, "content": ")", "id": ""})
    )
    juan2["hash"] = _self_hash(juan2)
    juan2_path.write_text(dump(juan2), encoding="utf-8")

    assert _run_add(bundle, None, source="parens", force=False, dry_run=False) == 0

    manifest = yaml.safe_load((bundle / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8"))
    seq1_markers = _asset_markers(bundle, manifest, 1)
    seq2_markers = _asset_markers(bundle, manifest, 2)
    assert [
        marker for marker in seq1_markers
        if marker.get("type") == "voice" and marker.get("name") == "note"
    ] == [
        {"type": "voice", "offset": 2, "length": 6, "name": "note", "id": "n1"},
    ]
    assert not any(marker.get("type") == "voice:problem" for marker in seq2_markers)
    assert {
        "type": "voice",
        "offset": 3,
        "length": 5,
        "name": "note",
        "id": "n1",
    } in seq2_markers


def test_voice_problems_command_writes_report(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    _write_two_juan_inline_paren_bundle(bundle)
    assert _run_add(bundle, None, source="parens", force=False, dry_run=False) == 1

    report = tmp_path / "voice-problems.jsonl"
    from bkk.voice.cli import run

    rc = run([
        "problems",
        "--corpus", str(tmp_path),
        "--text-id", TEXT_ID,
        "--out", str(report),
    ])

    assert rc == 0
    rows = read_voice_problems_report(report)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == 1
    assert row["textid"] == TEXT_ID
    assert row["seq"] == 2
    assert row["bucket"] == "body"
    assert row["offset"] == 3
    assert row["code"] == "unmatched-open"


def test_voice_problems_command_accepts_text_prefix(tmp_path: Path) -> None:
    from bkk.voice.cli import run

    for text_id in ("KR1a0001", "KR1a0002", "KR3a0001"):
        bundle = tmp_path / text_id
        _write_two_juan_inline_paren_bundle_for(bundle, text_id)
        assert _run_add(bundle, None, source="parens", force=False, dry_run=False) == 1

    report = tmp_path / "voice-problems.jsonl"
    rc = run([
        "problems",
        "--corpus", str(tmp_path),
        "--text-prefix", "KR1a",
        "--out", str(report),
    ])

    assert rc == 0
    rows = read_voice_problems_report(report)
    assert [row["textid"] for row in rows] == ["KR1a0001", "KR1a0002"]


def test_voice_problems_text_id_errors_when_bundle_missing(tmp_path: Path) -> None:
    from bkk.voice.cli import run

    report = tmp_path / "voice-problems.jsonl"
    rc = run([
        "problems",
        "--corpus", str(tmp_path),
        "--text-id", "KR0a9999",
        "--out", str(report),
    ])

    assert rc == 2
    assert not report.exists()


def test_add_updates_configured_voice_problem_report(
    tmp_path: Path, monkeypatch,
) -> None:
    bundle = tmp_path / TEXT_ID
    _write_two_juan_inline_paren_bundle(bundle)
    report = tmp_path / "voice-problems.jsonl"
    write_voice_problems_report([
        {
            "id": 1,
            "textid": "OTHER001",
            "title": "Other",
            "edition": None,
            "seq": 1,
            "bucket": "body",
            "offset": 1,
            "length": 0,
            "marker_id": "OTHER001_bkk_001-bkkvprob1",
            "source": "parens",
            "code": "unmatched-open",
            "message": "other",
        },
    ], report)
    monkeypatch.setenv("BKK_VOICE_PROBLEMS_REPORT", str(report))

    assert _run_add(bundle, None, source="parens", force=False, dry_run=False) == 1

    rows = read_voice_problems_report(report)
    assert [row["textid"] for row in rows] == ["OTHER001", TEXT_ID]
    row = next(row for row in rows if row["textid"] == TEXT_ID)
    assert row["seq"] == 2
    assert row["offset"] == 3
    assert row["code"] == "unmatched-open"


def test_add_clears_configured_voice_problem_report_for_clean_bundle(
    tmp_path: Path, monkeypatch,
) -> None:
    bundle = tmp_path / TEXT_ID
    _write_two_juan_inline_paren_bundle(bundle)
    report = tmp_path / "voice-problems.jsonl"
    monkeypatch.setenv("BKK_VOICE_PROBLEMS_REPORT", str(report))
    assert _run_add(bundle, None, source="parens", force=False, dry_run=False) == 1

    juan2_path = bundle / f"{TEXT_ID}_002.yaml"
    juan2 = yaml.safe_load(juan2_path.read_text(encoding="utf-8"))
    juan2["body"]["markers"].append(
        marker_to_flow({"type": "punctuation", "offset": 8, "content": ")", "id": ""})
    )
    juan2["hash"] = _self_hash(juan2)
    juan2_path.write_text(dump(juan2), encoding="utf-8")

    assert _run_add(bundle, None, source="parens", force=True, dry_run=False) == 0

    assert read_voice_problems_report(report) == []
