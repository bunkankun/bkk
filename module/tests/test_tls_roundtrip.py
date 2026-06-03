"""End-to-end TLS importer test.

Runs the importer against ``import/input/tls`` (KR6q0053), then asserts the
spirit-of-the-sample invariants: hashes recompute, offsets stay in range,
annotation seg ids resolve. Finally diffs the generated tree against
``import/samples/KR6q0053`` and fails only if the divergence report contains
``unexpected`` rows.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from bkk.importer.cli import _find_tls_texts
from bkk.importer.diverge import diff_trees, render_report
from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.read.tls import read_tls
from bkk.importer.write.bundle import write_bundle
from bkk.marker_assets import hydrate_juan_markers, load_marker_asset


REPO = Path(__file__).resolve().parents[1]
TEXT_ID = "KR6q0053"


@pytest.fixture(scope="module")
def out_root(tmp_path_factory) -> tuple[Path, Path]:
    """Run the importer once for the suite."""
    in_root = REPO / "input" / "tls"
    matches = _find_tls_texts(in_root, TEXT_ID)
    assert matches, f"{TEXT_ID}.xml not found under {in_root}"
    text_xml = matches[0]
    bundle = read_tls(
        text_xml,
        in_root / "tls-data" / "notes" / "swl" / f"{TEXT_ID}-ann.xml",
        in_root / "tls-data" / "notes" / "doc" / f"{TEXT_ID}-ann.xml",
        TEXT_ID,
    )
    out_dir = tmp_path_factory.mktemp("bkk-out")
    archive = tmp_path_factory.mktemp("bkk-annotations")
    write_bundle(bundle, out_dir, annotations_root=archive)
    return out_dir / TEXT_ID, archive


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_hydrated(bundle_dir: Path, seq: int) -> dict:
    manifest = _load(bundle_dir / f"{TEXT_ID}.manifest.yaml")
    juan = _load(bundle_dir / f"{TEXT_ID}_{seq:03d}.yaml")
    return hydrate_juan_markers(
        juan, load_marker_asset(bundle_dir, manifest, seq),
    )


def test_juan_text_nonempty(out_root: tuple[Path, Path]):
    bd, _ = out_root
    juan = _load(bd / f"{TEXT_ID}_001.yaml")
    assert juan["front"]["text"]
    assert juan["body"]["text"]


def test_text_hash_recomputes(out_root: tuple[Path, Path]):
    bd, _ = out_root
    juan = _load(bd / f"{TEXT_ID}_001.yaml")
    assert juan["front"]["hash"] == sha256_text(juan["front"]["text"])
    assert juan["body"]["hash"] == sha256_text(juan["body"]["text"])


def test_marker_offsets_in_range(out_root: tuple[Path, Path]):
    bd, _ = out_root
    juan = _load_hydrated(bd, 1)
    for bucket_name in ("front", "body"):
        bucket = juan[bucket_name]
        text_len = len(bucket["text"])
        for m in bucket["markers"]:
            assert 0 <= m["offset"] <= text_len, (
                f"{bucket_name}.{m['type']} offset {m['offset']} out of range "
                f"(text_len={text_len})"
            )


def test_juan_self_hash_recomputes(out_root: tuple[Path, Path]):
    bd, _ = out_root
    juan = _load(bd / f"{TEXT_ID}_001.yaml")
    expected = juan["hash"]
    juan_zeroed = dict(juan)
    juan_zeroed["hash"] = ZERO_HASH
    assert sha256_jcs(juan_zeroed) == expected


def test_manifest_hash_recomputes(out_root: tuple[Path, Path]):
    bd, _ = out_root
    manifest = _load(bd / f"{TEXT_ID}.manifest.yaml")
    expected = manifest["hash"]
    assert manifest_hash(manifest) == expected


def test_annotation_offsets_resolve(out_root: tuple[Path, Path]):
    bd, archive = out_root
    juan = _load(bd / f"{TEXT_ID}_001.yaml")
    bucket_lens = {b: len(juan[b]["text"]) for b in ("front", "body") if b in juan}
    ann_path = archive / TEXT_ID / f"{TEXT_ID}_001.ann.jsonl"
    for line in ann_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        bucket = record["bucket"]
        assert bucket in bucket_lens
        assert 0 <= record["bucket_offset"] <= bucket_lens[bucket]


def test_no_unexpected_divergences(out_root: tuple[Path, Path]):
    bd, _ = out_root
    sample = REPO / "samples" / TEXT_ID
    if not sample.exists():
        pytest.skip("sample tree not present")
    divergences = diff_trees(sample, bd)
    unexpected = [d for d in divergences if d.status == "unexpected"]
    if unexpected:
        report = render_report(divergences)
        pytest.fail(
            f"{len(unexpected)} unexpected divergence(s):\n"
            + report[:4000]
        )
