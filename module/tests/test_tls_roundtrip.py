"""End-to-end TLS importer test.

Runs the importer against ``import/input/tls`` (KR6q0053), then asserts the
spirit-of-the-sample invariants: hashes recompute, offsets stay in range,
annotation seg ids resolve. Finally diffs the generated tree against
``import/samples/KR6q0053`` and fails only if the divergence report contains
``unexpected`` rows.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bkk.importer.cli import _find_tls_texts
from bkk.importer.diverge import diff_trees, render_report
from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.read.tls import read_tls
from bkk.importer.write.bundle import write_bundle


REPO = Path(__file__).resolve().parents[1]
TEXT_ID = "KR6q0053"


@pytest.fixture(scope="module")
def out_root(tmp_path_factory) -> Path:
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
    write_bundle(bundle, out_dir)
    return out_dir / TEXT_ID


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_juan_text_nonempty(out_root: Path):
    juan = _load(out_root / f"{TEXT_ID}_001.yaml")
    assert juan["front"]["text"]
    assert juan["body"]["text"]


def test_text_hash_recomputes(out_root: Path):
    juan = _load(out_root / f"{TEXT_ID}_001.yaml")
    assert juan["front"]["hash"] == sha256_text(juan["front"]["text"])
    assert juan["body"]["hash"] == sha256_text(juan["body"]["text"])


def test_marker_offsets_in_range(out_root: Path):
    juan = _load(out_root / f"{TEXT_ID}_001.yaml")
    for bucket_name in ("front", "body"):
        bucket = juan[bucket_name]
        text_len = len(bucket["text"])
        for m in bucket["markers"]:
            assert 0 <= m["offset"] <= text_len, (
                f"{bucket_name}.{m['type']} offset {m['offset']} out of range "
                f"(text_len={text_len})"
            )


def test_juan_self_hash_recomputes(out_root: Path):
    juan = _load(out_root / f"{TEXT_ID}_001.yaml")
    expected = juan["hash"]
    juan_zeroed = dict(juan)
    juan_zeroed["hash"] = ZERO_HASH
    assert sha256_jcs(juan_zeroed) == expected


def test_manifest_hash_recomputes(out_root: Path):
    manifest = _load(out_root / f"{TEXT_ID}.manifest.yaml")
    expected = manifest["hash"]
    assert manifest_hash(manifest) == expected


def test_annotation_offsets_resolve(out_root: Path):
    juan = _load(out_root / f"{TEXT_ID}_001.yaml")
    ann = _load(out_root / f"{TEXT_ID}_001.ann.yaml")
    bucket_lens = {b: len(juan[b]["text"]) for b in ("front", "body") if b in juan}
    for entry in ann["annotations"]:
        bucket = entry["bucket"]
        assert bucket in bucket_lens
        assert 0 <= entry["offset"] <= bucket_lens[bucket]


def test_no_unexpected_divergences(out_root: Path):
    sample = REPO / "samples" / TEXT_ID
    if not sample.exists():
        pytest.skip("sample tree not present")
    divergences = diff_trees(sample, out_root)
    unexpected = [d for d in divergences if d.status == "unexpected"]
    if unexpected:
        report = render_report(divergences)
        pytest.fail(
            f"{len(unexpected)} unexpected divergence(s):\n"
            + report[:4000]
        )
