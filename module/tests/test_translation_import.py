"""End-to-end tests for ``bkk import --format translation``.

The four sample translations under ``module/samples/translations/`` cover
the shapes we need to exercise:

- ``KR1h0001-en.xml``                    — base case, English
- ``KR1h0004-en.xml``                    — English with empty <seg/>s
- ``KR1h0004-en-588d9aad.xml``           — snapshot with a hex revision suffix
- ``KR1h0004-fr-138ffefe.xml``           — French with availability/license
                                            and per-seg lang≠bundle-language
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from bkk.importer.cli import run


REPO = Path(__file__).resolve().parents[1]
SAMPLES = REPO / "samples" / "translations"


@pytest.fixture
def in_root(tmp_path: Path) -> Path:
    """A throwaway input tree shaped like the canonical TLS layout."""
    target = tmp_path / "in" / "tls-data" / "translations"
    target.mkdir(parents=True)
    for xml in SAMPLES.glob("*.xml"):
        shutil.copy2(xml, target / xml.name)
    return tmp_path / "in"


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_single_file_import(in_root: Path, tmp_path: Path):
    out = tmp_path / "out"
    rc = run([
        "--format", "translation",
        "--in", str(in_root),
        "--out", str(out),
        "--text-id", "KR1h0004",
        "--lang", "en",
        "--yes",
    ])
    assert rc == 0
    bundle_dir = out / "translations" / "KR1h0004-en"
    assert bundle_dir.is_dir()
    manifest_path = bundle_dir / "KR1h0004-en.manifest.yaml"
    assert manifest_path.is_file()

    m = _load(manifest_path)
    assert m["language"] == "en"
    assert m["canonical_identifier"] == "bkk:translation/KR1h0004-en/v1"
    assert m["source"]["canonical_identifier"] == "bkk:krp/KR1h0004/v1"
    assert m["source"]["hash"] is None  # no source bundle present
    assert m["hash"].startswith("sha256:")
    assert len(m["juan"]) >= 1
    for entry in m["juan"]:
        juan_file = bundle_dir / entry["file"]
        assert juan_file.is_file()
        assert entry["hash"].startswith("sha256:")


def test_empty_segs_dropped(in_root: Path, tmp_path: Path):
    """Self-closing ``<seg .../>`` elements must not produce empty spans."""
    out = tmp_path / "out"
    rc = run([
        "--format", "translation",
        "--in", str(in_root),
        "--out", str(out),
        "--text-id", "KR1h0004",
        "--lang", "en",
        "--yes",
    ])
    assert rc == 0

    src_xml = (SAMPLES / "KR1h0004-en.xml").read_text(encoding="utf-8")
    src_segs = src_xml.count("<seg ")
    src_empty = src_xml.count("/>") - src_xml.count("xml/>")  # rough; refined below
    # Count empty <seg> openings precisely: self-closing form ends with "/>".
    empty_segs = sum(
        1 for line in src_xml.splitlines()
        if line.strip().startswith("<seg ") and line.rstrip().endswith("/>")
    )

    bundle_dir = out / "translations" / "KR1h0004-en"
    rendered = sum(
        len([ln for ln in (p.read_text(encoding="utf-8").splitlines()) if ln])
        for p in bundle_dir.glob("KR1h0004-en_*.md")
    )
    assert rendered == src_segs - empty_segs > 0


def test_per_juan_split_by_corresp_juan(in_root: Path, tmp_path: Path):
    """Segments addressing source juan 002 must land in the _002.md file."""
    out = tmp_path / "out"
    rc = run([
        "--format", "translation",
        "--in", str(in_root),
        "--out", str(out),
        "--text-id", "KR1h0004",
        "--lang", "fr",
        "--yes",
    ])
    assert rc == 0

    bundle_dir = out / "translations" / "KR1h0004-fr-138ffefe"
    juan_002 = bundle_dir / "KR1h0004-fr-138ffefe_002.md"
    assert juan_002.is_file()
    body = juan_002.read_text(encoding="utf-8")
    # Every span in this file should reference juan 002.
    for line in body.splitlines():
        if not line.strip():
            continue
        assert "corresp=002-" in line or 'corresp="002-' in line, line


def test_hash_reproducible(in_root: Path, tmp_path: Path):
    """Importing the same input twice produces byte-identical bundles."""
    out_a = tmp_path / "out-a"
    out_b = tmp_path / "out-b"
    args = [
        "--format", "translation",
        "--in", str(in_root),
        "--text-id", "KR1h0004",
        "--lang", "en",
        "--yes",
    ]
    assert run(args + ["--out", str(out_a)]) == 0
    assert run(args + ["--out", str(out_b)]) == 0

    a = out_a / "translations" / "KR1h0004-en"
    b = out_b / "translations" / "KR1h0004-en"
    a_files = sorted(p.name for p in a.iterdir())
    b_files = sorted(p.name for p in b.iterdir())
    assert a_files == b_files
    for name in a_files:
        assert (a / name).read_bytes() == (b / name).read_bytes()


def test_snapshots_imported_as_separate_bundles(
    in_root: Path, tmp_path: Path,
):
    """KR1h0004-en.xml and KR1h0004-en-588d9aad.xml must coexist."""
    out = tmp_path / "out"
    rc = run([
        "--format", "translation",
        "--in", str(in_root),
        "--out", str(out),
        "--text-id", "KR1h0004",
        "--lang", "en",
        "--yes",
    ])
    assert rc == 0
    base = out / "translations"
    assert (base / "KR1h0004-en").is_dir()
    assert (base / "KR1h0004-en-588d9aad").is_dir()

    m1 = _load(base / "KR1h0004-en" / "KR1h0004-en.manifest.yaml")
    m2 = _load(
        base / "KR1h0004-en-588d9aad"
        / "KR1h0004-en-588d9aad.manifest.yaml"
    )
    assert m1["canonical_identifier"] != m2["canonical_identifier"]


def test_french_responsibility_and_license(in_root: Path, tmp_path: Path):
    """French sample carries availability/status and a 'creator' resp."""
    out = tmp_path / "out"
    rc = run([
        "--format", "translation",
        "--in", str(in_root),
        "--out", str(out),
        "--text-id", "KR1h0004",
        "--lang", "fr",
        "--yes",
    ])
    assert rc == 0
    m = _load(
        out / "translations" / "KR1h0004-fr-138ffefe"
        / "KR1h0004-fr-138ffefe.manifest.yaml"
    )
    assert m["language"] == "fr"
    assert m["license"] == "The copyright status of this work is unclear"
    roles = [r["role"] for r in m["responsibility"]]
    assert "translator" in roles
    assert "creator" in roles


def test_per_seg_lang_emitted_only_when_different(
    in_root: Path, tmp_path: Path,
):
    """English bundle: lang attr absent. French bundle: lang=en preserved."""
    out = tmp_path / "out"
    rc = run([
        "--format", "translation",
        "--in", str(in_root),
        "--out", str(out),
        "--yes",
    ])
    assert rc == 0
    en_md = list(
        (out / "translations" / "KR1h0001-en").glob("KR1h0001-en_*.md")
    )[0].read_text(encoding="utf-8")
    fr_md = list(
        (out / "translations" / "KR1h0004-fr-138ffefe")
        .glob("KR1h0004-fr-138ffefe_*.md")
    )[0].read_text(encoding="utf-8")

    assert "lang=" not in en_md, "English bundle should drop redundant lang=en"
    assert "lang=en" in fr_md, "French bundle keeps the (mismatched) lang=en"


def test_source_hash_resolved_when_source_bundle_present(
    in_root: Path, tmp_path: Path,
):
    """When <out>/<source-id>/<source-id>.manifest.yaml exists, copy its hash."""
    out = tmp_path / "out"
    src_dir = out / "KR1h0004"
    src_dir.mkdir(parents=True)
    fake_hash = "sha256:" + "ab" * 32
    (src_dir / "KR1h0004.manifest.yaml").write_text(
        f"canonical_identifier: bkk:krp/KR1h0004/v1\nhash: {fake_hash}\n",
        encoding="utf-8",
    )

    rc = run([
        "--format", "translation",
        "--in", str(in_root),
        "--out", str(out),
        "--text-id", "KR1h0004",
        "--lang", "en",
        "--yes",
    ])
    assert rc == 0
    m = _load(
        out / "translations" / "KR1h0004-en" / "KR1h0004-en.manifest.yaml"
    )
    assert m["source"]["hash"] == fake_hash
