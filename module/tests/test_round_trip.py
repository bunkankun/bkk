"""Bundle round-trip: import → export → import yields the same bundle.

Pipeline:
1. Read TLS source (KR6q0053) → bundle A; write to disk.
2. Export bundle A through the new exporter → text + swl + doc XMLs.
3. Re-read those XMLs → bundle B; write to disk.
4. Compare every YAML in the two bundle trees (manifest, juan, ann file,
   edition manifest, edition juan) as Python dicts.

The source sidecar is excluded — it carries absolute source-file paths and
its captured XML trees can differ in trailing whitespace between runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bkk.exporter.recipe import Recipe
from bkk.exporter.tls import export_tls_from_recipe
from bkk.importer.cli import _find_tls_texts
from bkk.importer.read.tls import read_tls
from bkk.importer.write.bundle import write_bundle


REPO = Path(__file__).resolve().parents[1]
TEXT_ID = "KR6q0053"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _read_input(in_root: Path):
    matches = _find_tls_texts(in_root, TEXT_ID)
    assert matches
    text_xml = matches[0]
    return read_tls(
        text_xml,
        in_root / "tls-data" / "notes" / "swl" / f"{TEXT_ID}-ann.xml",
        in_root / "tls-data" / "notes" / "doc" / f"{TEXT_ID}-ann.xml",
        TEXT_ID,
    )


@pytest.fixture(scope="module")
def round_trip_dirs(tmp_path_factory) -> tuple[Path, Path]:
    work = tmp_path_factory.mktemp("rt")

    # Bundle A from the original source.
    bundle_a = _read_input(REPO / "input" / "tls")
    a_root = work / "a"
    write_bundle(bundle_a, a_root)
    a_bundle = a_root / TEXT_ID

    # Export bundle A → exported XMLs.
    exports = work / "exports"
    recipe = Recipe(
        format="tls",
        bundle=a_bundle.resolve(),
        output_dir=exports.resolve(),
        source_path=work,
    )
    export_tls_from_recipe(recipe)

    # Re-read the exports → bundle B.
    bundle_b = read_tls(
        exports / f"{TEXT_ID}.xml",
        exports / "swl" / f"{TEXT_ID}-ann.xml",
        exports / "doc" / f"{TEXT_ID}-ann.xml",
        TEXT_ID,
    )
    b_root = work / "b"
    write_bundle(bundle_b, b_root)
    b_bundle = b_root / TEXT_ID
    return a_bundle, b_bundle


def test_master_manifest_equal(round_trip_dirs: tuple[Path, Path]):
    a, b = round_trip_dirs
    assert _load(a / f"{TEXT_ID}.manifest.yaml") == _load(
        b / f"{TEXT_ID}.manifest.yaml"
    )


def test_master_juan_equal(round_trip_dirs: tuple[Path, Path]):
    a, b = round_trip_dirs
    assert _load(a / f"{TEXT_ID}_001.yaml") == _load(b / f"{TEXT_ID}_001.yaml")


def test_ann_file_equal(round_trip_dirs: tuple[Path, Path]):
    a, b = round_trip_dirs
    assert _load(a / f"{TEXT_ID}_001.ann.yaml") == _load(
        b / f"{TEXT_ID}_001.ann.yaml"
    )


def test_edition_manifest_equal(round_trip_dirs: tuple[Path, Path]):
    a, b = round_trip_dirs
    rel = Path("editions") / "T" / f"{TEXT_ID}-T.manifest.yaml"
    assert _load(a / rel) == _load(b / rel)


def test_edition_juan_equal(round_trip_dirs: tuple[Path, Path]):
    a, b = round_trip_dirs
    rel = Path("editions") / "T" / f"{TEXT_ID}_001-T.yaml"
    assert _load(a / rel) == _load(b / rel)
