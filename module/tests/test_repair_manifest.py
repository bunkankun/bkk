"""Tests for ``bkk.repair.manifest.rebuild_manifests``.

Reproduces the multi-XML-file TLS bug by writing two single-juan bundles
sequentially under the same ``text_id`` (the same shape produced by
importing ``KR2b007a.xml`` then ``KR2b007b.xml``). The second
``write_bundle`` call overwrites the manifest, leaving juan 001's file
on disk but unreferenced; the rebuild should restore both juans to
``assets.parts`` and the TOC, and the resulting hashes must validate.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from bkk.importer.hashing import ZERO_HASH, sha256_jcs
from bkk.importer.ir import Bundle, Juan, Marker, Section
from bkk.importer.write.bundle import write_bundle
from bkk.repair.cli import run as repair_run
from bkk.repair.manifest import rebuild_manifests


TEXT_ID = "KR0test99"


def _seg(location: str, offset: int) -> Marker:
    return Marker(
        type="tls:seg",
        offset=offset,
        content="",
        id=f"{TEXT_ID}_T_{location}",
    )


def _section(head: str, head_id: str, text: str, markers: list[Marker]) -> Section:
    return Section(
        head_text=head,
        head_marker_id=head_id,
        text=text,
        markers=markers,
    )


def _single_juan_bundle(seq: int, head: str, body_text: str) -> Bundle:
    head_id = f"{TEXT_ID}_T_{seq:03d}-0001a.1-h"
    sec = _section(
        head, head_id, body_text,
        [
            # Real TLS parsing emits a tls:head marker for the section's
            # <head> at offset 0; the rebuild relies on these to
            # reconstruct the TOC.
            Marker(type="tls:head", offset=0, content=head, id=head_id),
            _seg(f"{seq:03d}-0001a.1", 0),
            _seg(f"{seq:03d}-0001a.2", 2),
        ],
    )
    return Bundle(
        text_id=TEXT_ID,
        juans=[Juan(seq=seq, sections=[sec])],
        metadata={"title": "Repair Test", "source": {"repository": "synthetic"}},
        edition_short="T",
    )


def _stage_split_text_bug(out_root: Path) -> Path:
    """Reproduce the multi-XML-file overwrite bug. Returns the bundle dir."""
    write_bundle(_single_juan_bundle(1, "卷一", "甲乙丙丁"), out_root)
    write_bundle(_single_juan_bundle(2, "卷二", "戊己庚辛"), out_root)
    return out_root / TEXT_ID


def test_split_text_manifest_overwrite_is_the_bug(tmp_path: Path):
    """Sanity: prior to repair the manifest only references the second juan."""
    bundle_dir = _stage_split_text_bug(tmp_path)
    mf = yaml.safe_load(
        (bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8")
    )
    parts = mf["assets"]["parts"]
    assert [p["seq"] for p in parts] == [2]
    # But the juan-001 file is on disk, just orphaned.
    assert (bundle_dir / f"{TEXT_ID}_001.yaml").is_file()


def test_rebuild_master_manifest_restores_both_juans(tmp_path: Path):
    bundle_dir = _stage_split_text_bug(tmp_path)
    summary = rebuild_manifests(bundle_dir)

    assert summary["master"]["parts"] == 2
    assert summary["master"]["toc"] >= 2  # one section entry per juan

    mf = yaml.safe_load(
        (bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8")
    )
    parts = mf["assets"]["parts"]
    assert [p["seq"] for p in parts] == [1, 2]
    assert [p["filename"] for p in parts] == [
        f"{TEXT_ID}_001.yaml",
        f"{TEXT_ID}_002.yaml",
    ]
    # Each manifest part hash matches the on-disk juan's self-hash.
    for part in parts:
        loaded = yaml.safe_load(
            (bundle_dir / part["filename"]).read_text(encoding="utf-8")
        )
        zeroed = dict(loaded)
        zeroed["hash"] = ZERO_HASH
        assert sha256_jcs(zeroed) == part["hash"]

    # Manifest's own hash recomputes.
    zeroed = dict(mf)
    zeroed["hash"] = ZERO_HASH
    assert sha256_jcs(zeroed) == mf["hash"]


def test_rebuild_edition_manifest_restores_both_juans(tmp_path: Path):
    bundle_dir = _stage_split_text_bug(tmp_path)
    rebuild_manifests(bundle_dir)

    edition_manifest = bundle_dir / "editions" / "T" / f"{TEXT_ID}-T.manifest.yaml"
    mf = yaml.safe_load(edition_manifest.read_text(encoding="utf-8"))
    parts = mf["assets"]["parts"]
    assert [p["seq"] for p in parts] == [1, 2]
    assert [p["filename"] for p in parts] == [
        f"{TEXT_ID}_001-T.yaml",
        f"{TEXT_ID}_002-T.yaml",
    ]


def test_rebuild_toc_labels_match_head_markers(tmp_path: Path):
    bundle_dir = _stage_split_text_bug(tmp_path)
    rebuild_manifests(bundle_dir)

    mf = yaml.safe_load(
        (bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8")
    )
    labels = [entry["label"] for entry in mf["table_of_contents"]]
    assert "卷一" in labels
    assert "卷二" in labels


def test_cli_resolves_bare_text_id_via_rc(tmp_path: Path, monkeypatch):
    """``bkk repair manifest <text-id>`` resolves the bundle dir against
    a configured ``import.out`` (the same default the importer uses)."""
    bundle_dir = _stage_split_text_bug(tmp_path)
    # Stub load_rc with import.out pointing at tmp_path.
    monkeypatch.setattr(
        "bkk.config.load_rc",
        lambda: {"import": {"out": tmp_path}},
    )
    rc = repair_run(["manifest", TEXT_ID])
    assert rc == 0

    mf = yaml.safe_load(
        (bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8")
    )
    assert [p["seq"] for p in mf["assets"]["parts"]] == [1, 2]


def test_cli_explicit_path_still_works(tmp_path: Path, monkeypatch):
    """A path argument bypasses the rc resolution."""
    bundle_dir = _stage_split_text_bug(tmp_path)
    monkeypatch.setattr("bkk.config.load_rc", lambda: {})
    rc = repair_run(["manifest", str(bundle_dir)])
    assert rc == 0


def test_cli_bare_id_without_rc_errors(tmp_path: Path, monkeypatch, capsys):
    """A bare id with no configured root produces a clear error."""
    monkeypatch.setattr("bkk.config.load_rc", lambda: {})
    rc = repair_run(["manifest", "KRdoes-not-exist"])
    assert rc == 2
    assert "bundle directory not found" in capsys.readouterr().err


def test_rebuild_preserves_existing_metadata(tmp_path: Path):
    bundle_dir = _stage_split_text_bug(tmp_path)
    rebuild_manifests(bundle_dir)

    mf = yaml.safe_load(
        (bundle_dir / f"{TEXT_ID}.manifest.yaml").read_text(encoding="utf-8")
    )
    assert mf["metadata"]["title"] == "Repair Test"
    assert mf["metadata"]["source"] == {"repository": "synthetic"}
    assert mf["metadata"]["edition"]["short"] == "bkk"
