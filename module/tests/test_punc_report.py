"""Punctuation-comparison report: input generation + rendering."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bkk.recipe.cli import run as recipe_cli
from bkk.recipe.punc_report import (
    PuncReportError,
    build_punctuation_report,
    make_punc_report_input,
)
from bkk.recipe.render import render_recipe_file

TEMPLATE = Path(__file__).resolve().parents[1] / "recipes" / "punc-report.yaml"


def _write_bundle(
    root: Path,
    textid: str,
    juans: dict[int, str],
    *,
    core_markers: dict[int, list[dict]] | None = None,
    references: list[dict] | None = None,
) -> Path:
    bundle = root / textid
    bundle.mkdir(parents=True)
    parts = []
    core_markers = core_markers or {}
    for seq, text in juans.items():
        filename = f"{textid}_{seq:03d}.yaml"
        (bundle / filename).write_text(
            yaml.safe_dump(
                {
                    "seq": seq,
                    "body": {
                        "text": text,
                        "hash": f"sha256:body{seq}",
                        "markers": core_markers.get(seq, []),
                    },
                    "hash": f"sha256:juan{seq}",
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        parts.append({"seq": seq, "filename": filename, "hash": f"sha256:juan{seq}"})
    manifest: dict = {
        "canonical_identifier": f"bkk:test/{textid}/v1",
        "hash": "sha256:manifest",
        "metadata": {"title": textid, "edition": {"short": "bkk"}},
        "assets": {"parts": parts},
    }
    if references is not None:
        manifest["assets"]["references"] = references
    (bundle / f"{textid}.manifest.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True),
        encoding="utf-8",
    )
    return bundle


def _punct(offset: int, content: str) -> dict:
    return {"type": "punctuation", "offset": offset, "content": content}


def _write_sidecar(
    bundle: Path, textid: str, seq: int, model: str, markers: list[dict],
) -> Path:
    assets = bundle / "assets"
    assets.mkdir(exist_ok=True)
    filename = f"{textid}_{seq:03d}.{model}.punctuation.yaml"
    path = assets / filename
    path.write_text(
        yaml.safe_dump(
            {
                "schema": 1,
                "status": "complete",
                "provenance": {"model": model},
                "markers": {"front": [], "body": markers, "back": []},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


def test_make_input_discovers_core_and_sidecar(tmp_path: Path):
    bundle = _write_bundle(
        tmp_path,
        "TST0001",
        {1: "甲乙丙丁戊"},
        core_markers={1: [_punct(2, "，")]},
    )
    _write_sidecar(bundle, "TST0001", 1, "gpt-4o", [_punct(3, "。")])

    data = make_punc_report_input(corpus_root=tmp_path, textid="TST0001")

    assert data["kind"] == "bkk.recipe-input/v1"
    assert data["for_template"] == "punc-report"
    assert data["target"] == {"textid": "TST0001", "juans": [1], "bucket": "body"}
    assert data["layout"] == {"width": 40}
    sigils = [s["sigil"] for s in data["sets"]]
    assert sigils == ["base", "gpt4o"]
    assert data["sets"][1]["source"] == "llm-punctuation"
    assert data["sets"][1]["model"] == "gpt-4o"


def test_make_input_juan_filter_and_default_all(tmp_path: Path):
    _write_bundle(
        tmp_path,
        "TST0002",
        {1: "甲乙丙", 2: "丁戊己"},
        core_markers={1: [_punct(1, "、")], 2: [_punct(1, "、")]},
    )

    all_juans = make_punc_report_input(corpus_root=tmp_path, textid="TST0002")
    assert all_juans["target"]["juans"] == [1, 2]

    only_two = make_punc_report_input(
        corpus_root=tmp_path, textid="TST0002", juans=[2],
    )
    assert only_two["target"]["juans"] == [2]


def test_make_input_unknown_juan_errors(tmp_path: Path):
    _write_bundle(tmp_path, "TST0003", {1: "甲乙丙"}, core_markers={1: [_punct(1, "、")]})
    with pytest.raises(PuncReportError, match="not found"):
        make_punc_report_input(corpus_root=tmp_path, textid="TST0003", juans=[9])


def test_make_input_no_sets_errors(tmp_path: Path):
    _write_bundle(tmp_path, "TST0004", {1: "甲乙丙"})
    with pytest.raises(PuncReportError, match="no punctuation sets"):
        make_punc_report_input(corpus_root=tmp_path, textid="TST0004")


def test_build_report_fixed_width_and_intersected(tmp_path: Path):
    bundle = _write_bundle(
        tmp_path,
        "TST0005",
        {1: "甲乙丙丁戊己庚辛壬癸"},
        core_markers={1: [_punct(2, "，")]},
    )
    _write_sidecar(bundle, "TST0005", 1, "m1", [_punct(5, "。"), _punct(10, "。")])

    data = make_punc_report_input(corpus_root=tmp_path, textid="TST0005", width=4)
    report = build_punctuation_report(data, corpus_root=tmp_path)

    assert report["width"] == 4
    groups = report["groups"]
    assert [(g["offset"], g["end"]) for g in groups] == [(0, 4), (4, 8), (8, 10)]
    for g in groups:
        assert [line["sigil"] for line in g["lines"]] == ["base", "m1"]

    base_lines = [g["lines"][0]["text"] for g in groups]
    m1_lines = [g["lines"][1]["text"] for g in groups]
    assert base_lines == ["甲乙，丙丁", "戊己庚辛", "壬癸"]
    assert m1_lines == ["甲乙丙丁", "戊。己庚辛", "壬癸。"]
    assert report["warnings"] == []


def test_build_report_keeps_boundary_punctuation_on_previous_line(tmp_path: Path):
    _write_bundle(
        tmp_path,
        "TST0011",
        {1: "甲乙丙丁戊己"},
        core_markers={1: [_punct(2, "，"), _punct(4, "。")]},
    )

    data = make_punc_report_input(corpus_root=tmp_path, textid="TST0011", width=2)
    report = build_punctuation_report(data, corpus_root=tmp_path)

    assert [g["lines"][0]["text"] for g in report["groups"]] == [
        "甲乙，",
        "丙丁。",
        "戊己",
    ]


def test_build_report_orders_same_offset_punctuation(tmp_path: Path):
    _write_bundle(
        tmp_path,
        "TST0010",
        {1: "甲乙"},
        core_markers={
            1: [
                _punct(1, "《『「(：\n)"),
                _punct(1, "」』、，。；？》"),
            ]
        },
    )

    data = make_punc_report_input(corpus_root=tmp_path, textid="TST0010", width=4)
    report = build_punctuation_report(data, corpus_root=tmp_path)

    assert report["groups"][0]["lines"][0]["text"] == (
        "甲》？；。』」，、：)\n(「『《乙"
    )


def test_build_report_missing_sidecar_warns(tmp_path: Path):
    _write_bundle(
        tmp_path,
        "TST0006",
        {1: "甲乙丙"},
        core_markers={1: [_punct(1, "、")]},
    )
    data = {
        "kind": "bkk.recipe-input/v1",
        "target": {"textid": "TST0006", "juans": [1], "bucket": "body"},
        "layout": {"width": 4},
        "sets": [
            {"sigil": "base", "label": "core", "source": "core"},
            {"sigil": "ghost", "label": "ghost", "source": "llm-punctuation", "model": "ghost"},
        ],
    }
    report = build_punctuation_report(data, corpus_root=tmp_path)
    assert any("no sidecar" in w for w in report["warnings"])
    assert [line["sigil"] for line in report["groups"][0]["lines"]] == ["base", "ghost"]
    assert report["groups"][0]["lines"][1]["text"] == "甲乙丙"


def test_render_template_end_to_end(tmp_path: Path):
    bundle = _write_bundle(
        tmp_path,
        "TST0007",
        {1: "甲乙丙丁"},
        core_markers={1: [_punct(2, "，")]},
    )
    _write_sidecar(bundle, "TST0007", 1, "gpt-4o", [_punct(1, "。")])

    input_data = make_punc_report_input(corpus_root=tmp_path, textid="TST0007", width=4)
    input_path = tmp_path / "in.yaml"
    input_path.write_text(yaml.safe_dump(input_data, allow_unicode=True), encoding="utf-8")

    rendered = render_recipe_file(
        TEMPLATE, corpus_root=tmp_path, input_path=input_path,
    )
    text = rendered.text
    assert "Punctuation comparison — TST0007" in text
    assert "`base`" in text and "`gpt4o`" in text
    assert "juan 001 @000000–000004" in text
    assert "base\t甲乙，丙丁" in text
    assert "gpt4o\t甲。乙丙丁" in text


def test_external_punctuation_root_discovery(tmp_path: Path):
    corpus = tmp_path / "corpus"
    section = corpus / "TST0"
    _write_bundle(section, "TST0008", {1: "甲乙丙"})
    punctuation_root = tmp_path / "punc"
    ext = punctuation_root / "TST0" / "TST0008"
    ext.mkdir(parents=True)
    (ext / "TST0008_001.ext-model.punctuation.yaml").write_text(
        yaml.safe_dump(
            {"markers": {"body": [_punct(1, "。")]}}, allow_unicode=True,
        ),
        encoding="utf-8",
    )

    data = make_punc_report_input(
        corpus_root=corpus, textid="TST0008", punctuation_root=punctuation_root,
    )
    sigils = [s["sigil"] for s in data["sets"]]
    assert sigils == ["extmodel"]

    report = build_punctuation_report(
        data, corpus_root=corpus, punctuation_root=punctuation_root,
    )
    assert report["groups"][0]["lines"][0]["text"] == "甲。乙丙"
    assert report["warnings"] == []


def test_recipe_make_cli_writes_default_output(tmp_path: Path, monkeypatch):
    _write_bundle(
        tmp_path,
        "TST0009",
        {1: "甲乙丙"},
        core_markers={1: [_punct(1, "、")]},
    )
    monkeypatch.chdir(tmp_path)

    rc = recipe_cli([
        "make", "punc-report",
        "--text-id", "TST0009",
        "--corpus", str(tmp_path),
    ])
    assert rc == 0
    out_path = tmp_path / "TST0009.punc-report.input.yaml"
    assert out_path.is_file()
    data = yaml.safe_load(out_path.read_text(encoding="utf-8"))
    assert data["target"]["textid"] == "TST0009"
    assert data["sets"][0]["sigil"] == "base"


def test_render_requires_input_for_punctuation_report(tmp_path: Path):
    from bkk.recipe.render import RecipeRenderError, render_recipe

    recipe = {
        "kind": "bkk.recipe/v1",
        "datasets": {"report": {"collect": "punctuation_report"}},
        "render": {"format": "markdown", "template": "x"},
    }
    with pytest.raises(RecipeRenderError, match="needs an input recipe"):
        render_recipe(recipe, corpus_root=tmp_path)
