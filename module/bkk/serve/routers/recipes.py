"""POST ``/recipes:fulfil`` — assemble a recipe into juan slices.

Accepts both JSON (``application/json``) and YAML (``application/yaml`` or
``application/x-yaml``) request bodies. Recipes live as YAML on disk so YAML
ingestion lets clients ``POST -d @recipe.yaml`` without a translation step.

The handler never raises 4xx/5xx for per-pin failures — those land in
``response.errors`` and the corresponding ``results[i].error``. It does raise
400 for an unparseable body or a top-level shape that isn't ``{pins: [...]}``.
"""

from __future__ import annotations

import yaml
from fastapi import APIRouter, Request
from pydantic import ValidationError

from .. import errors
from ..recipe_fulfil import fulfil
from ..schemas import FulfilResponse, RecipeRequest

router = APIRouter(tags=["recipes"])


_YAML_TYPES = {"application/yaml", "application/x-yaml", "text/yaml", "text/x-yaml"}


def _content_type(request: Request) -> str:
    raw = request.headers.get("content-type", "application/json")
    return raw.split(";", 1)[0].strip().lower()


async def _parse_body(request: Request) -> RecipeRequest:
    ctype = _content_type(request)
    raw = await request.body()
    if not raw:
        raise errors.bad_request("empty_body")
    try:
        if ctype in _YAML_TYPES:
            data = yaml.safe_load(raw)
        else:
            import json
            data = json.loads(raw)
    except (yaml.YAMLError, ValueError) as exc:
        raise errors.bad_request(
            "bad_request_body", content_type=ctype, parse_error=str(exc)
        )
    if not isinstance(data, dict):
        raise errors.bad_request("recipe_not_object", got=type(data).__name__)
    try:
        return RecipeRequest.model_validate(data)
    except ValidationError as exc:
        raise errors.bad_request("recipe_invalid", validation_errors=exc.errors())


@router.post(
    "/recipes:fulfil",
    response_model=FulfilResponse,
    summary="Materialize a recipe: resolve, hash-verify, slice each pin",
    description=(
        "Body is a recipe of the form ``{pins: [...]}``. Accepts "
        "``application/json`` or ``application/yaml``. Per-pin errors "
        "(unresolved identifier, hash mismatch, bad selection) are reported "
        "in the response's ``errors`` list — a request only fails outright "
        "when the body itself is malformed."
    ),
)
async def fulfil_recipe(request: Request) -> FulfilResponse:
    body = await _parse_body(request)
    state = request.app.state.bkk
    return fulfil(
        body,
        resolver=state.resolver,
        corpus_root=state.corpus_root,
    )
