"""End-to-end tests for ``bkk voice remove``.

The remove operation has to (1) drop every ``voice`` marker from each
juan's bucket marker lists, (2) recompute each touched juan's self-hash,
and (3) patch the manifest's ``assets.parts[*].hash`` for those juans
and rewrite the manifest's own self-hash. These tests synthesise a
minimal bundle in ``tmp_path`` so we exercise the YAML round-trip and
hash refresh without relying on any real corpus fixture.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from bkk.importer.hashing import manifest_hash, sha256_jcs, ZERO_HASH
from bkk.importer.write.yaml_writer import dump
from bkk.marker_assets import hydrate_juan_markers, load_marker_asset
from bkk.voice.cli import _process_one_remove, _run_remove


TEXT_ID = "TST0001"


def _make_juan(seq: int, voices: list[dict], text_id: str = TEXT_ID) -> dict:
    """A minimal juan dict with one body bucket and an arbitrary set of
    pre-existing voice markers plus a non-voice marker.
    """
    markers: list[dict] = [
        {"type": "punctuation", "offset": 3, "content": "(", "id": ""},
    ]
    markers.extend(voices)
    juan = {
        "canonical_identifier": f"bkk:krp/{text_id}/v1/juan/{seq}",
        "seq": seq,
        "body": {
            "text": "abcdefghij",
            "hash": "sha256:" + "0" * 64,
            "markers": markers,
        },
        "hash": ZERO_HASH,
    }
    juan["hash"] = _self_hash(juan)
    return juan


def _self_hash(juan: dict) -> str:
    m = copy.deepcopy(juan)
    m["hash"] = ZERO_HASH
    return sha256_jcs(m)


def _write_bundle(
    bundle_dir: Path, juans: list[dict], text_id: str = TEXT_ID,
) -> Path:
    """Write the juan files plus a manifest. Returns the manifest path."""
    bundle_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    for j in juans:
        name = f"{text_id}_{j['seq']:03d}.yaml"
        (bundle_dir / name).write_text(dump(j), encoding="utf-8")
        parts.append({"seq": j["seq"], "filename": name, "hash": j["hash"]})
    manifest = {
        "canonical_identifier": f"bkk:krp/{text_id}/v1",
        "assets": {"parts": parts},
        "metadata": {
            "title": "Test", "identifiers": {"krp": text_id},
            "edition": {"short": "bkk"},
        },
        "hash": ZERO_HASH,
    }
    manifest["hash"] = manifest_hash(manifest)
    mf_path = bundle_dir / f"{text_id}.manifest.yaml"
    mf_path.write_text(dump(manifest), encoding="utf-8")
    return mf_path


def test_remove_strips_voices_and_refreshes_hashes(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    voices = [
        {"type": "voice", "offset": 0, "length": 5, "name": "note", "id": "n1"},
        {"type": "voice", "offset": 0, "length": 10, "name": "root", "id": "r1"},
    ]
    juans = [_make_juan(1, voices)]
    _write_bundle(bundle, juans)

    rc = _run_remove(str(bundle), out_root=None, dry_run=False)
    assert rc == 0

    reloaded = yaml.safe_load(
        (bundle / f"{TEXT_ID}_001.yaml").read_text(encoding="utf-8")
    )
    mf = yaml.safe_load(
        (bundle / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8")
    )
    reloaded = hydrate_juan_markers(
        reloaded, load_marker_asset(bundle, mf, 1),
    )
    markers = reloaded["body"]["markers"]
    assert all(m.get("type") != "voice" for m in markers)
    # Non-voice marker is preserved.
    assert any(m.get("type") == "punctuation" for m in markers)
    # Juan self-hash is consistent with the rewritten physical content.
    physical = yaml.safe_load(
        (bundle / f"{TEXT_ID}_001.yaml").read_text(encoding="utf-8")
    )
    physical_expected = _self_hash(physical)
    assert physical["hash"] == physical_expected
    # Manifest's parts entry tracks the new juan hash.
    assert mf["assets"]["parts"][0]["hash"] == physical_expected
    # And the manifest's own self-hash agrees.
    assert mf["hash"] == manifest_hash(mf)


def test_remove_is_noop_on_clean_bundle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = tmp_path / TEXT_ID
    juans = [_make_juan(1, voices=[])]
    _write_bundle(bundle, juans)

    juan_path = bundle / f"{TEXT_ID}_001.yaml"
    mf_path = bundle / f"{TEXT_ID}.manifest.yaml"
    juan_before = juan_path.read_text(encoding="utf-8")
    mf_before = mf_path.read_text(encoding="utf-8")

    rc = _run_remove(str(bundle), out_root=None, dry_run=False)
    assert rc == 0

    out = capsys.readouterr().out
    assert "no voice markers to remove" in out

    # Bytes unchanged: the no-op path skips both juan and manifest writes.
    assert juan_path.read_text(encoding="utf-8") == juan_before
    assert mf_path.read_text(encoding="utf-8") == mf_before


def test_remove_dry_run_does_not_write(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    voices = [
        {"type": "voice", "offset": 0, "length": 5, "name": "note", "id": "n1"},
    ]
    juans = [_make_juan(1, voices)]
    _write_bundle(bundle, juans)

    juan_path = bundle / f"{TEXT_ID}_001.yaml"
    mf_path = bundle / f"{TEXT_ID}.manifest.yaml"
    juan_before = juan_path.read_text(encoding="utf-8")
    mf_before = mf_path.read_text(encoding="utf-8")

    rc = _run_remove(str(bundle), out_root=None, dry_run=True)
    assert rc == 0

    assert juan_path.read_text(encoding="utf-8") == juan_before
    assert mf_path.read_text(encoding="utf-8") == mf_before


def test_remove_text_prefix_processes_matching_bundles_only(tmp_path: Path) -> None:
    voices = [
        {"type": "voice", "offset": 0, "length": 5, "name": "note", "id": "n1"},
    ]
    for text_id in ("KR1a0001", "KR1a0002", "KR3a0001"):
        bundle = tmp_path / text_id
        _write_bundle(bundle, [_make_juan(1, voices, text_id)], text_id=text_id)

    rc = _run_remove(None, tmp_path, text_prefix="KR1a", dry_run=False)

    assert rc == 0
    for text_id, has_voice in (
        ("KR1a0001", False),
        ("KR1a0002", False),
        ("KR3a0001", True),
    ):
        bundle = tmp_path / text_id
        manifest = yaml.safe_load(
            (bundle / f"{text_id}.manifest.yaml").read_text(encoding="utf-8")
        )
        juan = yaml.safe_load(
            (bundle / f"{text_id}_001.yaml").read_text(encoding="utf-8")
        )
        juan = hydrate_juan_markers(
            juan, load_marker_asset(bundle, manifest, 1),
        )
        assert any(
            marker.get("type") == "voice"
            for marker in juan["body"]["markers"]
        ) is has_voice


def test_process_one_remove_returns_counts(tmp_path: Path) -> None:
    bundle = tmp_path / TEXT_ID
    voices = [
        {"type": "voice", "offset": 0, "length": 5, "name": "note", "id": "n1"},
        {"type": "voice", "offset": 0, "length": 8, "name": "root", "id": "r1"},
    ]
    juans = [_make_juan(1, voices), _make_juan(2, voices=[])]
    mf_path = _write_bundle(bundle, juans)

    stats = _process_one_remove(
        bundle, mf_path, TEXT_ID, short=None, dry_run=False,
    )
    assert stats["juans"] == 2
    assert stats["removed"] == 2
    # Two report lines: one for the juan we touched, one for the no-op.
    assert any("removed 2 voice marker(s)" in line for line in stats["lines"])
    assert any("no voice markers to remove" in line for line in stats["lines"])


def test_remove_missing_bundle_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    rc = _run_remove(str(tmp_path / "does-not-exist"), out_root=None, dry_run=False)
    assert rc == 2
    err = capsys.readouterr().err
    assert "bundle directory not found" in err


def test_remove_missing_manifest_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = tmp_path / TEXT_ID
    bundle.mkdir()
    (bundle / f"{TEXT_ID}_001.yaml").write_text("seq: 1\n", encoding="utf-8")

    rc = _run_remove(str(bundle), out_root=None, dry_run=False)
    assert rc == 2
    err = capsys.readouterr().err
    assert "master manifest not found" in err
