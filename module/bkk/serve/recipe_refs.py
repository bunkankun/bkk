"""Short recipe reference parsing.

The recipe API stores canonical pins as ``textid`` plus ``selection``.  Short
refs are an authoring convenience that are normalized into that shape before
Pydantic validates a recipe request.
"""

from __future__ import annotations

from typing import Any

from bkk.short_refs import parse_short_ref as _parse_short_ref


def parse_short_ref(ref: str) -> tuple[str, dict[str, Any]]:
    """Parse a recipe reference, preserving the recipe-facing error text."""
    try:
        return _parse_short_ref(ref)
    except ValueError as exc:
        raise ValueError(f"invalid recipe ref {ref!r}") from exc


def normalize_recipe_refs(data: Any) -> Any:
    """Return a copy of recipe request data with pin ``ref`` values expanded."""
    if not isinstance(data, dict):
        return data
    pins = data.get("pins")
    if not isinstance(pins, list):
        return data

    normalized = dict(data)
    out: list[Any] = []
    for idx, pin in enumerate(pins):
        if not isinstance(pin, dict) or "ref" not in pin:
            out.append(pin)
            continue

        ref = pin.get("ref")
        if not isinstance(ref, str):
            raise ValueError(f"pin {idx} ref must be a string")
        if pin.get("textid") is not None:
            raise ValueError(f"pin {idx} ref cannot be combined with textid")
        if pin.get("canonical_identifier") is not None:
            raise ValueError(
                f"pin {idx} ref cannot be combined with canonical_identifier"
            )
        if pin.get("selection") is not None:
            raise ValueError(f"pin {idx} ref cannot be combined with selection")

        textid, selection = parse_short_ref(ref)
        expanded = {k: v for k, v in pin.items() if k != "ref"}
        expanded["textid"] = textid
        expanded["selection"] = selection
        out.append(expanded)

    normalized["pins"] = out
    return normalized
