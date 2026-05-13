"""Recipe rendering: named pins, marker datasets, sandboxed templates."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bkk.recipe.cli import run as recipe_cli
from bkk.recipe.render import RecipeRenderError, render_recipe
from bkk.serve.schemas import RecipeRequest


def _write_voice_bundle(root: Path, textid: str = "RND0001") -> Path:
    bundle = root / textid
    bundle.mkdir()
    body = "AAAAACCCCCBBBBB"
    markers = [
        {"type": "voice", "offset": 0, "length": 5, "name": "root", "id": "r1"},
        {
            "type": "voice",
            "offset": 5,
            "length": 5,
            "name": "commentary",
            "id": "c1",
            "responds-to": "r1",
        },
        {"type": "voice", "offset": 10, "length": 5, "name": "root", "id": "r2"},
    ]
    (bundle / f"{textid}_001.yaml").write_text(
        yaml.safe_dump(
            {
                "seq": 1,
                "body": {
                    "text": body,
                    "hash": "sha256:body",
                    "markers": markers,
                },
                "hash": "sha256:juan",
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (bundle / f"{textid}.manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "canonical_identifier": f"bkk:test/{textid}/v1",
                "hash": "sha256:manifest",
                "metadata": {"title": "Rendered Voices", "edition": {"short": "bkk"}},
                "assets": {
                    "parts": [
                        {
                            "seq": 1,
                            "filename": f"{textid}_001.yaml",
                            "hash": "sha256:juan",
                        }
                    ]
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return bundle


def _voice_recipe(textid: str = "RND0001") -> dict:
    return {
        "kind": "bkk.recipe/v1",
        "pins": [
            {
                "name": "text",
                "role": "base",
                "textid": textid,
                "selection": {"juan": 1},
            }
        ],
        "datasets": {
            "voices": {
                "from": "text",
                "collect": "markers",
                "where": {"type": "voice"},
                "include_text": True,
                "context": 2,
            }
        },
        "render": {
            "format": "markdown",
            "template": (
                "# Voices in {{ pins.text.label }}\n"
                "{% for v in datasets.voices %}\n"
                "- {{ v.name }} {{ v.id }} "
                "{{ v.textid }} {{ v.juan_seq }}/{{ v.bucket }} "
                "@{{ v.offset }}+{{ v.length }} "
                "{{ v.responds_to or 'none' }} "
                "`{{ v.left }}[{{ v.text }}]{{ v.right }}`\n"
                "{% endfor %}"
            ),
        },
    }


def test_composition_only_recipe_still_parses_with_unnamed_pin():
    recipe = RecipeRequest.model_validate({
        "pins": [{"role": "base", "textid": "RND0001"}]
    })
    assert recipe.pins[0].role == "base"


def test_render_voice_marker_dataset(tmp_path: Path):
    _write_voice_bundle(tmp_path)
    rendered = render_recipe(_voice_recipe(), corpus_root=tmp_path)

    assert "# Voices in Rendered Voices" in rendered.text
    assert "- root r1 RND0001 1/body @0+5 none `[AAAAA]CC`" in rendered.text
    assert "- commentary c1 RND0001 1/body @5+5 r1 `AA[CCCCC]BB`" in rendered.text
    assert "- root r2 RND0001 1/body @10+5 none `CC[BBBBB]`" in rendered.text

    voices = rendered.context["datasets"]["voices"]
    assert voices[1]["text"] == "CCCCC"
    assert voices[1]["left"] == "AA"
    assert voices[1]["right"] == "BB"
    assert voices[1]["responds_to"] == "r1"
    assert voices[1]["end"] == 10


def test_render_rejects_malformed_template(tmp_path: Path):
    _write_voice_bundle(tmp_path)
    recipe = _voice_recipe()
    recipe["render"]["template"] = "{{ missing.value }}"

    with pytest.raises(RecipeRenderError, match="template render failed"):
        render_recipe(recipe, corpus_root=tmp_path)


def test_render_blocks_unsafe_jinja_access(tmp_path: Path):
    _write_voice_bundle(tmp_path)
    recipe = _voice_recipe()
    recipe["render"]["template"] = "{{ ''.__class__.__mro__ }}"

    with pytest.raises(RecipeRenderError, match="template render failed"):
        render_recipe(recipe, corpus_root=tmp_path)


def test_dataset_requires_named_pin(tmp_path: Path):
    _write_voice_bundle(tmp_path)
    recipe = _voice_recipe()
    recipe["pins"][0].pop("name")

    with pytest.raises(RecipeRenderError, match="unknown pin 'text'"):
        render_recipe(recipe, corpus_root=tmp_path)


def test_recipe_render_cli_writes_markdown(tmp_path: Path):
    _write_voice_bundle(tmp_path)
    recipe_path = tmp_path / "voice.yaml"
    out_path = tmp_path / "report.md"
    recipe_path.write_text(
        yaml.safe_dump(_voice_recipe(), allow_unicode=True),
        encoding="utf-8",
    )

    rc = recipe_cli([
        "render",
        str(recipe_path),
        "--corpus",
        str(tmp_path),
        "--out",
        str(out_path),
    ])

    assert rc == 0
    assert "Voices in Rendered Voices" in out_path.read_text(encoding="utf-8")
