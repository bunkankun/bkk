"""Render BKK recipes with controlled Jinja templates.

This is intentionally small for v1: recipes may name pins, collect marker
datasets from those pins, and render Markdown through a sandboxed Jinja
environment. The existing ``/recipes:fulfil`` implementation remains the
source of truth for resolving pins and applying selections.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import ValidationError

from bkk.serve.recipe_fulfil import fulfil
from bkk.serve.resolver import CorpusCache, IdentifierResolver
from bkk.serve.schemas import JuanSliceOut, RecipeRequest
from bkk.serve.selection import load_manifest


class RecipeRenderError(RuntimeError):
    """Raised when a render recipe cannot be parsed or rendered."""


@dataclass(frozen=True)
class RenderedRecipe:
    text: str
    context: dict[str, Any]


def load_recipe(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise RecipeRenderError(f"recipe YAML is invalid: {exc}") from exc
    if not isinstance(data, dict):
        raise RecipeRenderError("recipe must be a YAML mapping")
    return data


def render_recipe_file(path: Path, *, corpus_root: Path) -> RenderedRecipe:
    return render_recipe(load_recipe(path), corpus_root=corpus_root)


def render_recipe(recipe: dict[str, Any], *, corpus_root: Path) -> RenderedRecipe:
    pins_raw = recipe.get("pins")
    if not isinstance(pins_raw, list) or not pins_raw:
        raise RecipeRenderError("render recipe requires a non-empty pins list")

    render = recipe.get("render")
    if not isinstance(render, dict):
        raise RecipeRenderError("render recipe requires a render mapping")
    fmt = render.get("format", "markdown")
    if fmt != "markdown":
        raise RecipeRenderError(f"unsupported render.format {fmt!r}; expected 'markdown'")
    template = render.get("template")
    if not isinstance(template, str) or not template.strip():
        raise RecipeRenderError("render.template must be a non-empty string")

    try:
        request = RecipeRequest.model_validate({"pins": pins_raw})
    except ValidationError as exc:
        raise RecipeRenderError(f"recipe pins are invalid: {exc}") from exc
    resolver = IdentifierResolver(CorpusCache(corpus_root))
    fulfilled = fulfil(request, resolver=resolver, corpus_root=corpus_root)

    pin_context = _pin_contexts(pins_raw, fulfilled.results, corpus_root)
    pin_names = {
        raw["name"]: idx
        for idx, raw in enumerate(pins_raw)
        if isinstance(raw, dict) and isinstance(raw.get("name"), str)
    }
    datasets = _build_datasets(recipe.get("datasets") or {}, fulfilled.results, pin_names)
    context = {
        "kind": recipe.get("kind"),
        "pins": pin_context,
        "datasets": datasets,
        "errors": fulfilled.errors,
        "resolved_recipe": fulfilled.resolved_recipe,
    }

    env = SandboxedEnvironment(
        autoescape=False,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    try:
        rendered = env.from_string(template).render(**context)
    except TemplateError as exc:
        raise RecipeRenderError(f"template render failed: {exc}") from exc
    return RenderedRecipe(text=rendered, context=context)


def _pin_contexts(
    pins_raw: list[Any], results: list[Any], corpus_root: Path,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for idx, raw in enumerate(pins_raw):
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            continue
        result = results[idx] if idx < len(results) else None
        textid = getattr(result, "textid", None)
        title = None
        if textid:
            try:
                manifest = load_manifest(corpus_root, textid)
                metadata = manifest.get("metadata") or {}
                title = metadata.get("title") if isinstance(metadata, dict) else None
            except Exception:
                title = None
        out[name] = {
            "name": name,
            "role": raw.get("role"),
            "label": title or textid or name,
            "textid": textid,
            "canonical_identifier": getattr(result, "canonical_identifier", None),
            "selection": getattr(result, "selection", None),
            "verified": getattr(result, "verified", False),
            "manifest_hash": getattr(result, "manifest_hash", None),
            "error": getattr(result, "error", None),
        }
    return out


def _build_datasets(
    datasets_spec: Any, results: list[Any], pin_names: dict[str, int],
) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(datasets_spec, dict):
        raise RecipeRenderError("datasets must be a mapping")
    out: dict[str, list[dict[str, Any]]] = {}
    for name, spec in datasets_spec.items():
        if not isinstance(name, str) or not name:
            raise RecipeRenderError("dataset names must be non-empty strings")
        if not isinstance(spec, dict):
            raise RecipeRenderError(f"dataset {name!r} must be a mapping")
        if spec.get("collect") != "markers":
            raise RecipeRenderError(
                f"dataset {name!r} has unsupported collect={spec.get('collect')!r}"
            )
        source_name = spec.get("from")
        if not isinstance(source_name, str) or not source_name:
            raise RecipeRenderError(f"dataset {name!r} requires a named 'from' pin")
        if source_name not in pin_names:
            raise RecipeRenderError(
                f"dataset {name!r} references unknown pin {source_name!r}"
            )
        result = results[pin_names[source_name]]
        items = _collect_markers(result, spec)
        out[name] = items
    return out


def _collect_markers(result: Any, spec: dict[str, Any]) -> list[dict[str, Any]]:
    if result.error is not None or result.content is None:
        return []
    where = spec.get("where") or {}
    if where is not None and not isinstance(where, dict):
        raise RecipeRenderError("dataset where must be a mapping")
    want_type = where.get("type") if isinstance(where, dict) else None
    include_text = bool(spec.get("include_text", False))
    context = spec.get("context", 0)
    if not isinstance(context, int) or context < 0:
        raise RecipeRenderError("dataset context must be a non-negative integer")

    slices = result.content if isinstance(result.content, list) else [result.content]
    out: list[dict[str, Any]] = []
    for sl in slices:
        if not isinstance(sl, JuanSliceOut):
            sl = JuanSliceOut.model_validate(sl)
        for marker in sl.markers:
            if want_type is not None and marker.get("type") != want_type:
                continue
            item = _marker_item(result, sl, marker, include_text, context)
            out.append(item)
    out.sort(key=lambda it: (
        str(it.get("textid") or ""),
        int(it.get("juan_seq") or 0),
        {"front": 0, "body": 1, "back": 2}.get(str(it.get("bucket")), 9),
        int(it.get("offset") or 0),
        str(it.get("id") or ""),
    ))
    return out


def _marker_item(
    result: Any,
    sl: JuanSliceOut,
    marker: dict[str, Any],
    include_text: bool,
    context: int,
) -> dict[str, Any]:
    rel_offset = marker.get("offset")
    length = marker.get("length", 0)
    if not isinstance(rel_offset, int):
        rel_offset = 0
    if not isinstance(length, int):
        length = 0
    abs_offset = sl.span[0] + rel_offset
    item = dict(marker)
    item.update({
        "textid": result.textid or sl.textid,
        "juan_seq": sl.juan_seq,
        "bucket": sl.bucket,
        "offset": abs_offset,
        "relative_offset": rel_offset,
        "length": length,
        "end": abs_offset + length,
        "responds_to": marker.get("responds-to"),
    })
    if include_text:
        start = max(0, rel_offset)
        end = min(len(sl.text), start + max(0, length))
        item["text"] = sl.text[start:end]
        item["left"] = sl.text[max(0, start - context):start]
        item["right"] = sl.text[end:end + context]
    return item
