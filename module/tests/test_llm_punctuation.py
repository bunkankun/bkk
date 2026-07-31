from __future__ import annotations

from pathlib import Path

import yaml

from bkk.llm import punctuation
from bkk.llm.cli import run as llm_run


def _write_bundle(root: Path, text_id: str, text: str, markers: list[dict] | None = None) -> Path:
    bundle = root / text_id
    bundle.mkdir()
    (bundle / f"{text_id}_001.yaml").write_text(
        yaml.safe_dump(
            {
                "canonical_identifier": f"bkk:test/{text_id}/v1/juan/1",
                "seq": 1,
                "body": {
                    "text": text,
                    "markers": markers or [],
                    "hash": "sha256:0",
                },
                "hash": "sha256:0",
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (bundle / f"{text_id}.manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "canonical_identifier": f"bkk:test/{text_id}/v1",
                "canonical_location": f"bkk:test/{text_id}/v1",
                "canonical_set": {
                    "identifier": "bkk:charset/cjk-v1",
                    "hash": "sha256:" + "0" * 64,
                },
                "assets": {
                    "parts": [
                        {
                            "seq": 1,
                            "filename": f"{text_id}_001.yaml",
                            "hash": "sha256:0",
                        }
                    ]
                },
                "table_of_contents": [],
                "metadata": {"title": "Test", "edition": {"short": "bkk"}},
                "hash": "sha256:0",
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return bundle


def _add_edition(bundle: Path, text_id: str, short: str, text: str) -> None:
    ed = bundle / "editions" / short
    ed.mkdir(parents=True)
    (ed / f"{text_id}_001-{short}.yaml").write_text(
        yaml.safe_dump(
            {
                "canonical_identifier": f"bkk:test/{text_id}/{short}/v1/juan/1",
                "seq": 1,
                "body": {"text": text, "hash": "sha256:0"},
                "hash": "sha256:0",
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (ed / f"{text_id}-{short}.manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "canonical_identifier": f"bkk:test/{text_id}/{short}/v1",
                "canonical_location": f"bkk:test/{text_id}/{short}/v1",
                "canonical_set": {
                    "identifier": "bkk:charset/cjk-v1",
                    "hash": "sha256:" + "0" * 64,
                },
                "assets": {
                    "parts": [
                        {
                            "seq": 1,
                            "filename": f"{text_id}_001-{short}.yaml",
                            "hash": "sha256:0",
                        }
                    ]
                },
                "table_of_contents": [],
                "metadata": {"title": "Test", "edition": {"short": short}},
                "hash": "sha256:0",
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def test_punctuated_output_becomes_markers_and_rejects_variants() -> None:
    markers = punctuation.markers_from_punctuated_output(
        "甲乙丙",
        "甲，乙。\n\n丙",
        context_start=10,
        core_start=10,
        core_end=13,
    )

    assert markers == [
        {"type": "punctuation", "offset": 11, "content": "，"},
        {"type": "punctuation", "offset": 12, "content": "。"},
        {"type": "paragraph-break", "offset": 12, "content": ""},
    ]

    try:
        punctuation.markers_from_punctuated_output(
            "裏", "里", context_start=0, core_start=0, core_end=1,
        )
    except ValueError as exc:
        assert "unexpected character" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("variant substitution was accepted")


def test_extract_stream_regions_separates_note_and_commentary_and_excludes_head() -> None:
    markers = [
        {"type": "voice", "offset": 2, "length": 2, "name": "note", "id": "n1"},
        {
            "type": "voice",
            "offset": 4,
            "length": 2,
            "name": "commentary",
            "id": "c1",
        },
        {"type": "voice", "offset": 6, "length": 2, "name": "head", "id": "h1"},
    ]

    regions = punctuation.extract_stream_regions(
        "TEST0001", None, 1, "body", "甲乙丙丁戊己庚辛壬癸", markers,
    )

    assert [(r.stream, r.start, r.end, r.text) for r in regions] == [
        ("main", 0, 2, "甲乙"),
        ("main", 8, 10, "壬癸"),
        ("note:n1", 2, 4, "丙丁"),
        ("commentary:c1", 4, 6, "戊己"),
    ]


def test_chunk_region_uses_head_boundaries_and_context_overlap() -> None:
    region = punctuation.StreamRegion(
        text_id="TEST0001",
        edition=None,
        seq=1,
        bucket="body",
        stream="main",
        start=0,
        end=10,
        text="甲乙丙丁戊己庚辛壬癸",
    )

    chunks = punctuation.chunk_region(region, [0, 4, 10], chunk_chars=3, overlap=1)

    assert [(c.core_start, c.core_end, c.context_start, c.context_end, c.input_text) for c in chunks] == [
        (0, 3, 0, 4, "甲乙丙丁"),
        (3, 4, 2, 5, "丙丁戊"),
        (4, 7, 3, 8, "丁戊己庚辛"),
        (7, 10, 6, 10, "庚辛壬癸"),
    ]


def test_direct_cli_writes_reference_asset_without_canonical_marker_changes(
    tmp_path: Path, monkeypatch,
) -> None:
    bundle = _write_bundle(tmp_path, "TEST0001", "甲乙丙丁戊己")

    class FakeClient:
        def create_response(self, *, model: str, prompt: str, text: str) -> str:
            assert model == "test-model"
            assert text == "甲乙丙丁戊己"
            return "甲，乙。丙丁戊己"

    monkeypatch.setattr(
        punctuation, "make_openai_client", lambda ai_config: FakeClient(),
    )

    rc = llm_run([
        "punctuation",
        "run",
        "--bundle",
        str(bundle),
        "--model",
        "test-model",
        "--ai-config",
        str(tmp_path / "missing.xml"),
    ])

    assert rc == 0
    juan = yaml.safe_load((bundle / "TEST0001_001.yaml").read_text(encoding="utf-8"))
    assert "markers" not in juan["body"] or juan["body"]["markers"] == []

    manifest = yaml.safe_load(
        (bundle / "TEST0001.manifest.yaml").read_text(encoding="utf-8")
    )
    ref = manifest["assets"]["references"][0]
    assert ref["role"] == "llm-punctuation"
    assert ref["filename"] == "assets/TEST0001_001.test-model.punctuation.yaml"

    asset = yaml.safe_load((bundle / ref["filename"]).read_text(encoding="utf-8"))
    assert asset["markers"]["body"] == [
        {
            "type": "punctuation",
            "offset": 1,
            "content": "，",
            "source": "llm-punctuation",
            "model": "test-model",
            "id": "TEST0001_bkk_001-bkkpn1",
        },
        {
            "type": "punctuation",
            "offset": 2,
            "content": "。",
            "source": "llm-punctuation",
            "model": "test-model",
            "id": "TEST0001_bkk_001-bkkpn2",
        },
    ]


def test_models_cli_lists_available_models(monkeypatch, capsys) -> None:
    class FakeClient:
        def list_models(self) -> list[dict]:
            return [
                {"id": "gpt-5-mini", "owned_by": "openai"},
                {"id": "gpt-4.1", "owned_by": "openai"},
            ]

    monkeypatch.setattr(
        punctuation, "make_openai_client", lambda ai_config: FakeClient(),
    )

    rc = llm_run([
        "models",
        "--ai-config",
        "/tmp/missing.xml",
        "--contains",
        "5",
    ])

    assert rc == 0
    assert capsys.readouterr().out == "gpt-5-mini\n"


def test_ai_config_accepts_openai_config_api_token(tmp_path: Path) -> None:
    path = tmp_path / "ai-config.xml"
    path.write_text(
        "<engines><openai-config><api-token>secret</api-token>"
        "<model>gpt-test</model></openai-config></engines>",
        encoding="utf-8",
    )

    assert punctuation.load_ai_config(path) == {
        "api_key": "secret",
        "model": "gpt-test",
    }


def test_settings_default_min_chars_and_rc_override(tmp_path: Path) -> None:
    settings = punctuation.settings_from_rc(
        {"llm": {"model": "test-model", "min_chars": 9}},
        ai_config=tmp_path / "missing.xml",
        prompt=tmp_path / "prompt",
    )

    assert settings.min_chars == 9


def test_run_direct_defaults_to_master_only(tmp_path: Path, capsys) -> None:
    bundle = _write_bundle(tmp_path, "TEST0004", "甲乙丙丁戊己")
    _add_edition(bundle, "TEST0004", "W", "庚辛壬癸子丑")
    settings = punctuation.LlmSettings(
        model="test-model",
        ai_config=tmp_path / "missing.xml",
        prompt=tmp_path / "prompt",
    )
    (tmp_path / "prompt").write_text("prompt", encoding="utf-8")

    rc = punctuation.run_direct(
        bundle,
        None,
        text_id=None,
        text_prefix=None,
        selected_juans=None,
        settings=settings,
        dry_run=True,
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "[master]" in out
    assert "edition W" not in out
    assert "would submit 1 chunk request(s)" in out


def test_run_direct_include_editions_opt_in(tmp_path: Path, capsys) -> None:
    bundle = _write_bundle(tmp_path, "TEST0005", "甲乙丙丁戊己")
    _add_edition(bundle, "TEST0005", "W", "庚辛壬癸子丑")
    settings = punctuation.LlmSettings(
        model="test-model",
        ai_config=tmp_path / "missing.xml",
        prompt=tmp_path / "prompt",
    )
    (tmp_path / "prompt").write_text("prompt", encoding="utf-8")

    rc = punctuation.run_direct(
        bundle,
        None,
        text_id=None,
        text_prefix=None,
        selected_juans=None,
        settings=settings,
        dry_run=True,
        include_editions=True,
    )

    assert rc == 0
    out = capsys.readouterr().out
    assert "[master]" in out
    assert "[edition W]" in out
    assert "would submit 2 chunk request(s)" in out


def test_build_tasks_have_unique_custom_ids_across_same_stream_regions(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path,
        "TEST0002",
        "甲乙丙丁戊己庚辛",
        markers=[
            {"type": "voice", "offset": 2, "length": 1, "name": "head", "id": "h1"},
            {"type": "voice", "offset": 5, "length": 1, "name": "head", "id": "h2"},
        ],
    )
    settings = punctuation.LlmSettings(
        model="test-model",
        ai_config=tmp_path / "missing.xml",
        prompt=tmp_path / "prompt",
        chunk_chars=10,
        overlap=0,
    )

    tasks = punctuation.build_tasks_for_scope(
        bundle,
        bundle / "TEST0002.manifest.yaml",
        "TEST0002",
        None,
        settings=settings,
        prompt_text="prompt",
        selected_juans=None,
    )

    custom_ids = [task.custom_id for task in tasks]
    assert len(custom_ids) == len(set(custom_ids))


def test_build_tasks_skip_streams_with_five_or_fewer_characters(tmp_path: Path) -> None:
    bundle = _write_bundle(
        tmp_path,
        "TEST0003",
        "甲乙丙丁戊己",
        markers=[
            {"type": "voice", "offset": 0, "length": 5, "name": "note", "id": "n1"},
        ],
    )
    settings = punctuation.LlmSettings(
        model="test-model",
        ai_config=tmp_path / "missing.xml",
        prompt=tmp_path / "prompt",
        chunk_chars=10,
        overlap=0,
    )

    tasks = punctuation.build_tasks_for_scope(
        bundle,
        bundle / "TEST0003.manifest.yaml",
        "TEST0003",
        None,
        settings=settings,
        prompt_text="prompt",
        selected_juans=None,
    )

    assert tasks == []


def test_inspect_batch_writes_status_and_error_file(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    state_path = tmp_path / "punctuation.batch.yaml"
    state_path.write_text(
        yaml.safe_dump({
            "schema": 1,
            "task": "punctuation",
            "model": "test-model",
            "prompt_path": str(tmp_path / "prompt"),
            "chunk_chars": 3000,
            "overlap": 50,
            "batch": {"id": "batch_1"},
            "tasks": [],
        }),
        encoding="utf-8",
    )
    (tmp_path / "prompt").write_text("prompt", encoding="utf-8")

    class FakeClient:
        def retrieve_batch(self, batch_id: str) -> dict:
            assert batch_id == "batch_1"
            return {
                "id": "batch_1",
                "status": "failed",
                "error_file_id": "file_error",
                "request_counts": {"completed": 0, "failed": 1, "total": 1},
                "errors": {"data": [{"message": "duplicate custom_id"}]},
            }

        def download_file_text(self, file_id: str) -> str:
            assert file_id == "file_error"
            return '{"custom_id":"x","error":{"message":"duplicate custom_id"}}\n'

    monkeypatch.setattr(
        punctuation, "make_openai_client", lambda ai_config: FakeClient(),
    )

    rc = llm_run([
        "punctuation",
        "inspect",
        str(state_path),
        "--ai-config",
        str(tmp_path / "missing.xml"),
    ])

    assert rc == 0
    out = capsys.readouterr().out
    assert "status: failed" in out
    assert "batch-error.jsonl" in out
    assert state_path.with_suffix(".batch-status.yaml").exists()
    assert state_path.with_suffix(".batch-error.jsonl").read_text(
        encoding="utf-8",
    ).startswith('{"custom_id"')
