"""Tests for ``bkk chars canonicalize``."""

from __future__ import annotations

import csv
from pathlib import Path
import threading

import pytest
import yaml

from bkk.chars.canonicalize import (
    SUBSTITUTION_REASON,
    InvalidSubstitutionMarkerError,
    UnmappedCodepointError,
    canonicalize_text,
    revert_substitution_markers,
)
from bkk.chars.run import run_canonicalize, run_revert
from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.marker_assets import build_marker_asset, hydrate_juan_markers, load_marker_asset
from bkk.chars.refs import (
    CanonicalizationContext,
    MappingAsset,
    MappingEntry,
    load_context,
)


def _toy_ctx() -> CanonicalizationContext:
    mapping = MappingAsset(
        canonical_identifier="bkk:mapping/test-v1",
        hash="sha256:" + "1" * 64,
        filename="test-mapping.yaml",
    )
    return CanonicalizationContext(
        charset_id="bkk:charset/test-v1",
        charset_hash="sha256:" + "2" * 64,
        charset_filename="test-charset.yaml",
        inclusion_blocks=[(0x4E00, 0x9FFF)],
        excluded={0x5434: {"reason": "kZVariant", "replaced_by": 0x5449}},
        mappings=[mapping],
        mapping_entries={
            0x5434: MappingEntry(
                entry_id="tf-0001",
                replacement_cp=0x5449,
                reason="kZVariant",
                mapping_index=0,
            ),
        },
    )


def test_canonicalize_no_substitutions():
    ctx = _toy_ctx()
    text = "周易"  # both chars are in CJK Unified
    new_text, markers = canonicalize_text(text, ctx)
    assert new_text == text
    assert markers == []


def test_canonicalize_replaces_excluded_codepoint():
    ctx = _toy_ctx()
    src = chr(0x5434)         # excluded (kZVariant)
    repl = chr(0x5449)        # canonical replacement
    text = "周" + src + "易"
    new_text, markers = canonicalize_text(text, ctx)
    assert new_text == "周" + repl + "易"
    assert len(markers) == 1
    m = markers[0]
    assert m["type"] == "substitution"
    assert m["offset"] == 1
    assert m["original"] == src
    assert m["replacement"] == repl
    assert m["reason"] == SUBSTITUTION_REASON
    assert m["mapping"]["identifier"] == "bkk:mapping/test-v1"
    assert m["mapping"]["entry"] == "tf-0001"
    assert m["mapping"]["hash"].startswith("sha256:")


def test_canonicalize_raises_on_unmapped_outside_set():
    ctx = _toy_ctx()
    # U+0041 'A' is outside the inclusion block and has no mapping entry.
    with pytest.raises(UnmappedCodepointError) as exc_info:
        canonicalize_text("周A易", ctx)
    assert exc_info.value.offset == 1
    assert exc_info.value.codepoint == 0x0041


def test_canonicalize_empty_text():
    ctx = _toy_ctx()
    assert canonicalize_text("", ctx) == ("", [])


def test_canonicalize_offsets_unchanged_for_1to1_replacements():
    """Two adjacent substitutions: each marker's offset is the position
    in the post-substitution text stream, which (since every replacement
    is 1:1) equals the position in the input stream."""
    ctx = _toy_ctx()
    src = chr(0x5434)
    repl = chr(0x5449)
    new_text, markers = canonicalize_text(src + src, ctx)
    assert new_text == repl + repl
    assert [m["offset"] for m in markers] == [0, 1]


def test_load_context_default_refs_dir():
    """The shipped charset and mapping load cleanly and self-verify."""
    ctx = load_context()
    assert ctx.charset_id == "bkk:charset/cjk-v1"
    assert ctx.charset_hash.startswith("sha256:")
    assert any(
        lo <= 0x4E00 <= hi for lo, hi in ctx.inclusion_blocks
    )  # CJK Unified
    assert any(
        lo <= 0x105000 <= hi for lo, hi in ctx.inclusion_blocks
    )  # BKK PUA
    assert 0x5434 in ctx.excluded
    assert 0x5434 in ctx.mapping_entries
    entry = ctx.mapping_entries[0x5434]
    assert entry.replacement_cp == 0x5433
    assert entry.entry_id.startswith("vf-")
    assert ctx.mappings[entry.mapping_index].canonical_identifier == (
        "bkk:mapping/variant-fold-v1"
    )


def test_load_context_real_charset_covers_excluded_with_mapping():
    """Every excluded codepoint in the bootstrap charset is resolvable
    through the shipped mapping, so canonicalize_text never raises
    UnmappedCodepointError for shipped corpus characters that are inside
    one of the inclusion blocks."""
    ctx = load_context()
    missing = [cp for cp in ctx.excluded if cp not in ctx.mapping_entries]
    assert missing == [], (
        f"{len(missing)} excluded codepoint(s) lack a mapping entry: "
        f"{['U+{:04X}'.format(cp) for cp in missing[:5]]}"
    )

def test_revert_substitution_markers_restores_originals_and_drops_markers():
    src = chr(0x5434)
    repl = chr(0x5449)
    punctuation = {"type": "punctuation", "offset": 2, "content": "。"}
    substitution = {
        "type": "substitution",
        "offset": 1,
        "original": src,
        "replacement": repl,
        "reason": SUBSTITUTION_REASON,
        "mapping": {"identifier": "bkk:mapping/test-v1", "hash": "sha256:x", "entry": "tf-0001"},
    }

    new_text, kept, removed = revert_substitution_markers(
        "周" + repl + "易",
        [punctuation, substitution],
    )

    assert new_text == "周" + src + "易"
    assert kept == [punctuation]
    assert removed == [substitution]


def test_revert_substitution_markers_rejects_mismatch():
    with pytest.raises(InvalidSubstitutionMarkerError):
        revert_substitution_markers(
            "周易",
            [{"type": "substitution", "offset": 1, "original": "吴", "replacement": "吾"}],
        )


def _self_hash(data: dict) -> str:
    zeroed = dict(data)
    zeroed["hash"] = ZERO_HASH
    return sha256_jcs(zeroed)


def test_run_revert_restores_text_and_refreshes_external_markers(tmp_path: Path):
    text_id = "KR0chr01"
    bundle_dir = tmp_path / text_id
    bundle_dir.mkdir()
    (bundle_dir / "assets").mkdir()

    original = chr(0x5434)
    replacement = chr(0x5433)
    mapping_id = "bkk:mapping/variant-fold-v1"
    mapping_hash = "sha256:" + "1" * 64
    juan_name = f"{text_id}_001.yaml"
    head_id = f"{text_id}_T_001-h"

    juan = {
        "canonical_identifier": f"bkk:krp/{text_id}/bkk/v1/juan/1",
        "seq": 1,
        "body": {
            "text": "周" + replacement + "易",
            "hash": sha256_text("周" + replacement + "易"),
            "markers": [
                marker_to_flow({"type": "tls:head", "offset": 0, "content": "卷一", "id": head_id}),
            ],
        },
        "metadata": {"title": "Chars revert", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    juan["hash"] = _self_hash(juan)
    (bundle_dir / juan_name).write_text(dump(juan), encoding="utf-8")

    marker_asset = build_marker_asset(
        text_id,
        1,
        None,
        {
            "body": [
                {
                    "type": "substitution",
                    "offset": 1,
                    "original": original,
                    "replacement": replacement,
                    "reason": SUBSTITUTION_REASON,
                    "mapping": {
                        "identifier": mapping_id,
                        "hash": mapping_hash,
                        "entry": "vf-0001",
                    },
                },
                {"type": "punctuation", "offset": 2, "content": "。"},
            ],
        },
    )
    asset_name = f"assets/{text_id}_001.markers.yaml"
    (bundle_dir / asset_name).write_text(dump(marker_asset), encoding="utf-8")

    manifest = {
        "canonical_identifier": f"bkk:krp/{text_id}/v1",
        "canonical_location": f"https://kanripo.org/bkk/{text_id}/v1",
        "canonical_set": {"identifier": "bkk:charset/cjk-v1", "hash": "sha256:" + "2" * 64},
        "mappings": [marker_to_flow({"canonical_identifier": mapping_id, "hash": mapping_hash})],
        "assets": {
            "parts": [marker_to_flow({"seq": 1, "filename": juan_name, "hash": juan["hash"]})],
            "markers": [marker_to_flow({"seq": 1, "role": "markers", "filename": asset_name, "hash": marker_asset["hash"]})],
        },
        "table_of_contents": [
            {
                "ref": marker_to_flow({"seq": 1, "marker_id": head_id, "span": ["body", 0, 3]}),
                "label": "卷一",
                "type": "section",
                "level": 1,
            }
        ],
        "metadata": {"title": "Chars revert", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    manifest["hash"] = manifest_hash(manifest)
    (bundle_dir / f"{text_id}.manifest.yaml").write_text(dump(manifest), encoding="utf-8")

    assert run_revert(tmp_path, text_ids=[text_id], log_file=None) == 0

    new_manifest = yaml.safe_load(
        (bundle_dir / f"{text_id}.manifest.yaml").read_text(encoding="utf-8")
    )
    new_juan = yaml.safe_load((bundle_dir / juan_name).read_text(encoding="utf-8"))
    new_asset = load_marker_asset(bundle_dir, new_manifest, 1)
    hydrated = hydrate_juan_markers(new_juan, new_asset)

    assert new_juan["body"]["text"] == "周" + original + "易"
    assert hydrated["body"]["text"] == "周" + original + "易"
    assert [m["type"] for m in hydrated["body"]["markers"]] == [
        "tls:head",
        "punctuation",
    ]
    assert "mappings" not in new_manifest
    assert new_manifest["assets"]["parts"][0]["hash"] == new_juan["hash"]
    assert new_manifest["hash"] == manifest_hash(new_manifest)


def test_run_revert_recovers_orphan_stale_marker_asset(tmp_path: Path):
    """A rerun cleans marker assets orphaned by the earlier revert code."""
    text_id = "KR0chr02"
    bundle_dir = tmp_path / text_id
    bundle_dir.mkdir()
    (bundle_dir / "assets").mkdir()

    original = chr(0x5434)
    replacement = chr(0x5433)
    juan_name = f"{text_id}_001.yaml"
    juan = {
        "canonical_identifier": f"bkk:krp/{text_id}/bkk/v1/juan/1",
        "seq": 1,
        "body": {
            # Text is already restored, but an orphan asset still has the old
            # substitution marker.
            "text": "周" + original + "易",
            "hash": sha256_text("周" + original + "易"),
        },
        "metadata": {"title": "Orphan cleanup", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    juan["hash"] = _self_hash(juan)
    (bundle_dir / juan_name).write_text(dump(juan), encoding="utf-8")

    marker_asset = build_marker_asset(
        text_id,
        1,
        None,
        {
            "body": [
                {
                    "type": "substitution",
                    "offset": 1,
                    "original": original,
                    "replacement": replacement,
                    "reason": SUBSTITUTION_REASON,
                    "mapping": {
                        "identifier": "bkk:mapping/variant-fold-v1",
                        "hash": "sha256:" + "1" * 64,
                        "entry": "vf-0001",
                    },
                },
                {"type": "punctuation", "offset": 2, "content": "。"},
            ],
        },
    )
    asset_name = f"assets/{text_id}_001.markers.yaml"
    (bundle_dir / asset_name).write_text(dump(marker_asset), encoding="utf-8")

    manifest = {
        "canonical_identifier": f"bkk:krp/{text_id}/v1",
        "canonical_location": f"https://kanripo.org/bkk/{text_id}/v1",
        "canonical_set": {"identifier": "bkk:charset/cjk-v1", "hash": "sha256:" + "2" * 64},
        "assets": {
            "parts": [marker_to_flow({"seq": 1, "filename": juan_name, "hash": juan["hash"]})],
            # No marker entry: this is the orphaned-asset state to recover.
        },
        "table_of_contents": [],
        "metadata": {"title": "Orphan cleanup", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    manifest["hash"] = manifest_hash(manifest)
    (bundle_dir / f"{text_id}.manifest.yaml").write_text(dump(manifest), encoding="utf-8")

    assert run_revert(tmp_path, text_ids=[text_id], log_file=None) == 0

    new_manifest = yaml.safe_load(
        (bundle_dir / f"{text_id}.manifest.yaml").read_text(encoding="utf-8")
    )
    new_juan = yaml.safe_load((bundle_dir / juan_name).read_text(encoding="utf-8"))
    new_asset = load_marker_asset(bundle_dir, new_manifest, 1)
    hydrated = hydrate_juan_markers(new_juan, new_asset)

    assert new_juan["body"]["text"] == "周" + original + "易"
    assert [m["type"] for m in hydrated["body"]["markers"]] == ["punctuation"]
    assert all(
        m.get("type") != "substitution"
        for markers in (new_asset.get("markers") or {}).values()
        for m in markers
    )
    assert new_manifest["assets"]["markers"][0]["filename"] == asset_name
    assert new_manifest["hash"] == manifest_hash(new_manifest)


def test_run_revert_jobs_processes_bundles_concurrently(tmp_path: Path, monkeypatch):
    bundle_a = tmp_path / "KR0chr03"
    bundle_b = tmp_path / "KR0chr04"
    bundle_a.mkdir()
    bundle_b.mkdir()

    barrier = threading.Barrier(2)
    lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_run_revert_bundle(bundle_dir: Path, *, dry_run: bool):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        barrier.wait(timeout=2)
        with lock:
            active -= 1
        return {
            "juans": 1,
            "reverted": 0,
            "manifest_changed": False,
            "lines": [f"  {bundle_dir.name}: done"],
        }

    monkeypatch.setattr(
        "bkk.chars.run._select_bundles",
        lambda out_root, text_ids, log_fh=None: [bundle_a, bundle_b],
    )
    monkeypatch.setattr("bkk.chars.run._run_revert_bundle", fake_run_revert_bundle)

    assert run_revert(tmp_path, text_ids=[bundle_a.name, bundle_b.name], log_file=None, jobs=2) == 0
    assert max_active == 2


def test_run_canonicalize_jobs_processes_bundles_concurrently(
    tmp_path: Path,
    monkeypatch,
):
    bundle_a = tmp_path / "KR0chr05"
    bundle_b = tmp_path / "KR0chr06"
    bundle_a.mkdir()
    bundle_b.mkdir()

    barrier = threading.Barrier(2)
    lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_process_bundle(
        bundle_dir: Path,
        text_id: str,
        *,
        ctx: CanonicalizationContext,
        dry_run: bool,
        log_fh,
        abort_on_error: bool,
    ):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        barrier.wait(timeout=2)
        with lock:
            active -= 1
        return {
            "juans": 1,
            "substitutions": 0,
            "unmapped": 0,
            "manifest_changed": False,
            "lines": [f"  {text_id}: done"],
        }

    monkeypatch.setattr(
        "bkk.chars.run._select_bundles",
        lambda out_root, text_ids, log_fh=None: [bundle_a, bundle_b],
    )
    monkeypatch.setattr("bkk.chars.run._process_bundle", fake_process_bundle)

    assert run_canonicalize(
        tmp_path,
        ctx=_toy_ctx(),
        text_ids=[bundle_a.name, bundle_b.name],
        log_file=None,
        jobs=2,
    ) == 0
    assert max_active == 2


def test_run_canonicalize_rejects_nonpositive_jobs(tmp_path: Path, monkeypatch):
    bundle = tmp_path / "KR0chr07"
    bundle.mkdir()
    monkeypatch.setattr(
        "bkk.chars.run._select_bundles",
        lambda out_root, text_ids, log_fh=None: [bundle],
    )

    assert run_canonicalize(
        tmp_path,
        ctx=_toy_ctx(),
        log_file=None,
        jobs=0,
    ) == 2


def test_run_canonicalize_writes_unmapped_report(tmp_path: Path):
    text_id = "KR0chr08"
    bundle_dir = tmp_path / text_id
    bundle_dir.mkdir()

    juan_name = f"{text_id}_001.yaml"
    original = "周AA易B"
    juan = {
        "canonical_identifier": f"bkk:krp/{text_id}/bkk/v1/juan/1",
        "seq": 1,
        "body": {
            "text": original,
            "hash": sha256_text(original),
        },
        "metadata": {"title": "Unmapped report", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    juan["hash"] = _self_hash(juan)
    (bundle_dir / juan_name).write_text(dump(juan), encoding="utf-8")

    manifest = {
        "canonical_identifier": f"bkk:krp/{text_id}/v1",
        "canonical_location": f"https://kanripo.org/bkk/{text_id}/v1",
        "canonical_set": {
            "identifier": "bkk:charset/test-v1",
            "hash": "sha256:" + "2" * 64,
        },
        "assets": {
            "parts": [
                marker_to_flow({"seq": 1, "filename": juan_name, "hash": juan["hash"]}),
            ],
        },
        "table_of_contents": [],
        "metadata": {"title": "Unmapped report", "edition": {"short": "bkk"}},
        "hash": ZERO_HASH,
    }
    manifest["hash"] = manifest_hash(manifest)
    (bundle_dir / f"{text_id}.manifest.yaml").write_text(dump(manifest), encoding="utf-8")

    report_path = tmp_path / "unmapped.tsv"

    assert run_canonicalize(
        tmp_path,
        ctx=_toy_ctx(),
        text_ids=[text_id],
        log_file=None,
        unmapped_report=report_path,
    ) == 1

    unchanged = yaml.safe_load((bundle_dir / juan_name).read_text(encoding="utf-8"))
    assert unchanged["body"]["text"] == original
    with report_path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    assert rows == [
        {
            "text_id": text_id,
            "juan": "001",
            "juan_file": juan_name,
            "bucket": "body",
            "codepoint": "U+0041",
            "char": "A",
            "count": "2",
            "offsets": "1,2",
        },
        {
            "text_id": text_id,
            "juan": "001",
            "juan_file": juan_name,
            "bucket": "body",
            "codepoint": "U+0042",
            "char": "B",
            "count": "1",
            "offsets": "4",
        },
    ]


def test_canonicalize_cli_forwards_jobs(tmp_path: Path, monkeypatch):
    from bkk.chars import cli

    captured: dict = {}

    def fake_run_canonicalize(out_root: Path, **kwargs):
        captured["out_root"] = out_root
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "load_context", lambda refs_dir: _toy_ctx())
    monkeypatch.setattr(cli, "run_canonicalize", fake_run_canonicalize)

    assert cli.run([
        "canonicalize",
        "--out-root",
        str(tmp_path),
        "--jobs",
        "3",
        "--unmapped-report",
        str(tmp_path / "unmapped.tsv"),
    ]) == 0
    assert captured["out_root"] == tmp_path
    assert captured["jobs"] == 3
    assert captured["unmapped_report"] == tmp_path / "unmapped.tsv"
