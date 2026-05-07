"""Resolution tests for the TLS importer's letter-suffix split-sub-file
canonical-id lookup. A handful of TLS texts are split across files like
``KR2b007a.xml``, ``KR2b007b.xml`` whose TEI header declares the same
canonical ``<idno type="kanripo">KR2b0007</idno>``. ``--text-id
KR2b0007`` must resolve to all of them.

These tests are independent of the on-disk TLS fixture so they always
run.
"""

from __future__ import annotations

from pathlib import Path

from bkk.importer.cli import _find_tls_texts, run


def _make_tls_root(tmp_path: Path, files: dict[str, str]) -> Path:
    in_root = tmp_path / "src"
    base = in_root / "tls-texts" / "data"
    for rel, content in files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return in_root


def _tei_with_kanripo_id(canonical: str, xml_id: str) -> str:
    return (
        f'<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:id="{xml_id}">'
        f'<teiHeader><fileDesc><titleStmt><title>x</title></titleStmt>'
        f'<publicationStmt><p/></publicationStmt><sourceDesc><p/></sourceDesc>'
        f'</fileDesc><profileDesc><textClass>'
        f'<idno type="kanripo">{canonical}</idno>'
        f'</textClass></profileDesc></teiHeader>'
        f'<text><body/></text></TEI>'
    )


def test_find_tls_texts_exact_match(tmp_path: Path):
    in_root = _make_tls_root(tmp_path, {
        "KR6/KR6q/KR6q0053.xml": "<root/>",
    })
    matches = _find_tls_texts(in_root, "KR6q0053")
    assert [p.name for p in matches] == ["KR6q0053.xml"]


def test_find_tls_texts_split_subfiles(tmp_path: Path):
    files = {
        f"KR2/KR2b/KR2b007{c}.xml":
            _tei_with_kanripo_id("KR2b0007", f"KR2b007{c}")
        for c in "abc"
    }
    in_root = _make_tls_root(tmp_path, files)
    matches = _find_tls_texts(in_root, "KR2b0007")
    assert sorted(p.name for p in matches) == [
        "KR2b007a.xml", "KR2b007b.xml", "KR2b007c.xml",
    ]


def test_find_tls_texts_split_filters_non_matching_idno(tmp_path: Path):
    """A neighbouring file matching the glob but declaring a different
    canonical id must be rejected."""
    in_root = _make_tls_root(tmp_path, {
        "KR2/KR2b/KR2b007a.xml": _tei_with_kanripo_id("KR2b0007", "KR2b007a"),
        "KR2/KR2b/KR2b007z.xml": _tei_with_kanripo_id("KR2b0008", "KR2b007z"),
    })
    matches = _find_tls_texts(in_root, "KR2b0007")
    assert [p.name for p in matches] == ["KR2b007a.xml"]


def test_find_tls_texts_unknown_id_no_match(tmp_path: Path):
    in_root = _make_tls_root(tmp_path, {
        "KR9/KR9z/KR9z0001.xml": "<root/>",
    })
    assert _find_tls_texts(in_root, "KR9z9999") == []


def test_cli_single_text_unknown_id_errors(tmp_path: Path, capsys):
    in_root = _make_tls_root(tmp_path, {
        "KR9/KR9z/KR9z0001.xml": "<root/>",
    })
    out_root = tmp_path / "out"
    rc = run([
        "--format", "tls",
        "--in", str(in_root),
        "--text-id", "KR9z9999",
        "--out", str(out_root),
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "KR9z9999.xml not found" in err
    assert "split sub-files" in err
