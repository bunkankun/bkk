"""Cross-source bundle merge: TLS-first, KRP-on-top.

Three scopes:

1. ``inspect_existing_bundle`` — pure read, all four states.
2. ``extend_master_editions`` — appends to the editions list and re-hashes.
3. End-to-end via the CLI: TLS → KRP merges; KRP → TLS errors.

The end-to-end tests are skipped when the input fixtures are missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bkk.importer.cli import BundleConflictError, _import_one_tls, run
from bkk.importer.write.merge import (
    extend_master_editions,
    inspect_existing_bundle,
)


REPO = Path(__file__).resolve().parents[1]
FIXTURE_TEXT_ID = "KR6q0053"
TLS_FIXTURE_ROOT = REPO / "input" / "tls"
TLS_FIXTURE_XML = (
    TLS_FIXTURE_ROOT / "tls-texts" / "data" / "KR6" / "KR6q"
    / f"{FIXTURE_TEXT_ID}.xml"
)
KRP_FIXTURE_ROOT = REPO / "input" / "krp"
KRP_FIXTURE_REPO = KRP_FIXTURE_ROOT / FIXTURE_TEXT_ID


fixtures_present = pytest.mark.skipif(
    not (TLS_FIXTURE_XML.exists() and KRP_FIXTURE_REPO.exists()),
    reason=(
        f"need both TLS xml ({TLS_FIXTURE_XML}) and KRP repo "
        f"({KRP_FIXTURE_REPO})"
    ),
)


# ---------- inspect_existing_bundle ----------------------------------------


def test_inspect_empty(tmp_path: Path):
    state = inspect_existing_bundle(tmp_path, "NONE001")
    assert state.state == "empty"
    assert state.manifest_path is None
    assert state.editions == []


def test_inspect_tls(tmp_path: Path):
    """TLS-shaped bundle: source.yaml sidecar present, no entity_encoding."""
    bundle_root = tmp_path / "FAKE001"
    bundle_root.mkdir()
    (bundle_root / "FAKE001.manifest.yaml").write_text(
        "canonical_identifier: bkk:krp/FAKE001/v1\n"
        "metadata: {}\n",
        encoding="utf-8",
    )
    (bundle_root / "FAKE001.source.yaml").write_text("text_id: FAKE001\n",
                                                     encoding="utf-8")

    state = inspect_existing_bundle(tmp_path, "FAKE001")
    assert state.state == "tls"
    assert state.manifest_path == bundle_root / "FAKE001.manifest.yaml"
    assert state.editions == []


def test_inspect_krp(tmp_path: Path):
    """KRP-shaped bundle: entity_encoding present, no sidecar."""
    bundle_root = tmp_path / "FAKE002"
    bundle_root.mkdir()
    (bundle_root / "FAKE002.manifest.yaml").write_text(
        "canonical_identifier: bkk:krp/FAKE002/v1\n"
        "entity_encoding:\n"
        "  identifier: bkk:encoding/kanripo-pua-v1\n"
        "  hash: sha256:0\n"
        "editions:\n"
        "  - {short: WYG, label: WenYuanGe}\n"
        "metadata: {}\n",
        encoding="utf-8",
    )
    state = inspect_existing_bundle(tmp_path, "FAKE002")
    assert state.state == "krp"
    assert state.editions == ["WYG"]


def test_inspect_unknown(tmp_path: Path):
    """Manifest exists, no sidecar, no entity_encoding — can't classify."""
    bundle_root = tmp_path / "FAKE003"
    bundle_root.mkdir()
    (bundle_root / "FAKE003.manifest.yaml").write_text(
        "canonical_identifier: bkk:krp/FAKE003/v1\n"
        "metadata: {}\n",
        encoding="utf-8",
    )
    state = inspect_existing_bundle(tmp_path, "FAKE003")
    assert state.state == "unknown"


# ---------- extend_master_editions -----------------------------------------


def test_extend_master_editions_appends_and_rehashes(tmp_path: Path):
    """Adds new editions, leaves existing alone, recomputes hash."""
    manifest_path = tmp_path / "X.manifest.yaml"
    manifest_path.write_text(
        "canonical_identifier: bkk:krp/X/v1\n"
        "canonical_set: {identifier: bkk:charset/cjk-v1, hash: 'sha256:0'}\n"
        "assets: {parts: []}\n"
        "metadata: {title: T}\n"
        "hash: sha256:dead\n",
        encoding="utf-8",
    )

    final = extend_master_editions(manifest_path, [
        {"short": "WYG", "label": "文淵閣"},
        {"short": "T"},
    ])
    assert [e["short"] for e in final] == ["WYG", "T"]

    # Re-read; editions present, hash recomputed (no longer sha256:dead).
    reloaded = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert reloaded["editions"] == [
        {"short": "WYG", "label": "文淵閣"},
        {"short": "T"},
    ]
    assert reloaded["hash"] != "sha256:dead"
    assert reloaded["hash"].startswith("sha256:")


def test_extend_master_editions_skips_duplicates(tmp_path: Path):
    """Re-importing the same KRP recipe shouldn't grow the editions list."""
    manifest_path = tmp_path / "X.manifest.yaml"
    manifest_path.write_text(
        "canonical_identifier: bkk:krp/X/v1\n"
        "editions:\n"
        "  - {short: WYG, label: 文淵閣}\n"
        "assets: {parts: []}\n"
        "metadata: {}\n"
        "hash: sha256:dead\n",
        encoding="utf-8",
    )
    final = extend_master_editions(manifest_path, [
        {"short": "WYG", "label": "文淵閣"},
        {"short": "T"},
    ])
    assert [e["short"] for e in final] == ["WYG", "T"]


# ---------- end-to-end via CLI ---------------------------------------------


@fixtures_present
def test_cli_krp_merges_into_existing_tls(tmp_path: Path):
    """TLS imported first, then KRP — KRP editions slot under editions/,
    TLS surface is preserved, master manifest editions: list grows."""
    rc = run([
        "--format", "tls",
        "--in", str(TLS_FIXTURE_ROOT),
        "--text-id", FIXTURE_TEXT_ID,
        "--out", str(tmp_path),
    ])
    assert rc == 0
    bundle_root = tmp_path / FIXTURE_TEXT_ID
    master_path = bundle_root / f"{FIXTURE_TEXT_ID}.manifest.yaml"
    sidecar_path = bundle_root / f"{FIXTURE_TEXT_ID}.source.yaml"
    assert master_path.is_file()
    assert sidecar_path.is_file()

    # Snapshot the TLS-owned files so we can assert they survive intact.
    tls_master_text = master_path.read_text(encoding="utf-8")
    tls_sidecar_text = sidecar_path.read_text(encoding="utf-8")
    tls_t_edition_dir = bundle_root / "editions" / "T"
    assert tls_t_edition_dir.is_dir()
    tls_t_manifest_text = (
        tls_t_edition_dir / f"{FIXTURE_TEXT_ID}-T.manifest.yaml"
    ).read_text(encoding="utf-8")

    rc = run([
        "--format", "krp",
        "--in", str(KRP_FIXTURE_ROOT),
        "--text-id", FIXTURE_TEXT_ID,
        "--out", str(tmp_path),
    ])
    assert rc == 0

    # TLS surface untouched (apart from the master manifest's editions list).
    assert sidecar_path.read_text(encoding="utf-8") == tls_sidecar_text
    assert (
        tls_t_edition_dir / f"{FIXTURE_TEXT_ID}-T.manifest.yaml"
    ).read_text(encoding="utf-8") == tls_t_manifest_text

    # Master manifest at root: editions: list grew, source still TLS-shaped.
    master_after = yaml.safe_load(master_path.read_text(encoding="utf-8"))
    assert "entity_encoding" not in master_after, (
        "TLS-owned master must not gain entity_encoding from the merge"
    )
    new_shorts = {
        e["short"] for e in (master_after.get("editions") or [])
        if isinstance(e, dict)
    }
    assert "master" in new_shorts, "demoted KRP master should appear"
    # KR6q0053 KRP repo declares at least one witness in addition to master.
    assert len(new_shorts) >= 2

    # KRP editions present under editions/.
    editions_dir = bundle_root / "editions"
    for short in new_shorts:
        if short == "T":
            continue  # TLS-owned, already present
        assert (editions_dir / short).is_dir(), f"editions/{short}/ missing"
        assert (
            editions_dir / short / f"{FIXTURE_TEXT_ID}-{short}.manifest.yaml"
        ).is_file()

    # PUA-map.yaml — if KRP synthesis produced one, it must live at the
    # bundle root (not under editions/), even in merge mode. Some fixtures
    # contain no PUA codepoints, in which case no file is written.
    pua = bundle_root / "PUA-map.yaml"
    assert not (bundle_root / "editions" / "master" / "PUA-map.yaml").exists()
    if pua.exists():
        assert pua.is_file()


@fixtures_present
def test_cli_tls_into_existing_krp_errors(tmp_path: Path, capsys):
    """KRP first, then TLS → conflict error; bundle must not be modified."""
    rc = run([
        "--format", "krp",
        "--in", str(KRP_FIXTURE_ROOT),
        "--text-id", FIXTURE_TEXT_ID,
        "--out", str(tmp_path),
    ])
    assert rc == 0
    bundle_root = tmp_path / FIXTURE_TEXT_ID
    master_path = bundle_root / f"{FIXTURE_TEXT_ID}.manifest.yaml"
    krp_master_text = master_path.read_text(encoding="utf-8")

    rc = run([
        "--format", "tls",
        "--in", str(TLS_FIXTURE_ROOT),
        "--text-id", FIXTURE_TEXT_ID,
        "--out", str(tmp_path),
    ])
    assert rc == 1, "TLS into KRP must fail"
    err = capsys.readouterr().err
    assert "KRP-sourced bundle already exists" in err
    assert "TLS imports must precede KRP" in err

    # KRP master untouched.
    assert master_path.read_text(encoding="utf-8") == krp_master_text
    # No TLS sidecar appeared.
    assert not (bundle_root / f"{FIXTURE_TEXT_ID}.source.yaml").exists()


def test_tls_into_unknown_state_errors(tmp_path: Path):
    """A manifest with no source.yaml and no entity_encoding is unclassifiable
    — the TLS importer must refuse rather than silently overwriting."""
    bundle_root = tmp_path / FIXTURE_TEXT_ID
    bundle_root.mkdir()
    (bundle_root / f"{FIXTURE_TEXT_ID}.manifest.yaml").write_text(
        "canonical_identifier: bkk:krp/X/v1\nmetadata: {}\n",
        encoding="utf-8",
    )

    class _Args:
        in_root = TLS_FIXTURE_ROOT
        out_root = tmp_path
        sample = None

    with pytest.raises(BundleConflictError) as excinfo:
        _import_one_tls(_Args(), FIXTURE_TEXT_ID, TLS_FIXTURE_XML, sample=None)
    assert "can't be classified" in str(excinfo.value)
