from __future__ import annotations

import json
import sys
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


def _task_dict(
    custom_id: str,
    text_id: str,
    input_text: str,
    *,
    core_end: int | None = None,
) -> dict:
    end = len(input_text) if core_end is None else core_end
    return {
        "custom_id": custom_id,
        "text_id": text_id,
        "edition": None,
        "juan_dir": "",
        "manifest_path": "",
        "seq": 1,
        "bucket": "body",
        "stream": "main",
        "core_start": 0,
        "core_end": end,
        "context_start": 0,
        "context_end": len(input_text),
        "stream_end": len(input_text),
        "input_text": input_text,
    }


def test_punctuated_output_becomes_markers_and_records_variants() -> None:
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

    assert punctuation.markers_from_punctuated_output(
        "裏", "里", context_start=0, core_start=0, core_end=1,
    ) == [
        {
            "type": "variant",
            "offset": 0,
            "length": 1,
            "content": "裏",
            "replacement": "里",
        }
    ]

    assert punctuation.markers_from_punctuated_output(
        "甲乙丙丁", "AB丙丁", context_start=0, core_start=0, core_end=4,
    ) == [
        {
            "type": "variant",
            "offset": 0,
            "length": 1,
            "content": "甲",
            "replacement": "A",
        },
        {
            "type": "variant",
            "offset": 1,
            "length": 1,
            "content": "乙",
            "replacement": "B",
        },
    ]

    try:
        punctuation.markers_from_punctuated_output(
            "甲乙丙丁", "ABC丁", context_start=0, core_start=0, core_end=4,
        )
    except ValueError as exc:
        assert "more than 2 adjacent" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("long divergent run was accepted")

    try:
        punctuation.markers_from_punctuated_output(
            "甲乙", "甲X乙", context_start=0, core_start=0, core_end=2,
        )
    except ValueError as exc:
        assert "unexpected character" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("extra output character was accepted")

    assert punctuation.markers_from_punctuated_output(
        "甲乙丙丁",
        "甲，乙丙丁戊",
        context_start=0,
        core_start=0,
        core_end=3,
        include_core_end=False,
    ) == [
        {"type": "punctuation", "offset": 1, "content": "，"},
    ]

    assert punctuation.markers_from_punctuated_output(
        "甲乙丙丁",
        "甲，乙丙",
        context_start=0,
        core_start=0,
        core_end=3,
        include_core_end=False,
    ) == [
        {"type": "punctuation", "offset": 1, "content": "，"},
    ]

    try:
        punctuation.markers_from_punctuated_output(
            "甲乙丙丁",
            "甲乙",
            context_start=0,
            core_start=0,
            core_end=3,
            include_core_end=False,
        )
    except ValueError as exc:
        assert "output omitted" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("omitted core output was accepted")

    assert punctuation.markers_from_punctuated_output(
        "甲乙丙丁",
        "X甲乙，丙丁",
        context_start=0,
        core_start=1,
        core_end=3,
        include_core_end=False,
    ) == [
        {"type": "punctuation", "offset": 2, "content": "，"},
    ]


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

    chunks = punctuation.chunk_region(region, [0, 4, 10], chunk_chars=3, overlap=2)

    assert [(c.core_start, c.core_end, c.context_start, c.context_end, c.input_text) for c in chunks] == [
        (0, 3, 0, 4, "甲乙丙丁"),
        (3, 4, 2, 5, "丙丁戊"),
        (4, 7, 3, 8, "丁戊己庚辛"),
        (7, 10, 6, 10, "庚辛壬癸"),
    ]


def test_direct_cli_writes_reference_asset_without_canonical_marker_changes(
    tmp_path: Path, monkeypatch,
) -> None:
    bundle = _write_bundle(tmp_path, "TEST0001", "裏乙丙丁戊己")

    class FakeClient:
        def create_response(self, *, model: str, prompt: str, text: str) -> str:
            assert model == "test-model"
            assert text == "裏乙丙丁戊己"
            return "里，乙。丙丁戊己"

    seen: dict[str, str] = {}

    def fake_client(ai_config: Path, vendor: str = "openai") -> FakeClient:
        seen["vendor"] = vendor
        return FakeClient()

    monkeypatch.setattr(
        punctuation, "make_openai_client", fake_client,
    )

    rc = llm_run([
        "punctuation",
        "run",
        "--bundle",
        str(bundle),
        "--model",
        "test-model",
        "--vendor",
        "mistral",
        "--ai-config",
        str(tmp_path / "missing.xml"),
    ])

    assert rc == 0
    assert seen["vendor"] == "mistral"
    juan = yaml.safe_load((bundle / "TEST0001_001.yaml").read_text(encoding="utf-8"))
    assert "markers" not in juan["body"] or juan["body"]["markers"] == []

    manifest = yaml.safe_load(
        (bundle / "TEST0001.manifest.yaml").read_text(encoding="utf-8")
    )
    ref = manifest["assets"]["references"][0]
    assert ref["role"] == "llm-punctuation"
    assert ref["filename"] == "assets/TEST0001_001.test-model.punctuation.yaml"

    asset = yaml.safe_load((bundle / ref["filename"]).read_text(encoding="utf-8"))
    assert asset["provenance"]["provider"] == "mistral"
    assert asset["markers"]["body"] == [
        {
            "type": "variant",
            "offset": 0,
            "length": 1,
            "content": "裏",
            "replacement": "里",
            "source": "test-model",
            "model": "test-model",
            "id": "TEST0001_bkk_001-bkkvar1",
        },
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


def test_direct_run_writes_external_punctuation_root_by_section(
    tmp_path: Path, monkeypatch,
) -> None:
    corpus = tmp_path / "corpus"
    (corpus / "KR1a").mkdir(parents=True)
    bundle = _write_bundle(corpus / "KR1a", "KR1a0001", "甲乙丙丁戊己")
    punctuation_root = tmp_path / "punctuation"

    class FakeClient:
        def create_response(self, *, model: str, prompt: str, text: str) -> str:
            return "甲，乙丙丁戊己"

    monkeypatch.setattr(
        punctuation,
        "make_openai_client",
        lambda ai_config, vendor="openai": FakeClient(),
    )

    settings = punctuation.LlmSettings(
        model="test-model",
        ai_config=tmp_path / "missing.xml",
        prompt=tmp_path / "prompt",
        punctuation_root=punctuation_root,
    )
    (tmp_path / "prompt").write_text("prompt", encoding="utf-8")

    rc = punctuation.run_direct(
        bundle,
        corpus,
        text_id=None,
        text_prefix=None,
        selected_juans=None,
        settings=settings,
        dry_run=False,
    )

    assert rc == 0
    manifest = yaml.safe_load(
        (bundle / "KR1a0001.manifest.yaml").read_text(encoding="utf-8")
    )
    assert "references" not in manifest["assets"]
    filename = "KR1a0001_001.test-model.punctuation.yaml"
    asset_path = punctuation_root / "KR1a" / "KR1a0001" / filename
    assert asset_path.is_file()
    assert not (bundle / "assets").exists()


def test_models_cli_lists_available_models(monkeypatch, capsys) -> None:
    seen: dict[str, str] = {}

    class FakeClient:
        def list_models(self) -> list[dict]:
            return [
                {"id": "mistral-large-latest", "owned_by": "mistral"},
                {"id": "open-mistral-nemo", "owned_by": "mistral"},
            ]

    def fake_client(ai_config: Path, vendor: str = "openai") -> FakeClient:
        seen["ai_config"] = str(ai_config)
        seen["vendor"] = vendor
        return FakeClient()

    monkeypatch.setattr(
        punctuation,
        "make_openai_client",
        fake_client,
    )

    rc = llm_run([
        "models",
        "--ai-config",
        "/tmp/missing.xml",
        "--vendor",
        "mistral",
        "--contains",
        "large",
    ])

    assert rc == 0
    assert seen == {"ai_config": "/tmp/missing.xml", "vendor": "mistral"}
    assert capsys.readouterr().out == "mistral-large-latest\n"


def test_vendors_cli_lists_api_token_parent_names(tmp_path: Path, capsys) -> None:
    path = tmp_path / "ai-config.xml"
    path.write_text(
        "<engines>"
        "<sakana-ai><api-token>sakana-secret</api-token></sakana-ai>"
        "<mistral-config><api-token>mistral-secret</api-token></mistral-config>"
        "<openai-config><api-token>openai-secret</api-token></openai-config>"
        "<mistral-alt><api-token>second-secret</api-token></mistral-alt>"
        "</engines>",
        encoding="utf-8",
    )

    rc = llm_run(["vendors", "--ai-config", str(path)])

    assert rc == 0
    assert capsys.readouterr().out == "sakana\nmistral\nopenai\n"


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


def test_ai_config_selects_vendor_by_api_token_parent_name(tmp_path: Path) -> None:
    path = tmp_path / "ai-config.xml"
    path.write_text(
        "<engines>"
        "<openai-config><api-token>openai-secret</api-token>"
        "<model>gpt-test</model></openai-config>"
        "<mistral-config><api-token>mistral-secret</api-token>"
        "<model>mistral-test</model><base-url>https://example.test/v1</base-url>"
        "</mistral-config>"
        "</engines>",
        encoding="utf-8",
    )

    assert punctuation.load_ai_config(path, vendor="mistral") == {
        "api_key": "mistral-secret",
        "base_url": "https://example.test/v1",
        "model": "mistral-test",
    }


def test_client_config_adds_known_vendor_base_url_defaults(tmp_path: Path) -> None:
    path = tmp_path / "ai-config.xml"
    path.write_text(
        "<engines>"
        "<mistral-config><api-token>mistral-secret</api-token>"
        "<model>mistral-test</model></mistral-config>"
        "<deepseek-config><api-token>deepseek-secret</api-token>"
        "<model>deepseek-test</model></deepseek-config>"
        "<sakana-config><api-token>sakana-secret</api-token>"
        "<model>fugu-ultra</model></sakana-config>"
        "<or-config><api-token>or-secret</api-token>"
        "<model>openai/gpt-4o</model></or-config>"
        "</engines>",
        encoding="utf-8",
    )

    assert punctuation._client_config_for_vendor(path, "mistral")["base_url"] == (
        "https://api.mistral.ai/v1"
    )
    assert punctuation._client_config_for_vendor(path, "deepseek")["base_url"] == (
        "https://api.deepseek.com"
    )
    assert punctuation._client_config_for_vendor(path, "sakana")["base_url"] == (
        "https://api.sakana.ai/v1"
    )
    assert punctuation._client_config_for_vendor(path, "or")["base_url"] == (
        "https://openrouter.ai/api/v1"
    )


def test_make_client_uses_openrouter_batch_client_for_or_vendor(
    tmp_path: Path, monkeypatch,
) -> None:
    path = tmp_path / "ai-config.xml"
    path.write_text(
        "<engines><or-config><api-token>or-secret</api-token>"
        "<model>openai/gpt-4o</model></or-config></engines>",
        encoding="utf-8",
    )
    seen: dict[str, str] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs: str) -> None:
            seen.update(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "openai",
        type("OpenAiModule", (), {"OpenAI": FakeOpenAI}),
    )

    client = punctuation.make_openai_client(path, vendor="or")

    assert isinstance(client, punctuation.OpenRouterResponsesClient)
    assert seen["api_key"] == "or-secret"
    assert seen["base_url"] == "https://openrouter.ai/api/v1"


def test_openrouter_batch_payload_converts_jsonl_requests(tmp_path: Path) -> None:
    requests_path = tmp_path / "requests.jsonl"
    requests_path.write_text(
        json.dumps({
            "custom_id": "req-1",
            "method": "POST",
            "url": "/v1/responses",
            "body": {
                "model": "openai/gpt-4o",
                "input": [{"role": "user", "content": "甲乙"}],
            },
        }) + "\n",
        encoding="utf-8",
    )

    assert punctuation._openrouter_batch_payload(requests_path) == {
        "endpoint": "/v1/responses",
        "model": "openai/gpt-4o",
        "requests": [
            {
                "custom_id": "req-1",
                "body": {
                    "input": [{"role": "user", "content": "甲乙"}],
                },
            }
        ],
    }


def test_client_config_preserves_explicit_vendor_base_url(tmp_path: Path) -> None:
    path = tmp_path / "ai-config.xml"
    path.write_text(
        "<engines><mistral-config><api-token>mistral-secret</api-token>"
        "<base-url>https://proxy.example/v1</base-url>"
        "</mistral-config></engines>",
        encoding="utf-8",
    )

    assert punctuation._client_config_for_vendor(path, "mistral")["base_url"] == (
        "https://proxy.example/v1"
    )


def test_client_config_rejects_unknown_vendor_without_base_url(tmp_path: Path) -> None:
    path = tmp_path / "ai-config.xml"
    path.write_text(
        "<engines><custom-config><api-token>custom-secret</api-token>"
        "<model>custom-model</model></custom-config></engines>",
        encoding="utf-8",
    )

    try:
        punctuation._client_config_for_vendor(path, "custom")
    except ValueError as exc:
        assert "requires a base_url/base-url" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unknown vendor defaulted to OpenAI")


def test_settings_default_min_chars_and_rc_override(tmp_path: Path) -> None:
    settings = punctuation.settings_from_rc(
        {"llm": {"model": "test-model", "min_chars": 9}},
        ai_config=tmp_path / "missing.xml",
        prompt=tmp_path / "prompt",
    )

    assert settings.min_chars == 9


def test_settings_accepts_vendor_override(tmp_path: Path) -> None:
    path = tmp_path / "ai-config.xml"
    path.write_text(
        "<engines><deepseek-config><api-token>secret</api-token>"
        "<model>deepseek-test</model></deepseek-config></engines>",
        encoding="utf-8",
    )

    settings = punctuation.settings_from_rc(
        {},
        vendor="deepseek",
        ai_config=path,
        prompt=tmp_path / "prompt",
    )

    assert settings.vendor == "deepseek"
    assert settings.model == "deepseek-test"


def test_submit_batch_state_records_vendor(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    bundle = _write_bundle(tmp_path, "TEST0008", "甲乙丙丁戊己")
    (tmp_path / "prompt").write_text("prompt", encoding="utf-8")
    seen: dict[str, str] = {}

    class FakeClient:
        def submit_batch(self, *, requests_path: Path, metadata: dict | None = None) -> dict:
            assert requests_path.exists()
            return {"id": "batch_1", "status": "validating"}

    def fake_client(ai_config: Path, vendor: str = "openai") -> FakeClient:
        seen["vendor"] = vendor
        return FakeClient()

    monkeypatch.setattr(punctuation, "make_openai_client", fake_client)

    rc = llm_run([
        "punctuation",
        "submit",
        "--bundle",
        str(bundle),
        "--model",
        "test-model",
        "--vendor",
        "deepseek",
        "--prompt",
        str(tmp_path / "prompt"),
        "--ai-config",
        str(tmp_path / "missing.xml"),
    ])

    assert rc == 0
    assert "submitted batch" in capsys.readouterr().out
    assert seen["vendor"] == "deepseek"
    states = list((bundle / ".bkk-llm").glob("*.batch.yaml"))
    assert len(states) == 1
    state = yaml.safe_load(states[0].read_text(encoding="utf-8"))
    assert state["vendor"] == "deepseek"


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


def test_punctuate_run_cli_defaults_to_master_only(tmp_path: Path, capsys) -> None:
    bundle = _write_bundle(tmp_path, "TEST0004", "甲乙丙丁戊己")
    _add_edition(bundle, "TEST0004", "W", "庚辛壬癸子丑")
    (tmp_path / "prompt").write_text("prompt", encoding="utf-8")

    rc = llm_run([
        "punctuate",
        "run",
        "--bundle",
        str(bundle),
        "--model",
        "test-model",
        "--prompt",
        str(tmp_path / "prompt"),
        "--ai-config",
        str(tmp_path / "missing.xml"),
        "--dry-run",
    ])

    assert rc == 0
    out = capsys.readouterr().out
    assert "[master]" in out
    assert "edition W" not in out
    assert "would submit 1 chunk request(s)" in out


def test_punctuate_run_writes_clear_text_report_for_rejected_chunk(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    bundle = _write_bundle(tmp_path, "TEST0009", "甲乙丙丁戊己")
    (tmp_path / "prompt").write_text("prompt", encoding="utf-8")

    class FakeClient:
        def create_response(self, *, model: str, prompt: str, text: str) -> str:
            return "甲乙丙丁戊"

    monkeypatch.setattr(
        punctuation,
        "make_openai_client",
        lambda ai_config, vendor="openai": FakeClient(),
    )

    rc = llm_run([
        "punctuate",
        "run",
        "--bundle",
        str(bundle),
        "--model",
        "test-model",
        "--prompt",
        str(tmp_path / "prompt"),
        "--ai-config",
        str(tmp_path / "missing.xml"),
    ])

    assert rc == 1
    assert "clear-text report" in capsys.readouterr().out
    reports = list((bundle / ".bkk-llm").glob("*.direct-report.yaml"))
    assert len(reports) == 1
    report = yaml.safe_load(reports[0].read_text(encoding="utf-8"))
    assert report["task"] == "punctuation-direct-report"
    assert report["best_effort"] is False
    chunk = report["chunks"][0]
    assert chunk["status"] == "rejected"
    assert chunk["input_text"] == "甲乙丙丁戊己"
    assert chunk["output_text"] == "甲乙丙丁戊"
    assert chunk["issues"][0]["code"] == "invalid-output"
    assert chunk["issues"][0]["message"] == "output omitted 1 original character(s)"


def test_punctuate_run_best_effort_writes_error_marker(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    bundle = _write_bundle(tmp_path, "TEST0010", "甲乙丙丁戊己")
    (tmp_path / "prompt").write_text("prompt", encoding="utf-8")

    class FakeClient:
        def create_response(self, *, model: str, prompt: str, text: str) -> str:
            return "甲乙丙丁戊"

    monkeypatch.setattr(
        punctuation,
        "make_openai_client",
        lambda ai_config, vendor="openai": FakeClient(),
    )

    rc = llm_run([
        "punctuate",
        "run",
        "--bundle",
        str(bundle),
        "--model",
        "test-model",
        "--prompt",
        str(tmp_path / "prompt"),
        "--ai-config",
        str(tmp_path / "missing.xml"),
        "--best-effort",
    ])

    assert rc == 0
    assert "clear-text report" in capsys.readouterr().out
    report = yaml.safe_load(
        next((bundle / ".bkk-llm").glob("*.direct-report.yaml")).read_text(
            encoding="utf-8",
        )
    )
    assert report["best_effort"] is True
    manifest = yaml.safe_load(
        (bundle / "TEST0010.manifest.yaml").read_text(encoding="utf-8")
    )
    ref = manifest["assets"]["references"][0]
    asset = yaml.safe_load((bundle / ref["filename"]).read_text(encoding="utf-8"))
    error_marker = next(
        marker for marker in asset["markers"]["body"]
        if marker["type"] == "llm-error"
    )
    assert error_marker["offset"] == 0
    assert error_marker["length"] == 6
    assert error_marker["issue"] == {
        "code": "invalid-output",
        "message": "output omitted 1 original character(s)",
    }
    assert error_marker["model"] == "test-model"


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
        punctuation,
        "make_openai_client",
        lambda ai_config, vendor="openai": FakeClient(),
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


def test_collect_batch_writes_clear_text_report_for_rejected_chunk(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    bundle = _write_bundle(tmp_path, "TEST0004", "甲乙")
    state_path = tmp_path / "punctuation.batch.yaml"
    task = _task_dict("TEST0004:bkk:001:body:main:1", "TEST0004", "甲乙")
    task["juan_dir"] = str(bundle)
    task["manifest_path"] = str(bundle / "TEST0004.manifest.yaml")
    state_path.write_text(
        yaml.safe_dump({
            "schema": 1,
            "task": "punctuation",
            "model": "test-model",
            "prompt_path": str(tmp_path / "prompt"),
            "chunk_chars": 3000,
            "overlap": 50,
            "batch": {"id": "batch_1"},
            "tasks": [task],
        }),
        encoding="utf-8",
    )
    (tmp_path / "prompt").write_text("prompt", encoding="utf-8")

    class FakeClient:
        def retrieve_batch(self, batch_id: str) -> dict:
            assert batch_id == "batch_1"
            return {
                "id": "batch_1",
                "status": "completed",
                "output_file_id": "file_output",
                "request_counts": {"completed": 1, "failed": 0, "total": 1},
            }

        def download_file_text(self, file_id: str) -> str:
            assert file_id == "file_output"
            return (
                '{"custom_id":"TEST0004:bkk:001:body:main:1",'
                '"response":{"status_code":200,'
                '"body":{"output_text":"甲"}}}\n'
            )

    monkeypatch.setattr(
        punctuation,
        "make_openai_client",
        lambda ai_config, vendor="openai": FakeClient(),
    )

    rc = llm_run([
        "punctuation",
        "collect",
        str(state_path),
        "--ai-config",
        str(tmp_path / "missing.xml"),
    ])

    assert rc == 1
    assert "clear-text report" in capsys.readouterr().out
    report = yaml.safe_load(
        state_path.with_suffix(".batch-report.yaml").read_text(encoding="utf-8")
    )
    chunk = report["chunks"][0]
    assert chunk["input_text"] == "甲乙"
    assert chunk["output_text"] == "甲"
    assert chunk["status"] == "rejected"
    assert chunk["issues"][0]["code"] == "invalid-output"
    assert chunk["issues"][0]["message"] == "output omitted 1 original character(s)"
    details = chunk["issues"][0]["details"]
    assert details["input_context"] == {
        "index": 1,
        "before": "甲",
        "at": "乙",
        "after": "",
        "absolute_offset": 1,
    }
    assert details["output_context"] == {
        "index": 1,
        "before": "甲",
        "at": "",
        "after": "",
    }


def test_collect_batch_accepts_openrouter_inline_results(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    bundle = _write_bundle(tmp_path, "TEST0011", "甲乙丙丁戊己")
    state_path = tmp_path / "punctuation.batch.yaml"
    task = _task_dict(
        "TEST0011:bkk:001:body:main:1", "TEST0011", "甲乙丙丁戊己",
    )
    task["juan_dir"] = str(bundle)
    task["manifest_path"] = str(bundle / "TEST0011.manifest.yaml")
    state_path.write_text(
        yaml.safe_dump({
            "schema": 1,
            "task": "punctuation",
            "vendor": "or",
            "model": "openai/gpt-4o",
            "prompt_path": str(tmp_path / "prompt"),
            "chunk_chars": 3000,
            "overlap": 50,
            "batch": {"id": "batch_or"},
            "tasks": [task],
        }),
        encoding="utf-8",
    )
    (tmp_path / "prompt").write_text("prompt", encoding="utf-8")

    class FakeClient:
        def retrieve_batch(self, batch_id: str) -> dict:
            assert batch_id == "batch_or"
            return {
                "id": "batch_or",
                "status": "completed",
                "request_counts": {"completed": 1, "failed": 0, "total": 1},
                "results": [
                    {
                        "custom_id": "TEST0011:bkk:001:body:main:1",
                        "response": {
                            "status_code": 200,
                            "body": {"output_text": "甲，乙丙丁戊己"},
                        },
                        "error": None,
                    }
                ],
            }

        def download_file_text(self, file_id: str) -> str:  # pragma: no cover
            raise AssertionError("OpenRouter inline results should not download files")

    monkeypatch.setattr(
        punctuation,
        "make_openai_client",
        lambda ai_config, vendor="openai": FakeClient(),
    )

    rc = llm_run([
        "punctuation",
        "collect",
        str(state_path),
        "--ai-config",
        str(tmp_path / "missing.xml"),
    ])

    assert rc == 0
    assert "collected batch batch_or" in capsys.readouterr().out
    assert state_path.with_suffix(".batch-output.jsonl").exists()
    manifest = yaml.safe_load(
        (bundle / "TEST0011.manifest.yaml").read_text(encoding="utf-8")
    )
    ref = manifest["assets"]["references"][0]
    asset = yaml.safe_load((bundle / ref["filename"]).read_text(encoding="utf-8"))
    assert asset["markers"]["body"][0]["content"] == "，"


def test_retry_failed_batch_submits_only_rejected_chunks(
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
            "tasks": [
                _task_dict("ok", "TEST0005", "甲乙"),
                _task_dict("bad", "TEST0005", "丙丁"),
            ],
        }),
        encoding="utf-8",
    )
    (tmp_path / "prompt").write_text("prompt", encoding="utf-8")
    state_path.with_suffix(".batch-output.jsonl").write_text(
        "\n".join([
            '{"custom_id":"ok","response":{"status_code":200,'
            '"body":{"output_text":"甲，乙"}}}',
            '{"custom_id":"bad","response":{"status_code":200,'
            '"body":{"output_text":"丙X丁"}}}',
        ]) + "\n",
        encoding="utf-8",
    )
    submitted: dict[str, str] = {}

    class FakeClient:
        def retrieve_batch(self, batch_id: str) -> dict:
            assert batch_id == "batch_1"
            return {"id": "batch_1", "status": "completed"}

        def submit_batch(self, *, requests_path: Path, metadata: dict | None = None) -> dict:
            submitted["body"] = requests_path.read_text(encoding="utf-8")
            submitted["metadata_task"] = (metadata or {}).get("bkk_task", "")
            return {"id": "batch_retry", "status": "validating"}

    monkeypatch.setattr(
        punctuation,
        "make_openai_client",
        lambda ai_config, vendor="openai": FakeClient(),
    )

    rc = llm_run([
        "punctuation",
        "retry",
        str(state_path),
        "--ai-config",
        str(tmp_path / "missing.xml"),
    ])

    assert rc == 0
    assert "submitted retry batch" in capsys.readouterr().out
    assert submitted["metadata_task"] == "punctuation-retry"
    assert '"custom_id":"bad"' in submitted["body"]
    assert '"custom_id":"ok"' not in submitted["body"]
    retry_states = sorted(tmp_path.glob("punctuation.batch.retry-*.batch.yaml"))
    assert len(retry_states) == 1
    retry_state = yaml.safe_load(retry_states[0].read_text(encoding="utf-8"))
    assert retry_state["retry_custom_ids"] == ["bad"]
    assert retry_state["previous_output_files"] == [
        str(state_path.with_suffix(".batch-output.jsonl"))
    ]


def test_punctuate_batch_workflow_retries_rejected_chunks(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    bundle = _write_bundle(tmp_path, "TEST0006", "甲乙丙丁戊己")
    (tmp_path / "prompt").write_text("prompt", encoding="utf-8")
    submitted: list[str] = []

    class FakeClient:
        def submit_batch(self, *, requests_path: Path, metadata: dict | None = None) -> dict:
            submitted.append(requests_path.read_text(encoding="utf-8"))
            return {"id": f"batch_{len(submitted)}", "status": "validating"}

        def retrieve_batch(self, batch_id: str) -> dict:
            return {
                "id": batch_id,
                "status": "completed",
                "output_file_id": f"file_{batch_id}",
                "request_counts": {"completed": 1, "failed": 0, "total": 1},
            }

        def download_file_text(self, file_id: str) -> str:
            output = "甲X乙丙丁戊己" if file_id == "file_batch_1" else "甲，乙丙丁戊己"
            return json.dumps({
                "custom_id": "TEST0006:bkk:001:body:main:1",
                "response": {
                    "status_code": 200,
                    "body": {"output_text": output},
                },
            }, ensure_ascii=False) + "\n"

    monkeypatch.setattr(
        punctuation,
        "make_openai_client",
        lambda ai_config, vendor="openai": FakeClient(),
    )

    rc = llm_run([
        "punctuate",
        "batch",
        "--bundle",
        str(bundle),
        "--model",
        "test-model",
        "--prompt",
        str(tmp_path / "prompt"),
        "--ai-config",
        str(tmp_path / "missing.xml"),
        "--poll-seconds",
        "0",
    ])

    assert rc == 0
    assert len(submitted) == 2
    assert "retry 1/1" in capsys.readouterr().out
    assert not list((bundle / ".bkk-llm").glob("*.workflow-report.yaml"))
    manifest = yaml.safe_load(
        (bundle / "TEST0006.manifest.yaml").read_text(encoding="utf-8")
    )
    ref = manifest["assets"]["references"][0]
    asset = yaml.safe_load((bundle / ref["filename"]).read_text(encoding="utf-8"))
    assert asset["status"] == "complete"
    assert asset["markers"]["body"][0]["content"] == "，"


def test_punctuation_batch_workflow_reports_remaining_problems(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    bundle = _write_bundle(tmp_path, "TEST0007", "甲乙丙丁戊己")
    (tmp_path / "prompt").write_text("prompt", encoding="utf-8")

    class FakeClient:
        def submit_batch(self, *, requests_path: Path, metadata: dict | None = None) -> dict:
            return {"id": "batch_bad", "status": "validating"}

        def retrieve_batch(self, batch_id: str) -> dict:
            return {
                "id": batch_id,
                "status": "completed",
                "output_file_id": "file_bad",
                "request_counts": {"completed": 1, "failed": 0, "total": 1},
            }

        def download_file_text(self, file_id: str) -> str:
            return json.dumps({
                "custom_id": "TEST0007:bkk:001:body:main:1",
                "response": {
                    "status_code": 200,
                    "body": {"output_text": "甲X乙丙丁戊己"},
                },
            }, ensure_ascii=False) + "\n"

    monkeypatch.setattr(
        punctuation,
        "make_openai_client",
        lambda ai_config, vendor="openai": FakeClient(),
    )

    rc = llm_run([
        "punctuation",
        "batch",
        "--bundle",
        str(bundle),
        "--model",
        "test-model",
        "--prompt",
        str(tmp_path / "prompt"),
        "--ai-config",
        str(tmp_path / "missing.xml"),
        "--poll-seconds",
        "0",
        "--retries",
        "0",
        "--jobs",
        "2",
    ])

    assert rc == 1
    assert "workflow complete: 0 succeeded, 1 failed" in capsys.readouterr().out
    reports = list((bundle / ".bkk-llm").glob("*.workflow-report.yaml"))
    assert len(reports) == 1
    report = yaml.safe_load(reports[0].read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["problems"][0]["id"] == "TEST0007:bkk:001:body:main:1"
    assert report["problems"][0]["status"] == "rejected"


def test_punctuate_batch_best_effort_retries_then_writes_partial_markers(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    bundle = _write_bundle(tmp_path, "TEST0008", "甲乙丙丁戊己")
    (tmp_path / "prompt").write_text("prompt", encoding="utf-8")
    submitted: list[str] = []

    class FakeClient:
        def submit_batch(self, *, requests_path: Path, metadata: dict | None = None) -> dict:
            submitted.append(requests_path.read_text(encoding="utf-8"))
            return {"id": f"batch_{len(submitted)}", "status": "validating"}

        def retrieve_batch(self, batch_id: str) -> dict:
            return {
                "id": batch_id,
                "status": "completed",
                "output_file_id": f"file_{batch_id}",
                "request_counts": {"completed": 1, "failed": 0, "total": 1},
            }

        def download_file_text(self, file_id: str) -> str:
            return json.dumps({
                "custom_id": "TEST0008:bkk:001:body:main:1",
                "response": {
                    "status_code": 200,
                    "body": {"output_text": "甲，乙丙丁戊"},
                },
            }, ensure_ascii=False) + "\n"

    monkeypatch.setattr(
        punctuation,
        "make_openai_client",
        lambda ai_config, vendor="openai": FakeClient(),
    )

    rc = llm_run([
        "punctuate",
        "batch",
        "--bundle",
        str(bundle),
        "--model",
        "test-model",
        "--prompt",
        str(tmp_path / "prompt"),
        "--ai-config",
        str(tmp_path / "missing.xml"),
        "--poll-seconds",
        "0",
        "--retries",
        "1",
        "--best-effort",
    ])

    assert rc == 0
    assert len(submitted) == 2
    out = capsys.readouterr().out
    assert "retry 1/1" in out
    assert "best-effort collection after final retry" in out
    assert not list((bundle / ".bkk-llm").glob("*.workflow-report.yaml"))

    manifest = yaml.safe_load(
        (bundle / "TEST0008.manifest.yaml").read_text(encoding="utf-8")
    )
    ref = manifest["assets"]["references"][0]
    asset = yaml.safe_load((bundle / ref["filename"]).read_text(encoding="utf-8"))
    assert asset["status"] == "partial"
    assert asset["chunks"][0]["status"] == "rejected"
    body_markers = asset["markers"]["body"]
    punctuation_marker = next(
        marker for marker in body_markers if marker["type"] == "punctuation"
    )
    assert punctuation_marker["offset"] == 1
    assert punctuation_marker["content"] == "，"
    error_marker = next(
        marker for marker in body_markers if marker["type"] == "llm-error"
    )
    assert error_marker["offset"] == 0
    assert error_marker["length"] == 6
    assert error_marker["issue"] == {
        "code": "invalid-output",
        "message": "output omitted 1 original character(s)",
    }
    assert error_marker["model"] == "test-model"
