"""Generate LLM punctuation sidecar assets for BKK bundles."""

from __future__ import annotations

import copy
import concurrent.futures
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol
from xml.etree import ElementTree

import yaml

from bkk.cli_common import resolve_bundle_dir
from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.idassigner import allocate_marker_ids
from bkk.importer.write.yaml_writer import dump, marker_to_flow, reflow_manifest
from bkk.index.merge import discover_bundles
from bkk.marker_assets import (
    VALID_BUCKETS,
    effective_markers_for_bucket,
    load_marker_asset,
)

BUCKETS = ("front", "body", "back")
DEFAULT_CHUNK_CHARS = 3000
DEFAULT_OVERLAP = 50
DEFAULT_MIN_CHARS = 6
DEFAULT_AI_CONFIG = Path("~/ai-config.xml")
DEFAULT_VENDOR = "openai"
REFERENCE_ROLE = "llm-punctuation"
STATE_SCHEMA_VERSION = 1
ASSET_SCHEMA_VERSION = 1
DEFAULT_BATCH_POLL_SECONDS = 300
DEFAULT_BATCH_RETRIES = 1
DEFAULT_BATCH_JOBS = 1
MAX_ADJACENT_VARIANT_CHARS = 2
DEFAULT_VENDOR_BASE_URLS = {
    "mistral": "https://api.mistral.ai/v1",
    "deepseek": "https://api.deepseek.com",
    "sakana": "https://api.sakana.ai/v1",
}
_BATCH_TERMINAL_STATUSES = {
    "completed", "failed", "expired", "cancelled", "canceled",
}

_JUAN_RE = re.compile(
    r"^(?P<text_id>.+?)_(?P<seq>\d{3})(?:-(?P<short>[A-Za-z0-9][A-Za-z0-9_-]*))?\.yaml$",
)
_MODEL_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

ALLOWED_PUNCTUATION = frozenset(
    "，。、；？！：：「」『』《》〈〉（）()［］[]【】“”‘’、,.!?;:"
)


class LlmClient(Protocol):
    def list_models(self) -> list[dict[str, Any]]:
        ...

    def create_response(self, *, model: str, prompt: str, text: str) -> str:
        ...

    def submit_batch(
        self, *, requests_path: Path, metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        ...

    def retrieve_batch(self, batch_id: str) -> dict[str, Any]:
        ...

    def download_file_text(self, file_id: str) -> str:
        ...


@dataclass(frozen=True)
class LlmSettings:
    model: str
    ai_config: Path
    prompt: Path
    vendor: str = DEFAULT_VENDOR
    chunk_chars: int = DEFAULT_CHUNK_CHARS
    overlap: int = DEFAULT_OVERLAP
    min_chars: int = DEFAULT_MIN_CHARS
    cache_dir: Path | None = None


@dataclass(frozen=True)
class VoiceSpan:
    start: int
    end: int
    name: str
    marker_id: str


@dataclass(frozen=True)
class StreamRegion:
    text_id: str
    edition: str | None
    seq: int
    bucket: str
    stream: str
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class ChunkTask:
    custom_id: str
    text_id: str
    edition: str | None
    juan_dir: str
    manifest_path: str
    seq: int
    bucket: str
    stream: str
    core_start: int
    core_end: int
    context_start: int
    context_end: int
    stream_end: int
    input_text: str


@dataclass
class ValidationIssue:
    custom_id: str
    code: str
    message: str
    details: dict[str, Any] | None = None


class PunctuationValidationError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        input_index: int | None = None,
        output_index: int | None = None,
    ) -> None:
        super().__init__(message)
        self.input_index = input_index
        self.output_index = output_index


@dataclass(frozen=True)
class PunctuationScanResult:
    markers: list[dict[str, Any]]
    error: PunctuationValidationError | None = None


@dataclass(frozen=True)
class BatchWorkflowOptions:
    poll_seconds: int = DEFAULT_BATCH_POLL_SECONDS
    retries: int = DEFAULT_BATCH_RETRIES
    jobs: int = DEFAULT_BATCH_JOBS
    best_effort: bool = False


@dataclass(frozen=True)
class BatchWorkflowResult:
    text_id: str
    ok: bool
    state_path: Path | None
    attempts: int
    report_path: Path | None = None
    message: str | None = None


def settings_from_rc(
    rc: dict[str, Any],
    *,
    model: str | None = None,
    vendor: str | None = None,
    ai_config: Path | str | None = None,
    prompt: Path | str | None = None,
    chunk_chars: int | None = None,
    overlap: int | None = None,
    min_chars: int | None = None,
    cache_dir: Path | str | None = None,
) -> LlmSettings:
    llm = rc.get("llm") or {}
    raw_ai_config = ai_config if ai_config is not None else llm.get("ai_config")
    raw_prompt = prompt if prompt is not None else llm.get("prompt")
    raw_cache_dir = cache_dir if cache_dir is not None else llm.get("cache_dir")
    config_path = Path(raw_ai_config or DEFAULT_AI_CONFIG).expanduser()
    prompt_path = _resolve_prompt_path(raw_prompt)
    chosen_vendor = _normalize_vendor(vendor or llm.get("vendor") or DEFAULT_VENDOR)
    xml = load_ai_config(config_path, vendor=chosen_vendor)
    chosen_model = model or llm.get("model") or xml.get("model")
    if not isinstance(chosen_model, str) or not chosen_model.strip():
        raise ValueError(
            "LLM model is required: pass --model, set llm.model, or set "
            "model in ai-config XML"
        )
    chunk_value = _positive_int(
        chunk_chars if chunk_chars is not None else llm.get("chunk_chars"),
        DEFAULT_CHUNK_CHARS,
        "llm.chunk_chars",
    )
    overlap_value = _non_negative_int(
        overlap if overlap is not None else llm.get("overlap"),
        DEFAULT_OVERLAP,
        "llm.overlap",
    )
    if overlap_value >= chunk_value:
        raise ValueError("llm.overlap must be smaller than llm.chunk_chars")
    min_chars_value = _positive_int(
        min_chars if min_chars is not None else llm.get("min_chars"),
        DEFAULT_MIN_CHARS,
        "llm.min_chars",
    )
    resolved_cache = Path(raw_cache_dir).expanduser() if raw_cache_dir else None
    return LlmSettings(
        model=chosen_model.strip(),
        ai_config=config_path,
        prompt=prompt_path,
        vendor=chosen_vendor,
        chunk_chars=chunk_value,
        overlap=overlap_value,
        min_chars=min_chars_value,
        cache_dir=resolved_cache,
    )


def load_ai_config(path: Path, *, vendor: str = DEFAULT_VENDOR) -> dict[str, str]:
    """Load credential/config values for an OpenAI-compatible vendor from XML.

    Supported forms are intentionally permissive:
    ``<openai api_key="..." model="..."/>`` or child elements such as
    ``<api_key>...</api_key>`` under either the root or an ``openai`` element.
    Historical ``<openai-config><api-token>...`` files are accepted too. For
    other vendors, a parent containing ``<api-token>`` is selected by the part
    of its element name before the first ``-``.
    """
    path = Path(path).expanduser()
    if not path.exists():
        return {}
    root = ElementTree.fromstring(path.read_text(encoding="utf-8"))
    normalized_vendor = _normalize_vendor(vendor)
    node = _find_ai_config_node(root, normalized_vendor)
    if node is None:
        if normalized_vendor == DEFAULT_VENDOR:
            node = root
        else:
            return {}
    out: dict[str, str] = {}
    aliases = {
        "api_key": ("api_key", "api-key", "api_token", "api-token", "token", "key"),
        "organization": ("organization", "org", "openai-organization"),
        "project": ("project", "openai-project"),
        "base_url": (
            "base_url", "base-url", "api_base", "api-base", "endpoint", "url",
        ),
        "model": ("model",),
    }
    for key, names in aliases.items():
        value = _node_value(node, names)
        if value is None and node is not root:
            value = _node_value(root, names)
        if value is not None and value.strip():
            out[key] = value.strip()
    return out


def list_configured_vendors(path: Path) -> list[str]:
    """Return vendor names discovered from parents containing ``<api-token>``."""
    path = Path(path).expanduser()
    if not path.exists():
        return []
    root = ElementTree.fromstring(path.read_text(encoding="utf-8"))
    out: list[str] = []
    seen: set[str] = set()
    for node in root.iter():
        if _node_value(node, ("api-token", "api_token")) is None:
            continue
        vendor = _vendor_name_from_node(node)
        if vendor and vendor not in seen:
            seen.add(vendor)
            out.append(vendor)
    return out


def _find_ai_config_node(
    root: ElementTree.Element,
    vendor: str,
) -> ElementTree.Element | None:
    for node in root.iter():
        if (
            _vendor_name_from_node(node) == vendor
            and _node_looks_like_ai_config(node)
        ):
            return node
    return None


def _node_looks_like_ai_config(node: ElementTree.Element) -> bool:
    return any(
        _node_value(node, names) is not None
        for names in (
            ("api_key", "api-key", "api_token", "api-token", "token", "key"),
            ("model",),
        )
    )


def _vendor_name_from_node(node: ElementTree.Element) -> str:
    normalized = _strip_ns(node.tag).lower().replace("_", "-")
    return _normalize_vendor(normalized.split("-", 1)[0])


def _normalize_vendor(value: str) -> str:
    return str(value).strip().lower().replace("_", "-")


def _node_value(
    node: ElementTree.Element,
    names: tuple[str, ...],
) -> str | None:
    for name in names:
        value = node.attrib.get(name)
        if value is None:
            value = node.attrib.get(name.replace("_", "-"))
        if value is not None:
            return value
    wanted = {name.lower().replace("_", "-") for name in names}
    for child in list(node):
        if _strip_ns(child.tag).lower().replace("_", "-") in wanted:
            return child.text
    return None


def make_openai_client(ai_config: Path, vendor: str = DEFAULT_VENDOR) -> LlmClient:
    config = _client_config_for_vendor(ai_config, vendor)
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "the OpenAI Python SDK is required; install package dependency "
            "'openai'"
        ) from exc
    kwargs: dict[str, str] = {}
    if config.get("api_key"):
        kwargs["api_key"] = config["api_key"]
    if config.get("organization"):
        kwargs["organization"] = config["organization"]
    if config.get("project"):
        kwargs["project"] = config["project"]
    if config.get("base_url"):
        kwargs["base_url"] = config["base_url"]
    return OpenAiResponsesClient(OpenAI(**kwargs))


def _client_config_for_vendor(
    ai_config: Path,
    vendor: str = DEFAULT_VENDOR,
) -> dict[str, str]:
    config = load_ai_config(ai_config, vendor=vendor)
    normalized_vendor = _normalize_vendor(vendor)
    if not config.get("base_url"):
        default_base_url = DEFAULT_VENDOR_BASE_URLS.get(normalized_vendor)
        if default_base_url:
            config["base_url"] = default_base_url
        elif normalized_vendor != DEFAULT_VENDOR:
            raise ValueError(
                f"vendor {normalized_vendor!r} requires a base_url/base-url "
                "in the AI config"
            )
    return config


class OpenAiResponsesClient:
    def __init__(self, client: Any) -> None:
        self.client = client

    def list_models(self) -> list[dict[str, Any]]:
        models = self.client.models.list()
        data = getattr(models, "data", None)
        if data is None and isinstance(models, dict):
            data = models.get("data")
        out: list[dict[str, Any]] = []
        for model in data or []:
            if isinstance(model, dict):
                item = dict(model)
            elif hasattr(model, "model_dump"):
                item = model.model_dump()
            else:
                item = {
                    "id": getattr(model, "id", None),
                    "owned_by": getattr(model, "owned_by", None),
                    "created": getattr(model, "created", None),
                }
            if isinstance(item.get("id"), str):
                out.append(item)
        out.sort(key=lambda item: item["id"])
        return out

    def create_response(self, *, model: str, prompt: str, text: str) -> str:
        response = self.client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": text},
            ],
        )
        output = getattr(response, "output_text", None)
        if isinstance(output, str):
            return output
        return _extract_response_text(_to_plain_data(response))

    def submit_batch(
        self, *, requests_path: Path, metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        with requests_path.open("rb") as fh:
            uploaded = self.client.files.create(file=fh, purpose="batch")
        batch = self.client.batches.create(
            input_file_id=uploaded.id,
            endpoint="/v1/responses",
            completion_window="24h",
            metadata=metadata or {},
        )
        data = _to_plain_data(batch)
        data.setdefault("input_file_id", uploaded.id)
        return data

    def retrieve_batch(self, batch_id: str) -> dict[str, Any]:
        return _to_plain_data(self.client.batches.retrieve(batch_id))

    def download_file_text(self, file_id: str) -> str:
        content = self.client.files.content(file_id)
        if hasattr(content, "text"):
            text = content.text
            if isinstance(text, str):
                return text
        if hasattr(content, "read"):
            data = content.read()
            return data.decode("utf-8") if isinstance(data, bytes) else str(data)
        return str(content)


def list_available_models(
    ai_config: Path,
    *,
    vendor: str = DEFAULT_VENDOR,
    contains: str | None = None,
    client: LlmClient | None = None,
) -> list[dict[str, Any]]:
    client = client or make_openai_client(ai_config, vendor)
    models = client.list_models()
    if contains:
        needle = contains.lower()
        models = [
            model for model in models
            if needle in str(model.get("id", "")).lower()
        ]
    return models


def run_direct(
    bundle: str | Path | None,
    out_root: Path | None,
    *,
    text_id: str | None,
    text_prefix: str | None,
    selected_juans: set[int] | None,
    settings: LlmSettings,
    dry_run: bool,
    include_editions: bool = False,
    best_effort: bool = False,
    client: LlmClient | None = None,
) -> int:
    if not dry_run:
        client = client or make_openai_client(settings.ai_config, settings.vendor)
    prompt_text = _read_prompt(settings.prompt)
    bundle_dirs = _selected_bundle_dirs(bundle, out_root, text_id, text_prefix)
    total_tasks = total_markers = total_issues = 0
    run_stamp = _utc_stamp()
    for bundle_dir in bundle_dirs:
        print(f"[bundle {bundle_dir.name}]")
        bundle_tasks: list[ChunkTask] = []
        bundle_outputs: dict[str, str] = {}
        bundle_issues: list[ValidationIssue] = []
        for scope in _scope_targets(bundle_dir, include_editions=include_editions):
            print(f"[{scope_label(scope[2])}]")
            tasks = build_tasks_for_scope(
                scope[0], scope[1], bundle_dir.name, scope[2],
                settings=settings, prompt_text=prompt_text,
                selected_juans=selected_juans,
            )
            bundle_tasks.extend(tasks)
            outputs: dict[str, str] = {}
            request_issues: list[ValidationIssue] = []
            total_tasks += len(tasks)
            if dry_run:
                print(f"  would submit {len(tasks)} chunk request(s)")
            else:
                assert client is not None
                for task in tasks:
                    try:
                        outputs[task.custom_id] = client.create_response(
                            model=settings.model, prompt=prompt_text,
                            text=task.input_text,
                        )
                    except Exception as exc:  # noqa: BLE001 - diagnose per chunk
                        request_issues.append(ValidationIssue(
                            task.custom_id, "request-error", str(exc),
                        ))
                bundle_outputs.update(outputs)
                result = write_outputs_for_scope(
                    scope[0], scope[1], bundle_dir.name, scope[2], tasks,
                    outputs, settings=settings, prompt_text=prompt_text,
                    batch_id=None, preexisting_issues=request_issues,
                    best_effort=best_effort,
                )
                total_markers += result["markers"]
                total_issues += len(result["issues"])
                bundle_issues.extend(result["issues"])
                print(
                    f"  wrote {result['assets']} asset(s), "
                    f"{result['markers']} marker(s), "
                    f"{len(result['issues'])} rejected chunk(s)"
                )
        if not dry_run and bundle_tasks:
            report_path = _write_direct_text_report(
                _direct_report_path(bundle_dir, settings, run_stamp),
                settings=settings,
                bundle_dir=bundle_dir,
                tasks=bundle_tasks,
                outputs=bundle_outputs,
                issues=bundle_issues,
                best_effort=best_effort,
            )
            print(f"clear-text report: {report_path}")
    if dry_run:
        print(f"would submit {total_tasks} chunk request(s)")
        return 0
    print(
        f"generated {total_markers} marker(s) from {total_tasks} chunk "
        f"request(s)"
    )
    return 0 if best_effort else (1 if total_issues else 0)


def submit_batch(
    bundle: str | Path | None,
    out_root: Path | None,
    *,
    text_id: str | None,
    text_prefix: str | None,
    selected_juans: set[int] | None,
    settings: LlmSettings,
    include_editions: bool = False,
    client: LlmClient | None = None,
) -> Path:
    client = client or make_openai_client(settings.ai_config, settings.vendor)
    prompt_text = _read_prompt(settings.prompt)
    bundle_dirs = _selected_bundle_dirs(bundle, out_root, text_id, text_prefix)
    all_tasks: list[ChunkTask] = []
    for bundle_dir in bundle_dirs:
        for juan_dir, manifest_path, short in _scope_targets(
            bundle_dir, include_editions=include_editions,
        ):
            all_tasks.extend(build_tasks_for_scope(
                juan_dir, manifest_path, bundle_dir.name, short,
                settings=settings, prompt_text=prompt_text,
                selected_juans=selected_juans,
            ))
    if not all_tasks:
        raise RuntimeError("no chunk requests to submit")
    state_dir = _state_dir(settings, bundle_dirs[0])
    state_dir.mkdir(parents=True, exist_ok=True)
    state_text = model_slug(bundle_dirs[0].name)
    state_path = state_dir / (
        f"punctuation-{state_text}-{_utc_stamp()}-"
        f"{model_slug(settings.model)}.batch.yaml"
    )
    requests_path = state_path.with_suffix(".jsonl")
    requests_path.write_text(
        "".join(_batch_request_line(task, settings.model, prompt_text)
                for task in all_tasks),
        encoding="utf-8",
    )
    batch = client.submit_batch(
        requests_path=requests_path,
        metadata={"bkk_task": "punctuation", "model": settings.model[:512]},
    )
    state = {
        "schema": STATE_SCHEMA_VERSION,
        "task": "punctuation",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "vendor": settings.vendor,
        "model": settings.model,
        "prompt_path": str(settings.prompt),
        "prompt_hash": sha256_text(prompt_text),
        "chunk_chars": settings.chunk_chars,
        "overlap": settings.overlap,
        "min_chars": settings.min_chars,
        "batch": batch,
        "requests_file": str(requests_path),
        "tasks": [_task_to_dict(task) for task in all_tasks],
    }
    state_path.write_text(dump(state), encoding="utf-8")
    return state_path


def run_batch_workflow(
    bundle: str | Path | None,
    out_root: Path | None,
    *,
    text_id: str | None,
    text_prefix: str | None,
    selected_juans: set[int] | None,
    settings: LlmSettings,
    include_editions: bool = False,
    options: BatchWorkflowOptions | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    options = options or BatchWorkflowOptions()
    if options.poll_seconds < 0:
        raise ValueError("poll seconds must be non-negative")
    if options.retries < 0:
        raise ValueError("retries must be non-negative")
    if options.jobs <= 0:
        raise ValueError("jobs must be a positive integer")

    bundle_dirs = _selected_bundle_dirs(bundle, out_root, text_id, text_prefix)
    print(
        f"batch workflow: {len(bundle_dirs)} text(s), "
        f"jobs={options.jobs}, retries={options.retries}, "
        f"poll={options.poll_seconds}s"
    )
    results: list[BatchWorkflowResult] = []
    if options.jobs == 1 or len(bundle_dirs) <= 1:
        for bundle_dir in bundle_dirs:
            try:
                results.append(_run_batch_workflow_for_bundle(
                    bundle_dir,
                    selected_juans=selected_juans,
                    settings=settings,
                    include_editions=include_editions,
                    options=options,
                    sleep_fn=sleep_fn,
                ))
            except Exception as exc:  # noqa: BLE001 - keep later texts running
                results.append(BatchWorkflowResult(
                    text_id=bundle_dir.name,
                    ok=False,
                    state_path=None,
                    attempts=0,
                    message=str(exc),
                ))
                print(f"[{bundle_dir.name}] failed: {exc}")
    else:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(options.jobs, len(bundle_dirs)),
        ) as executor:
            future_to_bundle = {
                executor.submit(
                    _run_batch_workflow_for_bundle,
                    bundle_dir,
                    selected_juans=selected_juans,
                    settings=settings,
                    include_editions=include_editions,
                    options=options,
                    sleep_fn=sleep_fn,
                ): bundle_dir
                for bundle_dir in bundle_dirs
            }
            for future in concurrent.futures.as_completed(future_to_bundle):
                bundle_dir = future_to_bundle[future]
                try:
                    results.append(future.result())
                except Exception as exc:  # noqa: BLE001 - keep other jobs running
                    results.append(BatchWorkflowResult(
                        text_id=bundle_dir.name,
                        ok=False,
                        state_path=None,
                        attempts=0,
                        message=str(exc),
                    ))
                    print(f"[{bundle_dir.name}] failed: {exc}")

    failed = [result for result in results if not result.ok]
    print(
        f"batch workflow complete: {len(results) - len(failed)} succeeded, "
        f"{len(failed)} failed"
    )
    for result in failed:
        detail = f"; report: {result.report_path}" if result.report_path else ""
        message = f"; {result.message}" if result.message else ""
        print(f"  {result.text_id}: failed after {result.attempts} attempt(s){detail}{message}")
    return 1 if failed else 0


def _run_batch_workflow_for_bundle(
    bundle_dir: Path,
    *,
    selected_juans: set[int] | None,
    settings: LlmSettings,
    include_editions: bool,
    options: BatchWorkflowOptions,
    sleep_fn: Callable[[float], None],
) -> BatchWorkflowResult:
    text_id = bundle_dir.name
    print(f"[{text_id}] submitting batch")
    client = make_openai_client(settings.ai_config, settings.vendor)
    state_path = submit_batch(
        bundle_dir,
        None,
        text_id=None,
        text_prefix=None,
        selected_juans=selected_juans,
        settings=settings,
        include_editions=include_editions,
        client=client,
    )
    print(f"[{text_id}] submitted batch; state: {state_path}")
    attempts = 1
    state_path, message = _run_batch_attempt(
        text_id, state_path, settings, client, options, sleep_fn,
    )
    if message is None:
        return BatchWorkflowResult(text_id, True, state_path, attempts)

    for retry_index in range(options.retries):
        print(f"[{text_id}] retry {retry_index + 1}/{options.retries}: {message}")
        try:
            state_path = retry_failed_batch(state_path, settings=settings, client=client)
        except RuntimeError as exc:
            report_path = _write_workflow_failure_report(state_path, str(exc))
            return BatchWorkflowResult(
                text_id, False, state_path, attempts,
                report_path=report_path, message=str(exc),
            )
        print(f"[{text_id}] submitted retry batch; state: {state_path}")
        attempts += 1
        state_path, message = _run_batch_attempt(
            text_id, state_path, settings, client, options, sleep_fn,
        )
        if message is None:
            return BatchWorkflowResult(text_id, True, state_path, attempts)

    if options.best_effort and message == "batch had rejected or missing chunks":
        print(f"[{text_id}] best-effort collection after final retry: {message}")
        collect_batch(
            state_path, settings=settings, client=client, best_effort=True,
        )
        return BatchWorkflowResult(
            text_id, True, state_path, attempts,
            message="best-effort collection wrote partial assets",
        )

    report_path = _write_workflow_failure_report(state_path, message)
    return BatchWorkflowResult(
        text_id, False, state_path, attempts,
        report_path=report_path, message=message,
    )


def _run_batch_attempt(
    text_id: str,
    state_path: Path,
    settings: LlmSettings,
    client: LlmClient,
    options: BatchWorkflowOptions,
    sleep_fn: Callable[[float], None],
) -> tuple[Path, str | None]:
    batch = _wait_for_batch_terminal(
        text_id, state_path, client, options.poll_seconds, sleep_fn,
    )
    status = batch.get("status")
    if status != "completed":
        return state_path, f"batch status {status or '<unknown>'}"
    rc = collect_batch(state_path, settings=settings, client=client)
    if rc == 0:
        return state_path, None
    return state_path, "batch had rejected or missing chunks"


def _wait_for_batch_terminal(
    text_id: str,
    state_path: Path,
    client: LlmClient,
    poll_seconds: int,
    sleep_fn: Callable[[float], None],
) -> dict[str, Any]:
    state = yaml.load(state_path.read_text(encoding="utf-8"), Loader=_YAML_LOADER)
    if not isinstance(state, dict):
        raise RuntimeError(f"{state_path}: state file is not a mapping")
    batch_obj = state.get("batch") or {}
    batch_id = batch_obj.get("id")
    if not isinstance(batch_id, str) or not batch_id:
        raise RuntimeError(f"{state_path}: missing batch.id")
    while True:
        batch = client.retrieve_batch(batch_id)
        _save_batch_diagnostics(state_path, state, batch, client)
        print(f"[{text_id}] ", end="")
        print_batch_diagnostics(state_path, batch)
        status = batch.get("status")
        if status in _BATCH_TERMINAL_STATUSES:
            return batch
        if poll_seconds:
            print(f"[{text_id}] waiting {poll_seconds}s before next poll")
            sleep_fn(poll_seconds)
        else:
            sleep_fn(0)


def _write_workflow_failure_report(state_path: Path, message: str | None) -> Path:
    state = yaml.load(state_path.read_text(encoding="utf-8"), Loader=_YAML_LOADER)
    if not isinstance(state, dict):
        state = {}
    batch_report_path = state_path.with_suffix(".batch-report.yaml")
    problems: list[dict[str, Any]] = []
    if batch_report_path.exists():
        batch_report = yaml.load(
            batch_report_path.read_text(encoding="utf-8"),
            Loader=_YAML_LOADER,
        )
        if isinstance(batch_report, dict):
            for chunk in batch_report.get("chunks") or []:
                if not isinstance(chunk, dict):
                    continue
                if chunk.get("status") == "accepted":
                    continue
                problems.append({
                    "id": chunk.get("id"),
                    "text_id": chunk.get("text_id"),
                    "edition": chunk.get("edition"),
                    "seq": chunk.get("seq"),
                    "bucket": chunk.get("bucket"),
                    "stream": chunk.get("stream"),
                    "status": chunk.get("status"),
                    "issues": chunk.get("issues") or [],
                })
    report: dict[str, Any] = {
        "schema": 1,
        "task": "punctuation-batch-workflow-report",
        "status": "failed",
        "state_file": str(state_path),
        "batch": state.get("batch"),
        "message": message,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "problems": problems,
    }
    if batch_report_path.exists():
        report["batch_report"] = str(batch_report_path)
    report_path = state_path.with_suffix(".workflow-report.yaml")
    report_path.write_text(dump(report), encoding="utf-8")
    return report_path


def collect_batch(
    state_path: Path,
    *,
    settings: LlmSettings | None = None,
    client: LlmClient | None = None,
    best_effort: bool = False,
) -> int:
    state = yaml.load(Path(state_path).read_text(encoding="utf-8"), Loader=_YAML_LOADER)
    if not isinstance(state, dict):
        raise RuntimeError(f"{state_path}: state file is not a mapping")
    batch_obj = state.get("batch") or {}
    batch_id = batch_obj.get("id")
    if not isinstance(batch_id, str) or not batch_id:
        raise RuntimeError(f"{state_path}: missing batch.id")
    model = state.get("model")
    if not isinstance(model, str) or not model:
        raise RuntimeError(f"{state_path}: missing model")
    if settings is None:
        settings = LlmSettings(
            model=model,
            ai_config=DEFAULT_AI_CONFIG.expanduser(),
            prompt=Path(state.get("prompt_path") or _default_prompt_path()),
            vendor=str(state.get("vendor") or DEFAULT_VENDOR),
            chunk_chars=int(state.get("chunk_chars") or DEFAULT_CHUNK_CHARS),
            overlap=int(state.get("overlap") or DEFAULT_OVERLAP),
            min_chars=int(state.get("min_chars") or DEFAULT_MIN_CHARS),
        )
    client = client or make_openai_client(settings.ai_config, settings.vendor)
    batch = client.retrieve_batch(batch_id)
    _save_batch_diagnostics(Path(state_path), state, batch, client)
    status = batch.get("status")
    if status != "completed":
        print_batch_diagnostics(Path(state_path), batch)
        return 1
    output_file_id = batch.get("output_file_id")
    if not isinstance(output_file_id, str) or not output_file_id:
        raise RuntimeError(f"batch {batch_id} completed without output_file_id")
    output_text = client.download_file_text(output_file_id)
    combined_output_text = _combined_output_text(Path(state_path), state, output_text)
    outputs, issues = parse_batch_output(combined_output_text)
    issues = _unresolved_parse_issues(outputs, issues)
    tasks = [_task_from_dict(t) for t in state.get("tasks") or [] if isinstance(t, dict)]
    prompt_text = _read_prompt(settings.prompt)
    grouped: dict[tuple[str, str, str | None], list[ChunkTask]] = {}
    for task in tasks:
        grouped.setdefault(
            (task.juan_dir, task.manifest_path, task.edition), []
        ).append(task)
    all_task_ids = {task.custom_id for task in tasks}
    total_markers = 0
    total_issues = len([issue for issue in issues if issue.custom_id not in all_task_ids])
    report_issues = [issue for issue in issues if issue.custom_id not in all_task_ids]
    total_assets = 0
    for (juan_dir, manifest_path, short), scope_tasks in grouped.items():
        text_id = scope_tasks[0].text_id
        scope_ids = {task.custom_id for task in scope_tasks}
        scope_issues = [issue for issue in issues if issue.custom_id in scope_ids]
        result = write_outputs_for_scope(
            Path(juan_dir), Path(manifest_path), text_id, short,
            scope_tasks, outputs, settings=settings, prompt_text=prompt_text,
            batch_id=batch_id, preexisting_issues=scope_issues,
            best_effort=best_effort,
        )
        total_markers += result["markers"]
        total_issues += len(result["issues"])
        report_issues.extend(result["issues"])
        total_assets += result["assets"]
    report_path = _write_batch_text_report(
        Path(state_path), state, tasks, outputs, report_issues,
    )
    print(
        f"collected batch {batch_id}: wrote {total_assets} asset(s), "
        f"{total_markers} marker(s), {total_issues} rejected chunk(s)"
    )
    print(f"clear-text report: {report_path}")
    return 0 if best_effort else (1 if total_issues else 0)


def inspect_batch(
    state_path: Path,
    *,
    settings: LlmSettings,
    client: LlmClient | None = None,
) -> int:
    state = yaml.load(Path(state_path).read_text(encoding="utf-8"), Loader=_YAML_LOADER)
    if not isinstance(state, dict):
        raise RuntimeError(f"{state_path}: state file is not a mapping")
    batch_obj = state.get("batch") or {}
    batch_id = batch_obj.get("id")
    if not isinstance(batch_id, str) or not batch_id:
        raise RuntimeError(f"{state_path}: missing batch.id")
    client = client or make_openai_client(settings.ai_config, settings.vendor)
    batch = client.retrieve_batch(batch_id)
    _save_batch_diagnostics(Path(state_path), state, batch, client)
    print_batch_diagnostics(Path(state_path), batch)
    return 0


def retry_failed_batch(
    state_path: Path,
    *,
    settings: LlmSettings,
    client: LlmClient | None = None,
) -> Path:
    state_path = Path(state_path)
    state = yaml.load(state_path.read_text(encoding="utf-8"), Loader=_YAML_LOADER)
    if not isinstance(state, dict):
        raise RuntimeError(f"{state_path}: state file is not a mapping")
    tasks = [_task_from_dict(t) for t in state.get("tasks") or [] if isinstance(t, dict)]
    if not tasks:
        raise RuntimeError(f"{state_path}: no tasks to retry")
    client = client or make_openai_client(settings.ai_config, settings.vendor)
    batch_obj = state.get("batch") or {}
    batch_id = batch_obj.get("id")
    if isinstance(batch_id, str) and batch_id:
        batch = client.retrieve_batch(batch_id)
        _save_batch_diagnostics(state_path, state, batch, client)
    failed_ids = _failed_custom_ids_for_state(state_path, state, tasks)
    if not failed_ids:
        raise RuntimeError(f"{state_path}: no failed chunks to retry")
    failed_tasks = [task for task in tasks if task.custom_id in failed_ids]
    prompt_text = _read_prompt(settings.prompt)
    retry_path = state_path.with_name(
        f"{state_path.stem}.retry-{_utc_stamp()}.batch.yaml"
    )
    requests_path = retry_path.with_suffix(".jsonl")
    requests_path.write_text(
        "".join(_batch_request_line(task, settings.model, prompt_text)
                for task in failed_tasks),
        encoding="utf-8",
    )
    batch = client.submit_batch(
        requests_path=requests_path,
        metadata={
            "bkk_task": "punctuation-retry",
            "model": settings.model[:512],
            "retry_of": str(state_path.name)[:512],
        },
    )
    previous_output_files = _previous_output_files_for_retry(state_path, state)
    retry_state = {
        **state,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "batch": batch,
        "requests_file": str(requests_path),
        "retry_of": str(state_path),
        "retry_custom_ids": sorted(failed_ids),
        "previous_output_files": previous_output_files,
    }
    retry_path.write_text(dump(retry_state), encoding="utf-8")
    return retry_path


def build_tasks_for_scope(
    juan_dir: Path,
    manifest_path: Path,
    text_id: str,
    short: str | None,
    *,
    settings: LlmSettings,
    prompt_text: str,
    selected_juans: set[int] | None,
) -> list[ChunkTask]:
    del prompt_text
    manifest = _yaml_load(manifest_path)
    tasks: list[ChunkTask] = []
    chunk_index = 0
    for seq, juan_path in _juan_entries(juan_dir, text_id, short, selected_juans):
        data = _yaml_load(juan_path)
        marker_asset = load_marker_asset(juan_dir, manifest, seq)
        for bucket_name in BUCKETS:
            bucket = data.get(bucket_name)
            if not isinstance(bucket, dict):
                continue
            text = bucket.get("text")
            if not isinstance(text, str) or not text:
                continue
            markers = effective_markers_for_bucket(data, bucket_name, marker_asset)
            regions = extract_stream_regions(
                text_id, short, seq, bucket_name, text, markers,
            )
            regions = [
                region for region in regions
                if len(region.text) >= settings.min_chars
            ]
            boundaries = _head_boundaries(text, markers)
            for region in regions:
                chunks = chunk_region(
                    region, boundaries, chunk_chars=settings.chunk_chars,
                    overlap=settings.overlap,
                )
                for chunk in chunks:
                    chunk_index += 1
                    tasks.append(_task_with_id(
                        chunk, chunk_index, juan_dir, manifest_path,
                    ))
    return tasks


def write_outputs_for_scope(
    juan_dir: Path,
    manifest_path: Path,
    text_id: str,
    short: str | None,
    tasks: list[ChunkTask],
    outputs: dict[str, str],
    *,
    settings: LlmSettings,
    prompt_text: str,
    batch_id: str | None,
    preexisting_issues: list[ValidationIssue] | None = None,
    best_effort: bool = False,
) -> dict[str, Any]:
    manifest = _yaml_load(manifest_path)
    tasks_by_seq: dict[int, list[ChunkTask]] = {}
    for task in tasks:
        tasks_by_seq.setdefault(task.seq, []).append(task)
    total_assets = total_markers = 0
    issues = list(preexisting_issues or [])
    issue_by_custom_id = {
        issue.custom_id: issue
        for issue in issues
        if issue.custom_id
    }
    marker_hashes: dict[int, tuple[str, str]] = {}
    files: list[tuple[Path, dict[str, Any]]] = []
    for seq, seq_tasks in sorted(tasks_by_seq.items()):
        juan_path = _juan_path(juan_dir, text_id, short, seq)
        data = _yaml_load(juan_path)
        marker_asset = load_marker_asset(juan_dir, manifest, seq)
        markers_by_bucket: dict[str, list[dict[str, Any]]] = {
            bucket: [] for bucket in VALID_BUCKETS
        }
        chunk_rows: list[dict[str, Any]] = []
        occupied = _occupied_marker_ids(data, marker_asset)
        for task in seq_tasks:
            existing_issue = issue_by_custom_id.get(task.custom_id)
            if existing_issue is not None:
                if best_effort:
                    markers_by_bucket[task.bucket].append(
                        _llm_error_marker_for_issue(
                            task, existing_issue, model=settings.model,
                        )
                    )
                chunk_rows.append(_chunk_row(
                    task, status=existing_issue.code,
                    message=existing_issue.message,
                ))
                continue
            output = outputs.get(task.custom_id)
            if output is None:
                issues.append(ValidationIssue(
                    task.custom_id, "missing-output",
                    "batch output did not include this custom_id",
                ))
                if best_effort:
                    markers_by_bucket[task.bucket].append(
                        _llm_error_marker_for_issue(
                            task, issues[-1], model=settings.model,
                        )
                    )
                chunk_rows.append(_chunk_row(task, status="missing-output"))
                continue
            try:
                markers = markers_from_punctuated_output(
                    task.input_text, output,
                    context_start=task.context_start,
                    core_start=task.core_start,
                    core_end=task.core_end,
                    include_core_end=task.core_end == task.stream_end,
                )
            except ValueError as exc:
                issue = _validation_issue_for_output(task, output, exc)
                issues.append(issue)
                if best_effort:
                    scan = best_effort_markers_from_punctuated_output(
                        task.input_text, output,
                        context_start=task.context_start,
                        core_start=task.core_start,
                        core_end=task.core_end,
                        include_core_end=task.core_end == task.stream_end,
                    )
                    usable_markers = _usable_markers_before_issue(
                        task, scan.markers, issue,
                    )
                    _annotate_llm_markers(usable_markers, model=settings.model)
                    markers_by_bucket[task.bucket].extend(usable_markers)
                    markers_by_bucket[task.bucket].append(
                        _llm_error_marker_for_issue(
                            task, issue, model=settings.model,
                        )
                    )
                chunk_rows.append(
                    _chunk_row(task, status="rejected", message=str(exc))
                )
                continue
            _annotate_llm_markers(markers, model=settings.model)
            markers_by_bucket[task.bucket].extend(markers)
            chunk_rows.append(
                _chunk_row(task, status="accepted", marker_count=len(markers))
            )
        for bucket in markers_by_bucket:
            markers_by_bucket[bucket].sort(key=lambda m: (m.get("offset", 0), m.get("content", "")))
            ids = allocate_marker_ids(
                [str(m.get("type") or "") for m in markers_by_bucket[bucket]],
                text_id=text_id,
                edition=short or "bkk",
                juan_label=f"{seq:03d}",
                occupied_ids=occupied,
            )
            for marker, marker_id in zip(markers_by_bucket[bucket], ids):
                marker["id"] = marker_id
        marker_count = sum(len(v) for v in markers_by_bucket.values())
        asset = build_llm_punctuation_asset(
            text_id, seq, short, settings=settings, prompt_text=prompt_text,
            markers_by_bucket=markers_by_bucket, chunks=chunk_rows,
            batch_id=batch_id,
        )
        filename = llm_punctuation_asset_filename(text_id, seq, short, settings.model)
        files.append((juan_dir / filename, asset))
        marker_hashes[seq] = (filename, asset["hash"])
        total_assets += 1
        total_markers += marker_count
    if files:
        for path, asset in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(dump(asset), encoding="utf-8")
        _update_manifest_references(manifest_path, marker_hashes, settings.model)
    return {"assets": total_assets, "markers": total_markers, "issues": issues}


def extract_stream_regions(
    text_id: str,
    short: str | None,
    seq: int,
    bucket: str,
    text: str,
    markers: list[dict[str, Any]],
) -> list[StreamRegion]:
    spans = _voice_spans(len(text), markers)
    separate = [s for s in spans if s.name in {"note", "comm", "commentary"}]
    excluded = _merge_ranges([(s.start, s.end) for s in separate + [s for s in spans if s.name == "head"]])
    regions: list[StreamRegion] = []
    pos = 0
    for start, end in excluded:
        if pos < start:
            regions.append(_region(text_id, short, seq, bucket, "main", pos, start, text))
        pos = max(pos, end)
    if pos < len(text):
        regions.append(_region(text_id, short, seq, bucket, "main", pos, len(text), text))
    for span in separate:
        regions.append(_region(
            text_id, short, seq, bucket, f"{span.name}:{span.marker_id}",
            span.start, span.end, text,
        ))
    return [r for r in regions if r.text]


def chunk_region(
    region: StreamRegion,
    boundaries: list[int],
    *,
    chunk_chars: int,
    overlap: int,
) -> list[ChunkTask]:
    abs_regions = _split_by_boundaries(region.start, region.end, boundaries)
    out: list[ChunkTask] = []
    before_overlap = overlap // 2
    after_overlap = overlap - before_overlap
    for start, end in abs_regions:
        pos = start
        while pos < end:
            core_end = min(pos + chunk_chars, end)
            context_start = max(region.start, pos - before_overlap)
            context_end = min(region.end, core_end + after_overlap)
            out.append(ChunkTask(
                custom_id="",
                text_id=region.text_id,
                edition=region.edition,
                juan_dir="",
                manifest_path="",
                seq=region.seq,
                bucket=region.bucket,
                stream=region.stream,
                core_start=pos,
                core_end=core_end,
                context_start=context_start,
                context_end=context_end,
                stream_end=region.end,
                input_text=region.text[
                    context_start - region.start: context_end - region.start
                ],
            ))
            pos = core_end
    return out


def markers_from_punctuated_output(
    original: str,
    output: str,
    *,
    context_start: int,
    core_start: int,
    core_end: int,
    include_core_end: bool = True,
) -> list[dict[str, Any]]:
    return _scan_punctuated_output(
        original, output,
        context_start=context_start,
        core_start=core_start,
        core_end=core_end,
        include_core_end=include_core_end,
        fail_fast=True,
    ).markers


def best_effort_markers_from_punctuated_output(
    original: str,
    output: str,
    *,
    context_start: int,
    core_start: int,
    core_end: int,
    include_core_end: bool = True,
) -> PunctuationScanResult:
    return _scan_punctuated_output(
        original, output,
        context_start=context_start,
        core_start=core_start,
        core_end=core_end,
        include_core_end=include_core_end,
        fail_fast=False,
    )


def _scan_punctuated_output(
    original: str,
    output: str,
    *,
    context_start: int,
    core_start: int,
    core_end: int,
    include_core_end: bool,
    fail_fast: bool,
) -> PunctuationScanResult:
    output = output.replace("\r\n", "\n").replace("\r", "\n")
    markers: list[dict[str, Any]] = []
    core_rel_start = max(0, core_start - context_start)
    i = core_rel_start
    j = _output_start_for_core(original, output, core_rel_start)
    variant_run_len = 0
    variant_run_input_start: int | None = None
    variant_run_output_start: int | None = None

    def fail(
        message: str,
        *,
        input_index: int | None = None,
        output_index: int | None = None,
        validation_index: int | None = None,
    ) -> PunctuationScanResult:
        exc = PunctuationValidationError(
            message, input_index=input_index, output_index=output_index,
        )
        check_index = input_index if validation_index is None else validation_index
        if check_index is not None:
            absolute = context_start + check_index
            if not _in_owned_core(
                absolute, core_start, core_end, include_core_end,
            ):
                return PunctuationScanResult(markers)
        if fail_fast:
            raise exc
        return PunctuationScanResult(markers, exc)

    while j < len(output):
        ch = output[j]
        if i < len(original) and ch == original[i]:
            variant_run_len = 0
            variant_run_input_start = None
            variant_run_output_start = None
            i += 1
            j += 1
            continue
        absolute = context_start + i
        if ch == "\n":
            run_end = j
            while run_end < len(output) and output[run_end] == "\n":
                run_end += 1
            if run_end - j < 2:
                return fail(
                    f"unexpected single newline at output index {j}",
                    input_index=i,
                    output_index=j,
                )
            if _in_owned_core(absolute, core_start, core_end, include_core_end):
                markers.append({
                    "type": "paragraph-break",
                    "offset": absolute,
                    "content": "",
                })
            j = run_end
            continue
        if ch in ALLOWED_PUNCTUATION:
            if _in_owned_core(absolute, core_start, core_end, include_core_end):
                markers.append({
                    "type": "punctuation",
                    "offset": absolute,
                    "content": ch,
                })
            j += 1
            continue
        if i < len(original):
            if variant_run_len == 0:
                variant_run_input_start = i
                variant_run_output_start = j
            variant_run_len += 1
            if variant_run_len > MAX_ADJACENT_VARIANT_CHARS:
                input_start = (
                    i if variant_run_input_start is None
                    else variant_run_input_start
                )
                output_start = (
                    j if variant_run_output_start is None
                    else variant_run_output_start
                )
                return fail(
                    "output diverged for more than "
                    f"{MAX_ADJACENT_VARIANT_CHARS} adjacent original "
                    f"character(s) starting at input index {input_start}",
                    input_index=input_start,
                    output_index=output_start,
                    validation_index=i,
                )
            if _in_owned_core(absolute, core_start, core_end, include_core_end):
                markers.append({
                    "type": "variant",
                    "offset": absolute,
                    "length": 1,
                    "content": original[i],
                    "replacement": ch,
                })
            i += 1
            j += 1
            continue
        return fail(
            f"unexpected character {ch!r} at output index {j}",
            input_index=i,
            output_index=j,
        )
    if i != len(original):
        return fail(
            f"output omitted {len(original) - i} original character(s)",
            input_index=i,
            output_index=len(output),
        )
    return PunctuationScanResult(markers)


def _output_start_for_core(original: str, output: str, core_rel_start: int) -> int:
    if core_rel_start <= 0:
        return 0
    if core_rel_start >= len(original):
        return len(output)
    core_prefix = original[core_rel_start:core_rel_start + 4]
    for size in range(len(core_prefix), 0, -1):
        pos = output.find(core_prefix[:size])
        if pos >= 0:
            return pos
    return 0


def parse_batch_output(output_text: str) -> tuple[dict[str, str], list[ValidationIssue]]:
    outputs: dict[str, str] = {}
    issues: list[ValidationIssue] = []
    for line_no, line in enumerate(output_text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            issues.append(ValidationIssue(
                f"line-{line_no}", "json", f"invalid JSONL row: {exc}",
            ))
            continue
        custom_id = row.get("custom_id")
        if not isinstance(custom_id, str) or not custom_id:
            issues.append(ValidationIssue(
                f"line-{line_no}", "custom-id", "row missing custom_id",
            ))
            continue
        if row.get("error"):
            issues.append(ValidationIssue(
                custom_id, "request-error", str(row.get("error")),
            ))
            continue
        response = row.get("response") or {}
        if response.get("status_code") != 200:
            issues.append(ValidationIssue(
                custom_id, "status",
                f"request status {response.get('status_code')}",
            ))
            continue
        try:
            outputs[custom_id] = _extract_response_text(response.get("body") or {})
        except ValueError as exc:
            issues.append(ValidationIssue(custom_id, "response-text", str(exc)))
    return outputs, issues


def _combined_output_text(
    state_path: Path,
    state: dict[str, Any],
    current_output_text: str,
) -> str:
    parts: list[str] = []
    for raw in state.get("previous_output_files") or []:
        if not isinstance(raw, str) or not raw:
            continue
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = state_path.parent / path
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    parts.append(current_output_text)
    return "\n".join(part.rstrip("\n") for part in parts if part is not None) + "\n"


def _unresolved_parse_issues(
    outputs: dict[str, str],
    issues: list[ValidationIssue],
) -> list[ValidationIssue]:
    # Retry collection parses older output first and retry output last. If the
    # retry supplies a valid response for a custom_id that previously had a
    # request-level error, drop the stale request issue.
    return [
        issue for issue in issues
        if issue.custom_id not in outputs or issue.custom_id.startswith("line-")
    ]


def _failed_custom_ids_for_state(
    state_path: Path,
    state: dict[str, Any],
    tasks: list[ChunkTask],
) -> set[str]:
    texts: list[str] = []
    for suffix in (".batch-output.jsonl", ".batch-error.jsonl"):
        path = state_path.with_suffix(suffix)
        if path.exists():
            texts.append(path.read_text(encoding="utf-8"))
    combined = _combined_output_text(state_path, state, "\n".join(texts))
    outputs, issues = parse_batch_output(combined)
    issues = _unresolved_parse_issues(outputs, issues)
    failed = {
        issue.custom_id for issue in issues
        if issue.custom_id and not issue.custom_id.startswith("line-")
    }
    for task in tasks:
        output = outputs.get(task.custom_id)
        if output is None:
            failed.add(task.custom_id)
            continue
        try:
            markers_from_punctuated_output(
                task.input_text,
                output,
                context_start=task.context_start,
                core_start=task.core_start,
                core_end=task.core_end,
                include_core_end=task.core_end == task.stream_end,
            )
        except ValueError:
            failed.add(task.custom_id)
    return failed


def _validation_issues_for_outputs(
    tasks: list[ChunkTask],
    outputs: dict[str, str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for task in tasks:
        output = outputs.get(task.custom_id)
        if output is None:
            continue
        try:
            markers_from_punctuated_output(
                task.input_text,
                output,
                context_start=task.context_start,
                core_start=task.core_start,
                core_end=task.core_end,
                include_core_end=task.core_end == task.stream_end,
            )
        except ValueError as exc:
            issues.append(_validation_issue_for_output(task, output, exc))
    return issues


def _validation_issue_for_output(
    task: ChunkTask,
    output: str,
    exc: ValueError,
) -> ValidationIssue:
    details: dict[str, Any] | None = None
    if isinstance(exc, PunctuationValidationError):
        details = _validation_context_details(task, output, exc)
    return ValidationIssue(task.custom_id, "invalid-output", str(exc), details)


def _annotate_llm_markers(markers: list[dict[str, Any]], *, model: str) -> None:
    for marker in markers:
        marker["source"] = (
            model if marker.get("type") == "variant" else REFERENCE_ROLE
        )
        marker["model"] = model


def _usable_markers_before_issue(
    task: ChunkTask,
    markers: list[dict[str, Any]],
    issue: ValidationIssue,
) -> list[dict[str, Any]]:
    offending_offset = _issue_absolute_offset(issue)
    if offending_offset is None:
        return list(markers)
    usable: list[dict[str, Any]] = []
    for marker in markers:
        offset = _marker_offset(marker)
        if offset is not None and offset < offending_offset:
            usable.append(marker)
    return usable


def _llm_error_marker_for_issue(
    task: ChunkTask,
    issue: ValidationIssue,
    *,
    model: str,
) -> dict[str, Any]:
    center = _issue_absolute_offset(issue)
    if center is None:
        center = task.core_start
    center = min(max(center, 0), task.stream_end)
    start = max(0, center - 10)
    end = min(task.stream_end, center + 10)
    if end <= start and task.stream_end > 0:
        if start >= task.stream_end:
            start = max(0, task.stream_end - 1)
        end = min(task.stream_end, start + 1)
    return {
        "type": "llm-error",
        "offset": start,
        "length": max(0, end - start),
        "issue": {
            "code": issue.code,
            "message": issue.message,
        },
        "model": model,
    }


def _issue_absolute_offset(issue: ValidationIssue) -> int | None:
    details = issue.details or {}
    input_context = details.get("input_context")
    if isinstance(input_context, dict):
        offset = input_context.get("absolute_offset")
        if isinstance(offset, int):
            return offset
    return None


def _marker_offset(marker: dict[str, Any]) -> int | None:
    offset = marker.get("offset")
    return offset if isinstance(offset, int) else None


def _validation_context_details(
    task: ChunkTask,
    output: str,
    exc: PunctuationValidationError,
) -> dict[str, Any]:
    details: dict[str, Any] = {"context_chars": 20}
    if exc.input_index is not None:
        input_context = _text_index_context(task.input_text, exc.input_index)
        input_context["absolute_offset"] = task.context_start + exc.input_index
        details["input_context"] = input_context
    if exc.output_index is not None:
        details["output_context"] = _text_index_context(output, exc.output_index)
    return details


def _text_index_context(text: str, index: int, *, radius: int = 20) -> dict[str, Any]:
    index = min(max(index, 0), len(text))
    at_end = min(index + 1, len(text))
    return {
        "index": index,
        "before": text[max(0, index - radius):index],
        "at": text[index:at_end],
        "after": text[at_end:min(len(text), at_end + radius)],
    }


def _previous_output_files_for_retry(
    state_path: Path,
    state: dict[str, Any],
) -> list[str]:
    out: list[str] = []
    for raw in state.get("previous_output_files") or []:
        if isinstance(raw, str) and raw and raw not in out:
            out.append(raw)
    current = state_path.with_suffix(".batch-output.jsonl")
    if current.exists():
        value = str(current)
        if value not in out:
            out.append(value)
    return out


def _write_batch_text_report(
    state_path: Path,
    state: dict[str, Any],
    tasks: list[ChunkTask],
    outputs: dict[str, str],
    issues: list[ValidationIssue],
) -> Path:
    report_path = state_path.with_suffix(".batch-report.yaml")
    _write_punctuation_text_report(
        report_path,
        task_name="punctuation-batch-report",
        model=state.get("model"),
        tasks=tasks,
        outputs=outputs,
        issues=issues,
        extra={
            "state_file": str(state_path),
            "batch": state.get("batch"),
        },
    )
    return report_path


def _write_direct_text_report(
    report_path: Path,
    *,
    settings: LlmSettings,
    bundle_dir: Path,
    tasks: list[ChunkTask],
    outputs: dict[str, str],
    issues: list[ValidationIssue],
    best_effort: bool,
) -> Path:
    _write_punctuation_text_report(
        report_path,
        task_name="punctuation-direct-report",
        model=settings.model,
        tasks=tasks,
        outputs=outputs,
        issues=issues,
        extra={
            "bundle": str(bundle_dir),
            "vendor": settings.vendor,
            "best_effort": best_effort,
        },
    )
    return report_path


def _write_punctuation_text_report(
    report_path: Path,
    *,
    task_name: str,
    model: Any,
    tasks: list[ChunkTask],
    outputs: dict[str, str],
    issues: list[ValidationIssue],
    extra: dict[str, Any],
) -> None:
    issues_by_id: dict[str, list[ValidationIssue]] = {}
    unmatched: list[ValidationIssue] = []
    task_ids = {task.custom_id for task in tasks}
    for issue in issues:
        if issue.custom_id in task_ids:
            issues_by_id.setdefault(issue.custom_id, []).append(issue)
        else:
            unmatched.append(issue)
    chunks: list[dict[str, Any]] = []
    for task in tasks:
        task_issues = issues_by_id.get(task.custom_id, [])
        if task_issues:
            status = "rejected"
        elif task.custom_id in outputs:
            status = "accepted"
        else:
            status = "missing-output"
        row: dict[str, Any] = {
            "id": task.custom_id,
            "text_id": task.text_id,
            "edition": task.edition,
            "seq": task.seq,
            "bucket": task.bucket,
            "stream": task.stream,
            "core": [task.core_start, task.core_end],
            "context": [task.context_start, task.context_end],
            "status": status,
            "input_text": task.input_text,
        }
        output = outputs.get(task.custom_id)
        if output is not None:
            row["output_text"] = output
        if task_issues:
            row["issues"] = [
                _issue_report_row(issue)
                for issue in task_issues
            ]
        chunks.append(row)
    report: dict[str, Any] = {
        "schema": 1,
        "task": task_name,
        **extra,
        "model": model,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "chunks": chunks,
    }
    if unmatched:
        report["unmatched_issues"] = [
            _issue_report_row(issue, include_custom_id=True)
            for issue in unmatched
        ]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(dump(report), encoding="utf-8")


def _direct_report_path(
    bundle_dir: Path,
    settings: LlmSettings,
    run_stamp: str,
) -> Path:
    state_dir = _state_dir(settings, bundle_dir)
    filename = (
        f"punctuation-direct-{model_slug(bundle_dir.name)}-{run_stamp}-"
        f"{model_slug(settings.model)}.direct-report.yaml"
    )
    return state_dir / filename


def _issue_report_row(
    issue: ValidationIssue,
    *,
    include_custom_id: bool = False,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "code": issue.code,
        "message": issue.message,
    }
    if include_custom_id:
        row["custom_id"] = issue.custom_id
    if issue.details:
        row["details"] = issue.details
    return row


def print_batch_diagnostics(state_path: Path, batch: dict[str, Any]) -> None:
    batch_id = batch.get("id") or "<unknown>"
    status = batch.get("status") or "<unknown>"
    counts = batch.get("request_counts") or {}
    total = counts.get("total", 0)
    completed = counts.get("completed", 0)
    failed = counts.get("failed", 0)
    print(
        f"batch {batch_id} status: {status}; "
        f"requests completed={completed} failed={failed} total={total}"
    )
    for label in (
        "created_at", "validating_at", "in_progress_at", "finalizing_at",
        "completed_at", "failed_at", "expired_at", "cancelled_at",
        "expires_at",
    ):
        value = batch.get(label)
        if value is not None:
            print(f"  {label}: {value}")
    errors = batch.get("errors")
    if errors:
        print(f"  errors: {_compact_json(errors)}")
    for key, suffix in (
        ("error_file_id", ".batch-error.jsonl"),
        ("output_file_id", ".batch-output.jsonl"),
    ):
        file_id = batch.get(key)
        if isinstance(file_id, str) and file_id:
            print(f"  {key}: {file_id}")
            print(f"  saved: {state_path.with_suffix(suffix)}")
    print(f"  saved status: {state_path.with_suffix('.batch-status.yaml')}")
    report_path = state_path.with_suffix(".batch-report.yaml")
    if report_path.exists():
        print(f"  clear-text report: {report_path}")


def _save_batch_diagnostics(
    state_path: Path,
    state: dict[str, Any],
    batch: dict[str, Any],
    client: LlmClient,
) -> None:
    state = dict(state)
    state["batch"] = batch
    status_path = state_path.with_suffix(".batch-status.yaml")
    status_path.write_text(dump(state), encoding="utf-8")
    for key, suffix in (
        ("error_file_id", ".batch-error.jsonl"),
        ("output_file_id", ".batch-output.jsonl"),
    ):
        file_id = batch.get(key)
        if not isinstance(file_id, str) or not file_id:
            continue
        target = state_path.with_suffix(suffix)
        if target.exists():
            continue
        try:
            text = client.download_file_text(file_id)
        except Exception as exc:  # noqa: BLE001 - diagnostics must not mask status
            target.with_suffix(target.suffix + ".download-error.txt").write_text(
                str(exc), encoding="utf-8",
            )
            continue
        target.write_text(text, encoding="utf-8")
    tasks = [_task_from_dict(t) for t in state.get("tasks") or [] if isinstance(t, dict)]
    texts: list[str] = []
    for suffix in (".batch-output.jsonl", ".batch-error.jsonl"):
        path = state_path.with_suffix(suffix)
        if path.exists():
            texts.append(path.read_text(encoding="utf-8"))
    if tasks and texts:
        outputs, issues = parse_batch_output("\n".join(texts))
        issues = _unresolved_parse_issues(outputs, issues)
        issues.extend(_validation_issues_for_outputs(tasks, outputs))
        _write_batch_text_report(state_path, state, tasks, outputs, issues)


def _compact_json(data: Any) -> str:
    try:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        return str(data)


def build_llm_punctuation_asset(
    text_id: str,
    seq: int,
    short: str | None,
    *,
    settings: LlmSettings,
    prompt_text: str,
    markers_by_bucket: dict[str, list[dict[str, Any]]],
    chunks: list[dict[str, Any]],
    batch_id: str | None,
) -> dict[str, Any]:
    status = "complete" if all(c.get("status") == "accepted" for c in chunks) else "partial"
    asset = {
        "schema": ASSET_SCHEMA_VERSION,
        "canonical_identifier": llm_punctuation_canonical_identifier(
            text_id, seq, short, settings.model,
        ),
        "seq": seq,
        "role": REFERENCE_ROLE,
        "status": status,
        "provenance": {
            "provider": settings.vendor,
            "model": settings.model,
            "prompt_path": str(settings.prompt),
            "prompt_hash": sha256_text(prompt_text),
            "chunk_chars": settings.chunk_chars,
            "overlap": settings.overlap,
            "min_chars": settings.min_chars,
            "batch_id": batch_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        "chunks": chunks,
        "markers": {
            bucket: [
                marker_to_flow(marker)
                for marker in markers_by_bucket.get(bucket, [])
            ]
            for bucket in VALID_BUCKETS
        },
        "hash": ZERO_HASH,
    }
    asset["hash"] = reference_asset_hash(asset)
    return asset


def reference_asset_hash(asset: dict[str, Any]) -> str:
    data = copy.deepcopy(asset)
    data["hash"] = ZERO_HASH
    return sha256_jcs(data)


def llm_punctuation_asset_filename(
    text_id: str, seq: int, short: str | None, model: str,
) -> str:
    suffix = f"-{short}" if short else ""
    return f"assets/{text_id}_{seq:03d}{suffix}.{model_slug(model)}.punctuation.yaml"


def llm_punctuation_canonical_identifier(
    text_id: str, seq: int, short: str | None, model: str,
) -> str:
    edition = short or "bkk"
    return (
        f"bkk:krp/{text_id}/{edition}/v1/llm-punctuation/"
        f"{seq}/{model_slug(model)}"
    )


def model_slug(model: str) -> str:
    slug = _MODEL_SAFE_RE.sub("-", model.strip()).strip("-._")
    return slug or "model"


def scope_label(short: str | None) -> str:
    return "master" if short is None else f"edition {short}"


def _selected_bundle_dirs(
    bundle: str | Path | None,
    out_root: Path | None,
    text_id: str | None,
    text_prefix: str | None,
) -> list[Path]:
    if text_prefix is not None:
        if out_root is None:
            raise FileNotFoundError(
                "bundle root not configured; pass --out or configure global.corpus"
            )
        dirs = discover_bundles(Path(out_root), prefix=text_prefix)
        if not dirs:
            raise FileNotFoundError(
                f"no bundles found under {out_root} with prefix {text_prefix!r}"
            )
        return dirs
    return [resolve_bundle_dir(bundle=bundle, text_id=text_id, root=out_root)]


def _scope_targets(
    bundle_dir: Path,
    *,
    include_editions: bool = False,
) -> list[tuple[Path, Path, str | None]]:
    text_id = bundle_dir.name
    manifest = bundle_dir / f"{text_id}.manifest.yaml"
    if not manifest.exists():
        raise FileNotFoundError(f"master manifest not found: {manifest}")
    targets: list[tuple[Path, Path, str | None]] = [(bundle_dir, manifest, None)]
    if not include_editions:
        return targets
    editions = bundle_dir / "editions"
    if editions.is_dir():
        for sub in sorted(editions.iterdir()):
            if not sub.is_dir():
                continue
            path = sub / f"{text_id}-{sub.name}.manifest.yaml"
            if path.exists():
                targets.append((sub, path, sub.name))
    return targets


def _juan_entries(
    juan_dir: Path,
    text_id: str,
    short: str | None,
    selected_juans: set[int] | None,
) -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    for path in sorted(juan_dir.iterdir()):
        if not path.is_file():
            continue
        match = _JUAN_RE.match(path.name)
        if not match or match.group("text_id") != text_id:
            continue
        if match.group("short") != short:
            continue
        seq = int(match.group("seq"))
        if selected_juans is None or seq in selected_juans:
            out.append((seq, path))
    if not out:
        raise FileNotFoundError(f"no selected juan files found under {juan_dir}")
    return out


def _juan_path(juan_dir: Path, text_id: str, short: str | None, seq: int) -> Path:
    suffix = f"-{short}" if short else ""
    return juan_dir / f"{text_id}_{seq:03d}{suffix}.yaml"


def _voice_spans(text_len: int, markers: list[dict[str, Any]]) -> list[VoiceSpan]:
    spans: list[VoiceSpan] = []
    for marker in markers:
        if not isinstance(marker, dict) or marker.get("type") != "voice":
            continue
        name = marker.get("name")
        offset = marker.get("offset")
        length = marker.get("length")
        if not isinstance(name, str) or not isinstance(offset, int) or not isinstance(length, int):
            continue
        if length <= 0:
            continue
        start = min(max(offset, 0), text_len)
        end = min(max(offset + length, start), text_len)
        if start < end:
            marker_id = marker.get("id")
            spans.append(VoiceSpan(
                start, end, name, marker_id if isinstance(marker_id, str) else "",
            ))
    spans.sort(key=lambda s: (s.start, s.end, s.name))
    return spans


def _head_boundaries(text: str, markers: list[dict[str, Any]]) -> list[int]:
    boundaries = {0, len(text)}
    for marker in markers:
        if not isinstance(marker, dict):
            continue
        if marker.get("type") not in {"head", "tls:head"}:
            continue
        offset = marker.get("offset")
        if isinstance(offset, int) and 0 < offset < len(text):
            boundaries.add(offset)
    for span in _voice_spans(len(text), markers):
        if span.name == "head":
            boundaries.add(span.start)
            boundaries.add(span.end)
    return sorted(boundaries)


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if start >= end:
            continue
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _split_by_boundaries(
    start: int, end: int, boundaries: list[int],
) -> list[tuple[int, int]]:
    points = [start]
    points.extend(b for b in boundaries if start < b < end)
    points.append(end)
    return [(a, b) for a, b in zip(points, points[1:]) if a < b]


def _region(
    text_id: str,
    short: str | None,
    seq: int,
    bucket: str,
    stream: str,
    start: int,
    end: int,
    text: str,
) -> StreamRegion:
    return StreamRegion(
        text_id=text_id,
        edition=short,
        seq=seq,
        bucket=bucket,
        stream=stream,
        start=start,
        end=end,
        text=text[start:end],
    )


def _task_with_id(
    task: ChunkTask,
    index: int,
    juan_dir: Path,
    manifest_path: Path,
) -> ChunkTask:
    stream = model_slug(task.stream)
    edition = task.edition or "bkk"
    custom_id = (
        f"{task.text_id}:{edition}:{task.seq:03d}:"
        f"{task.bucket}:{stream}:{index}"
    )
    return ChunkTask(
        custom_id=custom_id,
        text_id=task.text_id,
        edition=task.edition,
        juan_dir=str(juan_dir),
        manifest_path=str(manifest_path),
        seq=task.seq,
        bucket=task.bucket,
        stream=task.stream,
        core_start=task.core_start,
        core_end=task.core_end,
        context_start=task.context_start,
        context_end=task.context_end,
        stream_end=task.stream_end,
        input_text=task.input_text,
    )


def _occupied_marker_ids(data: dict[str, Any], marker_asset: dict[str, Any] | None) -> set[str]:
    occupied: set[str] = set()
    for bucket in BUCKETS:
        for marker in effective_markers_for_bucket(data, bucket, marker_asset):
            marker_id = marker.get("id") if isinstance(marker, dict) else None
            if isinstance(marker_id, str) and marker_id:
                occupied.add(marker_id)
    return occupied


def _chunk_row(
    task: ChunkTask,
    *,
    status: str,
    marker_count: int = 0,
    message: str | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": task.custom_id,
        "bucket": task.bucket,
        "stream": task.stream,
        "core": [task.core_start, task.core_end],
        "context": [task.context_start, task.context_end],
        "status": status,
        "markers": marker_count,
    }
    if message:
        row["message"] = message
    return marker_to_flow(row)


def _task_to_dict(task: ChunkTask) -> dict[str, Any]:
    return task.__dict__.copy()


def _task_from_dict(data: dict[str, Any]) -> ChunkTask:
    return ChunkTask(
        custom_id=str(data["custom_id"]),
        text_id=str(data["text_id"]),
        edition=data.get("edition") if isinstance(data.get("edition"), str) else None,
        juan_dir=str(data["juan_dir"]),
        manifest_path=str(data["manifest_path"]),
        seq=int(data["seq"]),
        bucket=str(data["bucket"]),
        stream=str(data["stream"]),
        core_start=int(data["core_start"]),
        core_end=int(data["core_end"]),
        context_start=int(data["context_start"]),
        context_end=int(data["context_end"]),
        stream_end=int(data.get("stream_end", data["context_end"])),
        input_text=str(data["input_text"]),
    )


def _in_owned_core(
    offset: int,
    core_start: int,
    core_end: int,
    include_core_end: bool,
) -> bool:
    if core_start <= offset < core_end:
        return True
    return include_core_end and offset == core_end


def _batch_request_line(task: ChunkTask, model: str, prompt_text: str) -> str:
    row = {
        "custom_id": task.custom_id,
        "method": "POST",
        "url": "/v1/responses",
        "body": {
            "model": model,
            "input": [
                {"role": "system", "content": prompt_text},
                {"role": "user", "content": task.input_text},
            ],
        },
    }
    return json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"


def _update_manifest_references(
    manifest_path: Path,
    marker_hashes: dict[int, tuple[str, str]],
    model: str,
) -> None:
    manifest = _yaml_load(manifest_path)
    assets = manifest.setdefault("assets", {})
    references = assets.get("references") or []
    kept: list[Any] = []
    model_value = model
    for entry in references:
        if (
            isinstance(entry, dict)
            and entry.get("role") == REFERENCE_ROLE
            and entry.get("model") == model_value
            and isinstance(entry.get("seq"), int)
            and entry.get("seq") in marker_hashes
        ):
            continue
        kept.append(entry)
    for seq, (filename, hash_value) in sorted(marker_hashes.items()):
        kept.append(marker_to_flow({
            "seq": seq,
            "role": REFERENCE_ROLE,
            "model": model_value,
            "filename": filename,
            "hash": hash_value,
        }))
    assets["references"] = kept
    reflow_manifest(manifest)
    manifest["hash"] = manifest_hash(manifest)
    manifest_path.write_text(dump(manifest), encoding="utf-8")


def _extract_response_text(data: Any) -> str:
    if not isinstance(data, dict):
        raise ValueError("response body is not a mapping")
    output_text = data.get("output_text")
    if isinstance(output_text, str):
        return output_text
    pieces: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                pieces.append(text)
    if pieces:
        return "".join(pieces)
    raise ValueError("response body did not contain output text")


def _to_plain_data(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return {
        k: getattr(obj, k)
        for k in dir(obj)
        if not k.startswith("_") and isinstance(getattr(obj, k), (str, int, float, bool, list, dict, type(None)))
    }


def _yaml_load(path: Path) -> dict[str, Any]:
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=_YAML_LOADER)
    if not isinstance(data, dict):
        raise RuntimeError(f"{path}: top-level YAML is not a mapping")
    return data


def _resolve_prompt_path(raw: Path | str | None) -> Path:
    if raw is not None:
        return Path(raw).expanduser()
    return _default_prompt_path()


def _default_prompt_path() -> Path:
    module_root = Path(__file__).resolve().parents[2]
    preferred = module_root / "prompts" / "punctuation"
    if preferred.exists():
        return preferred
    return module_root / "prompts" / "punctuate.txt"


def _read_prompt(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"prompt file not found: {path}")
    return path.read_text(encoding="utf-8").rstrip() + "\n\nText to punctuate:"


def _state_dir(settings: LlmSettings, first_bundle_dir: Path) -> Path:
    if settings.cache_dir is not None:
        return settings.cache_dir
    return first_bundle_dir / ".bkk-llm"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _positive_int(value: Any, default: int, label: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a positive integer")
    out = int(value)
    if out <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return out


def _non_negative_int(value: Any, default: int, label: str) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a non-negative integer")
    out = int(value)
    if out < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return out


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
