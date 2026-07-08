"""Compact KR text references across CLI entry points."""

from __future__ import annotations

import argparse

import pytest

from bkk.short_refs import (
    normalize_text_id,
    parse_text_juan_selector,
    text_id_arg,
    text_prefix_arg,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1h4", "KR1h0004"),
        ("KR1h4", "KR1h0004"),
        ("KR1h0004", "KR1h0004"),
        ("J01nA001", "J01nA001"),
    ],
)
def test_normalize_text_id(value, expected):
    assert normalize_text_id(value) == expected


def test_text_only_argument_rejects_juan_selector():
    with pytest.raises(argparse.ArgumentTypeError, match="requires a complete text"):
        text_id_arg("1h4/1")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("6", "KR6"),
        ("6q", "KR6q"),
        ("KR6q", "KR6q"),
        ("1h4", "KR1h0004"),
        ("KR1h4", "KR1h0004"),
    ],
)
def test_text_prefix_argument(value, expected):
    assert text_prefix_arg(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1h4", ("KR1h0004", None)),
        ("KR1h4", ("KR1h0004", None)),
        ("1h4/1", ("KR1h0004", 1)),
        ("KR1h0004/001", ("KR1h0004", 1)),
    ],
)
def test_text_juan_selector(value, expected):
    assert parse_text_juan_selector(value) == expected


def test_text_id_shortcut_is_wired_to_cli_parsers():
    from bkk.annotations.cli import _build_parser as annotations_parser
    from bkk.chars.cli import build_parser as chars_parser
    from bkk.exporter.cli import build_parser as exporter_parser
    from bkk.importer.cli import build_parser as importer_parser
    from bkk.index.cli import build_parser as index_parser
    from bkk.info.cli import build_parser as info_parser
    from bkk.repair.cli import build_parser as repair_parser
    from bkk.repo.cli import build_parser as repo_parser
    from bkk.validator.chars_check import _build_parser as validate_chars_parser
    from bkk.voice.cli import build_parser as voice_parser

    assert importer_parser().parse_args(
        ["--text-id", "1h4"]
    ).text_id == "KR1h0004"
    assert exporter_parser().parse_args(
        ["--text-id", "1h4"]
    ).text_id == "KR1h0004"
    assert info_parser().parse_args(
        ["--text-id", "1h4"]
    ).text_id == "KR1h0004"
    assert chars_parser().parse_args(
        ["revert", "--text-id", "1h4"]
    ).text_ids == ["KR1h0004"]
    assert validate_chars_parser().parse_args(
        ["--text-id", "1h4"]
    ).text_ids == ["KR1h0004"]
    assert annotations_parser().parse_args(
        ["validate", "--text-id", "1h4"]
    ).text_id == "KR1h0004"
    assert index_parser().parse_args(
        ["search", "index.bkkx", "term", "--text-id", "1h4"]
    ).text_id == "KR1h0004"
    assert voice_parser().parse_args(
        ["remove", "--text-id", "1h4"]
    ).text_id == "KR1h0004"
    assert repair_parser().parse_args(
        ["manifest", "--text-id", "1h4"]
    ).text_id == "KR1h0004"
    assert repo_parser().parse_args(
        ["status", "--text-prefix", "1h4"]
    ).text_prefix == "KR1h0004"


def test_legacy_selector_forms_still_parse():
    from bkk.annotations.cli import _build_parser as annotations_parser
    from bkk.index.cli import build_parser as index_parser
    from bkk.repair.cli import build_parser as repair_parser
    from bkk.repo.cli import build_parser as repo_parser
    from bkk.voice.cli import build_parser as voice_parser

    assert annotations_parser().parse_args(
        ["validate", "1h4"]
    ).legacy_text_id == "KR1h0004"
    assert index_parser().parse_args(
        ["search", "index.bkkx", "term", "--textid", "1h4"]
    ).legacy_textid == "KR1h0004"
    assert voice_parser().parse_args(
        ["remove", "1h4"]
    ).legacy_bundle == "KR1h0004"
    assert repair_parser().parse_args(
        ["manifest", "1h4"]
    ).legacy_bundle == "KR1h0004"
    assert repo_parser().parse_args(
        ["status", "1h4"]
    ).legacy_prefix == "KR1h0004"


def test_canonical_and_legacy_selector_conflicts_are_rejected(capsys):
    from bkk.annotations.cli import run as annotations_run
    from bkk.index.cli import run as index_run
    from bkk.repair.cli import run as repair_run
    from bkk.repo.cli import run as repo_run
    from bkk.voice.cli import run as voice_run

    assert annotations_run(["validate", "1h4", "--text-id", "1h4"]) == 2
    assert "provide only one" in capsys.readouterr().err

    with pytest.raises(SystemExit) as exc:
        index_run([
            "search", "missing.bkkx", "term", "--textid", "1h4",
            "--text-id", "1h4",
        ])
    assert exc.value.code == 2
    assert "provide only one" in capsys.readouterr().err

    assert repair_run(["manifest", "1h4", "--text-id", "1h4"]) == 2
    assert "exactly one" in capsys.readouterr().err

    assert voice_run(["remove", "1h4", "--text-id", "1h4"]) == 2
    assert "exactly one" in capsys.readouterr().err

    assert repo_run([
        "--corpus", "/nonexistent", "status", "1h4", "--text-prefix", "1h4",
    ]) == 2
    assert "provide only one" in capsys.readouterr().err
