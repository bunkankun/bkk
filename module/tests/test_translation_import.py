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

import re
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


def _load_manifest_from_md(path: Path) -> dict:
    """Parse the YAML front-matter from a Markdown file."""
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    assert len(parts) >= 3, f"missing YAML front-matter in {path}"
    return yaml.safe_load(parts[1])


def _body_from_md(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    return parts[2] if len(parts) >= 3 else text


_BODY_LINE_RE = re.compile(r"^\[(?P<text>.*)\]\{(?P<refs>[^}]+)\}$")


def _ref_tokens(line: str) -> list[str]:
    m = _BODY_LINE_RE.match(line)
    assert m, f"body line doesn't match span shape: {line!r}"
    return [tok.lstrip("@") for tok in m.group("refs").split()]


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
    bundle_dir = out / "translations" / "KR1h0004" / "en" / "KR1h0004-en"
    assert bundle_dir.is_dir()
    bundle_md = bundle_dir / "KR1h0004-en.md"
    assert bundle_md.is_file()

    m = _load_manifest_from_md(bundle_md)
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
        # Per-juan file's front-matter hash equals the manifest's juan entry.
        jh = _load_manifest_from_md(juan_file)
        assert jh["hash"] == entry["hash"]
        assert juan_file.read_text(encoding="utf-8").startswith("---\n")


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
    empty_segs = sum(
        1 for line in src_xml.splitlines()
        if line.strip().startswith("<seg ") and line.rstrip().endswith("/>")
    )

    bundle_dir = out / "translations" / "KR1h0004" / "en" / "KR1h0004-en"
    rendered = sum(
        len([ln for ln in _body_from_md(p).splitlines() if ln.strip()])
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

    bundle_dir = (
        out / "translations" / "KR1h0004" / "fr" / "KR1h0004-fr-138ffefe"
    )
    juan_002 = bundle_dir / "KR1h0004-fr-138ffefe_002.md"
    assert juan_002.is_file()
    # Every body span ref must reference juan 002.
    for line in _body_from_md(juan_002).splitlines():
        if not line.strip():
            continue
        refs = _ref_tokens(line)
        for r in refs:
            assert r.startswith("002-"), (r, line)


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

    a = out_a / "translations" / "KR1h0004" / "en" / "KR1h0004-en"
    b = out_b / "translations" / "KR1h0004" / "en" / "KR1h0004-en"
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
    en = out / "translations" / "KR1h0004" / "en"
    assert (en / "KR1h0004-en").is_dir()
    assert (en / "KR1h0004-en-588d9aad").is_dir()

    m1 = _load_manifest_from_md(en / "KR1h0004-en" / "KR1h0004-en.md")
    m2 = _load_manifest_from_md(
        en / "KR1h0004-en-588d9aad" / "KR1h0004-en-588d9aad.md"
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
    m = _load_manifest_from_md(
        out / "translations" / "KR1h0004" / "fr"
        / "KR1h0004-fr-138ffefe" / "KR1h0004-fr-138ffefe.md"
    )
    assert m["language"] == "fr"
    assert m["license"] == "The copyright status of this work is unclear"
    roles = [r["role"] for r in m["responsibility"]]
    assert "translator" in roles
    assert "creator" in roles


def test_per_seg_lang_emitted_only_when_different(
    in_root: Path, tmp_path: Path,
):
    """English bundle: no marker carries lang. French bundle: lang=en in markers."""
    out = tmp_path / "out"
    rc = run([
        "--format", "translation",
        "--in", str(in_root),
        "--out", str(out),
        "--yes",
    ])
    assert rc == 0

    en_dir = out / "translations" / "KR1h0001" / "en" / "KR1h0001-en"
    en_juan = next(en_dir.glob("KR1h0001-en_*.md"))
    en_hdr = _load_manifest_from_md(en_juan)
    assert all("lang" not in m for m in en_hdr["markers"]), (
        "English bundle should drop redundant lang=en from markers"
    )

    fr_dir = (
        out / "translations" / "KR1h0004" / "fr" / "KR1h0004-fr-138ffefe"
    )
    fr_juan = next(fr_dir.glob("KR1h0004-fr-138ffefe_*.md"))
    fr_hdr = _load_manifest_from_md(fr_juan)
    assert any(m.get("lang") == "en" for m in fr_hdr["markers"]), (
        "French bundle should preserve the (mismatched) lang=en"
    )


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
    m = _load_manifest_from_md(
        out / "translations" / "KR1h0004" / "en"
        / "KR1h0004-en" / "KR1h0004-en.md"
    )
    assert m["source"]["hash"] == fake_hash


def test_juan_file_header_shape(in_root: Path, tmp_path: Path):
    """Per-juan front-matter carries exactly the documented keys."""
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
    bundle_dir = out / "translations" / "KR1h0004" / "en" / "KR1h0004-en"
    manifest = _load_manifest_from_md(bundle_dir / "KR1h0004-en.md")
    by_label = {entry["label"]: entry for entry in manifest["juan"]}

    juan_paths = sorted(bundle_dir.glob("KR1h0004-en_*.md"))
    assert juan_paths
    for juan_path in juan_paths:
        hdr = _load_manifest_from_md(juan_path)
        assert set(hdr.keys()) == {
            "canonical_identifier", "bundle", "juan_seq",
            "juan_label", "hash", "markers",
        }, hdr.keys()
        label = hdr["juan_label"]
        assert hdr["canonical_identifier"] == (
            f"bkk:translation/KR1h0004-en/v1#juan/{label}"
        )
        assert hdr["bundle"] == "bkk:translation/KR1h0004-en/v1"
        assert hdr["hash"] == by_label[label]["hash"]
        body_lines = [
            ln for ln in _body_from_md(juan_path).splitlines() if ln.strip()
        ]
        assert len(hdr["markers"]) == len(body_lines)


def test_body_marker_refs_match_header(in_root: Path, tmp_path: Path):
    """Body span refs (in order) must match `markers[i].ref` exactly."""
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
    bundle_dir = out / "translations" / "KR1h0004" / "en" / "KR1h0004-en"
    for juan_path in bundle_dir.glob("KR1h0004-en_*.md"):
        hdr = _load_manifest_from_md(juan_path)
        body_lines = [
            ln for ln in _body_from_md(juan_path).splitlines() if ln.strip()
        ]
        for marker, line in zip(hdr["markers"], body_lines):
            body_refs = _ref_tokens(line)
            mref = marker["ref"]
            expected = [mref] if isinstance(mref, str) else list(mref)
            assert body_refs == expected, (juan_path.name, body_refs, expected)


def test_bundle_hash_changes_with_segment_edit(
    in_root: Path, tmp_path: Path,
):
    """Mutating a source segment's text must change the bundle hash."""
    out_a = tmp_path / "out-a"
    args = [
        "--format", "translation",
        "--in", str(in_root),
        "--text-id", "KR1h0004",
        "--lang", "en",
        "--yes",
    ]
    assert run(args + ["--out", str(out_a)]) == 0
    m_before = _load_manifest_from_md(
        out_a / "translations" / "KR1h0004" / "en"
        / "KR1h0004-en" / "KR1h0004-en.md"
    )

    target = in_root / "tls-data" / "translations" / "KR1h0004-en.xml"
    original = target.read_text(encoding="utf-8")
    needle = "Zigong said,"
    assert needle in original
    target.write_text(
        original.replace(needle, "Zigong said HONK,", 1), encoding="utf-8",
    )

    out_b = tmp_path / "out-b"
    assert run(args + ["--out", str(out_b)]) == 0
    m_after = _load_manifest_from_md(
        out_b / "translations" / "KR1h0004" / "en"
        / "KR1h0004-en" / "KR1h0004-en.md"
    )
    assert m_before["hash"] != m_after["hash"]


# ---------- --on-exists -----------------------------------------------------


def test_on_exists_skip_leaves_existing_bundle_alone(
    in_root: Path, tmp_path: Path,
):
    """With --on-exists skip, a second run does not rewrite the bundle.

    Verified by dropping a sentinel file alongside the bundle entry-point
    and asserting it (and a hand-edit to the bundle .md) survive the
    second run.
    """
    out = tmp_path / "out"
    args = [
        "--format", "translation",
        "--in", str(in_root),
        "--out", str(out),
        "--text-id", "KR1h0004",
        "--lang", "en",
        "--yes",
    ]
    assert run(args) == 0
    bundle_dir = out / "translations" / "KR1h0004" / "en" / "KR1h0004-en"
    bundle_md = bundle_dir / "KR1h0004-en.md"
    sentinel = bundle_dir / "SENTINEL.txt"
    sentinel.write_text("untouched", encoding="utf-8")
    edited = bundle_md.read_text(encoding="utf-8") + "\n<!-- hand edit -->\n"
    bundle_md.write_text(edited, encoding="utf-8")

    assert run(args + ["--on-exists", "skip"]) == 0
    assert sentinel.exists() and sentinel.read_text() == "untouched"
    assert bundle_md.read_text(encoding="utf-8") == edited


def test_on_exists_overwrite_is_default(in_root: Path, tmp_path: Path):
    """Default --on-exists overwrites (regression guard for today's
    behavior)."""
    out = tmp_path / "out"
    args = [
        "--format", "translation",
        "--in", str(in_root),
        "--out", str(out),
        "--text-id", "KR1h0004",
        "--lang", "en",
        "--yes",
    ]
    assert run(args) == 0
    bundle_md = (
        out / "translations" / "KR1h0004" / "en"
        / "KR1h0004-en" / "KR1h0004-en.md"
    )
    bundle_md.write_text("CORRUPTED", encoding="utf-8")

    assert run(args) == 0
    assert bundle_md.read_text(encoding="utf-8") != "CORRUPTED"
    assert bundle_md.read_text(encoding="utf-8").startswith("---\n")


def test_on_exists_skip_bulk_prefilter_reports_skipped(
    in_root: Path, tmp_path: Path, capsys,
):
    """Bulk run with --on-exists skip: existing bundles are reported via
    stderr and the importer returns 0 even when every discovered file is
    skipped."""
    out = tmp_path / "out"
    # First pass: import every translation under in_root (no filters).
    assert run([
        "--format", "translation",
        "--in", str(in_root),
        "--out", str(out),
        "--yes",
    ]) == 0
    _ = capsys.readouterr()

    # Second pass: with skip, everything is already on disk.
    rc = run([
        "--format", "translation",
        "--in", str(in_root),
        "--out", str(out),
        "--on-exists", "skip",
        "--yes",
    ])
    assert rc == 0
    err = capsys.readouterr().err
    assert "skipped" in err and "already imported" in err
    assert "nothing to import" in err
