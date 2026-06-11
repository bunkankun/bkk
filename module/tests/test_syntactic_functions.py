from __future__ import annotations

from pathlib import Path

import yaml

from bkk.core.syntactic_functions import (
    lint_syntactic_function_records,
    parse_syntactic_label,
)
from bkk.core_cli.cli import run as core_run


def _write_syntactic_function(
    root: Path,
    uuid: str,
    code: str,
    *,
    lint_accept: list[str] | None = None,
) -> Path:
    path = root / "syntactic-functions" / uuid[0] / f"{uuid}.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, object] = {
        "schema_version": 2,
        "uuid": uuid,
        "type": "syntactic-function",
        "labels": {"display": code, "alternate": []},
        "code": code,
    }
    if lint_accept is not None:
        record["lint_accept"] = lint_accept
    path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
    return path


def test_parse_syntactic_label_accepts_compact_labels():
    examples = [
        "npropostNab.adV>Nab",
        "vt{NEG}+Vtt[0](oN1.)+N2",
        "NP[post-npro2.][post=npro1:]adN",
        "VP{PASS}[adN][.post=npro1]",
        "NPab{N1=N2}",
    ]

    for label in examples:
        result = parse_syntactic_label(label)
        messages = [(d.code, d.message) for d in result.diagnostics]
        assert result.ok, messages
        assert not any(d.code == "unknown-token" for d in result.diagnostics)


def test_parse_syntactic_label_reports_structural_and_normalization_issues():
    result = parse_syntactic_label("vttoN1{PIVOT].+N2{PRED}")

    assert not result.ok
    assert {d.code for d in result.diagnostics} >= {"mismatched-bracket"}

    fullwidth = parse_syntactic_label("vt＋V(0)")

    assert fullwidth.normalized == "vt+V(0)"
    assert any(d.code == "fullwidth-punctuation" for d in fullwidth.diagnostics)


def test_lint_syntactic_function_records_reports_actionable_findings(tmp_path: Path):
    root = tmp_path / "core"
    _write_syntactic_function(root, "00000000-0000-0000-0000-000000000001", "vt+prep N")
    _write_syntactic_function(root, "11111111-1111-1111-1111-111111111111", "vadN{{PRED}")
    _write_syntactic_function(root, "22222222-2222-2222-2222-222222222222", "npro.post-N{SUBJECT}")
    _write_syntactic_function(root, "33333333-3333-3333-3333-333333333333", "NPab")
    _write_syntactic_function(root, "44444444-4444-4444-4444-444444444444", "NPab")

    report = lint_syntactic_function_records(root)
    codes = {item.diagnostic.code for item in report.diagnostics}

    assert report.record_count == 5
    assert report.distinct_label_count == 4
    assert "whitespace" in codes
    assert "unclosed-bracket" in codes
    assert "role-alias" in codes
    assert "duplicate-code" in codes


def test_lint_accept_suppresses_warnings_per_record(tmp_path: Path):
    root = tmp_path / "core"
    _write_syntactic_function(
        root,
        "00000000-0000-0000-0000-000000000001",
        "vt+prep N",
        lint_accept=["whitespace"],
    )
    _write_syntactic_function(
        root,
        "11111111-1111-1111-1111-111111111111",
        "vt+prep N",
    )

    report = lint_syntactic_function_records(root)
    by_path: dict[str, list[str]] = {}
    for item in report.diagnostics:
        by_path.setdefault(item.path.stem, []).append(item.diagnostic.code)

    assert "whitespace" not in by_path.get("00000000-0000-0000-0000-000000000001", [])
    assert "whitespace" in by_path.get("11111111-1111-1111-1111-111111111111", [])


def test_lint_accept_does_not_silence_errors(tmp_path: Path):
    root = tmp_path / "core"
    _write_syntactic_function(
        root,
        "00000000-0000-0000-0000-000000000001",
        "vadN{{PRED}",
        lint_accept=["unclosed-bracket"],
    )

    report = lint_syntactic_function_records(root)
    codes = {item.diagnostic.code for item in report.diagnostics}
    assert "unclosed-bracket" in codes


def test_lint_accept_malformed_emits_warning(tmp_path: Path):
    root = tmp_path / "core"
    path = root / "syntactic-functions" / "0" / "00000000-0000-0000-0000-000000000001.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({
            "uuid": "00000000-0000-0000-0000-000000000001",
            "type": "syntactic-function",
            "code": "NPab",
            "lint_accept": "whitespace",
        }, sort_keys=False),
        encoding="utf-8",
    )

    report = lint_syntactic_function_records(root)
    codes = {item.diagnostic.code for item in report.diagnostics}
    assert "lint-accept-malformed" in codes


def test_core_cli_lints_syntactic_functions(tmp_path: Path, capsys):
    root = tmp_path / "core"
    _write_syntactic_function(root, "00000000-0000-0000-0000-000000000001", "vt(+V[0]）")

    rc = core_run(["lint-syntactic-functions", str(root), "--limit", "0", "--strict"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "fullwidth-punctuation" in captured.err
    assert "unclosed-bracket" not in captured.err
    assert "checked 1 syntactic-function record" in captured.err
