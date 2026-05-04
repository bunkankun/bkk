"""Walk a recipe's pins and assemble the response shape for ``/recipes:fulfil``.

Each pin in the request is processed independently:

1. Identify the bundle by ``textid`` (preferred) or ``canonical_identifier``,
   resolving collisions with the same UX as :mod:`routers.texts`
   (prefer the candidate without a ``base_edition``).
2. If the pin supplied a ``hash``, compare it against the bundle's current
   manifest hash; mismatch becomes a per-pin error and the slice is skipped.
3. Apply the pin's ``selection`` via :func:`selection.apply_selection`.
4. Echo the resolved pin (with ``textid`` / ``canonical_identifier`` / ``hash``
   filled in from the snapshot) into ``resolved_recipe.pins``.

Errors are *not* propagated as HTTP 4xx/5xx; the request as a whole succeeds
as long as the recipe parsed. Per-pin failures are recorded in the response's
``errors`` list and the corresponding ``results`` entry carries ``error`` and
``content=None``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException

from . import selection
from .resolver import BundleRef, IdentifierResolver
from .schemas import (
    FulfilResponse,
    FulfilResult,
    JuanSliceOut,
    RecipePin,
    RecipeRequest,
)


def _resolve_pin(
    pin: RecipePin, resolver: IdentifierResolver
) -> tuple[BundleRef | None, dict[str, Any] | None]:
    """Return ``(ref, error)`` where exactly one is non-None."""
    if pin.textid:
        candidates = resolver.lookup(pin.textid)
        for c in candidates:
            if c.textid == pin.textid:
                return c, None
        return None, {
            "error": "pin_textid_not_found",
            "textid": pin.textid,
        }

    if pin.canonical_identifier:
        candidates = resolver.lookup(pin.canonical_identifier)
        if not candidates:
            return None, {
                "error": "pin_identifier_not_found",
                "canonical_identifier": pin.canonical_identifier,
            }
        chosen = resolver.disambiguate(candidates)
        if chosen is None:
            return None, {
                "error": "pin_identifier_ambiguous",
                "canonical_identifier": pin.canonical_identifier,
                "candidates": [c.textid for c in candidates],
            }
        return chosen, None

    return None, {"error": "pin_missing_identifier"}


def _slice_to_out(sl: selection.JuanSlice, textid: str) -> JuanSliceOut:
    return JuanSliceOut(
        textid=textid,
        juan_seq=sl.juan_seq,
        bucket=sl.bucket,
        span=[sl.span[0], sl.span[1]],
        text=sl.text,
        markers=sl.markers,
    )


def _manifest_hash(corpus_root: Path, textid: str) -> str | None:
    manifest = selection.load_manifest(corpus_root, textid)
    h = manifest.get("hash")
    return h if isinstance(h, str) else None


def fulfil(
    request: RecipeRequest,
    *,
    resolver: IdentifierResolver,
    corpus_root: Path,
) -> FulfilResponse:
    """Fulfil a recipe request; never raises on per-pin failures."""
    results: list[FulfilResult] = []
    errors: list[dict[str, Any]] = []
    resolved_pins: list[dict[str, Any]] = []

    for idx, pin in enumerate(request.pins):
        ref, resolve_err = _resolve_pin(pin, resolver)

        if resolve_err is not None or ref is None:
            err = dict(resolve_err or {"error": "unresolvable_pin"})
            err["pin_index"] = idx
            errors.append(err)
            results.append(FulfilResult(
                pin_index=idx,
                role=pin.role,
                textid=pin.textid,
                canonical_identifier=pin.canonical_identifier,
                selection=pin.selection,
                content=None,
                verified=False,
                manifest_hash=None,
                error=err,
            ))
            resolved_pins.append(pin.model_dump(exclude_none=True))
            continue

        try:
            current_hash = _manifest_hash(corpus_root, ref.textid)
        except HTTPException as exc:
            err = {
                "error": "manifest_unreadable",
                "pin_index": idx,
                "textid": ref.textid,
                "detail": exc.detail,
            }
            errors.append(err)
            results.append(FulfilResult(
                pin_index=idx, role=pin.role, textid=ref.textid,
                canonical_identifier=ref.canonical_identifier,
                selection=pin.selection, content=None,
                verified=False, manifest_hash=None, error=err,
            ))
            resolved_pins.append({
                **pin.model_dump(exclude_none=True),
                "textid": ref.textid,
                "canonical_identifier": ref.canonical_identifier,
            })
            continue

        verified = True
        hash_err: dict[str, Any] | None = None
        if pin.hash and current_hash and pin.hash != current_hash:
            verified = False
            hash_err = {
                "error": "hash_mismatch",
                "pin_index": idx,
                "textid": ref.textid,
                "expected": pin.hash,
                "actual": current_hash,
            }
            errors.append(hash_err)

        resolved_entry: dict[str, Any] = {
            **pin.model_dump(exclude_none=True),
            "textid": ref.textid,
            "canonical_identifier": ref.canonical_identifier,
        }
        if current_hash:
            resolved_entry["hash"] = current_hash
        resolved_pins.append(resolved_entry)

        if hash_err is not None:
            results.append(FulfilResult(
                pin_index=idx, role=pin.role, textid=ref.textid,
                canonical_identifier=ref.canonical_identifier,
                selection=pin.selection, content=None,
                verified=False, manifest_hash=current_hash, error=hash_err,
            ))
            continue

        try:
            sliced = selection.apply_selection(
                pin.selection, corpus_root=corpus_root, textid=ref.textid,
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
            err = {**detail, "pin_index": idx, "textid": ref.textid}
            errors.append(err)
            results.append(FulfilResult(
                pin_index=idx, role=pin.role, textid=ref.textid,
                canonical_identifier=ref.canonical_identifier,
                selection=pin.selection, content=None,
                verified=verified, manifest_hash=current_hash, error=err,
            ))
            continue

        if isinstance(sliced, list):
            content: JuanSliceOut | list[JuanSliceOut] = [
                _slice_to_out(s, ref.textid) for s in sliced
            ]
        else:
            content = _slice_to_out(sliced, ref.textid)

        results.append(FulfilResult(
            pin_index=idx,
            role=pin.role,
            textid=ref.textid,
            canonical_identifier=ref.canonical_identifier,
            selection=pin.selection,
            content=content,
            verified=verified,
            manifest_hash=current_hash,
            error=None,
        ))

    return FulfilResponse(
        resolved_recipe={"pins": resolved_pins},
        results=results,
        errors=errors,
    )
