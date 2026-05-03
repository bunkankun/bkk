"""Recipe-file loader for the BKK exporter.

A recipe is a small YAML file that names what to export and where. v1 is
deliberately minimal — three required keys, no others. Unknown keys raise
to keep the door open for future fields without silent typos.

    format: tls
    bundle: ./output/KR6q0053
    output_dir: ./exports/KR6q0053

The KRP exporter accepts a few optional knobs that shape the on-disk layout::

    format: krp
    bundle: ./output/KR3a0013
    output_dir: ./exports/KR3a0013
    shape: dirs        # dirs | git | single   (default: dirs)
    edition: WYG       # required iff shape: single
    mode: split        # split | concat        (default: split)
    editions: [WYG]    # optional filter; default: all editions in the bundle
    juans: [1, 2]      # optional seq filter; default: all juans
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


_REQUIRED = {"format", "bundle", "output_dir"}
_KRP_OPTIONAL = {"shape", "edition", "mode", "editions", "juans"}
_KNOWN = _REQUIRED | _KRP_OPTIONAL
_SUPPORTED_FORMATS = {"tls", "krp"}
_SHAPES = {"dirs", "git", "single"}
_MODES = {"split", "concat"}


@dataclass
class Recipe:
    format: str
    bundle: Path
    output_dir: Path
    source_path: Path  # path to the recipe file itself, for error messages
    # KRP-specific knobs (ignored for other formats).
    shape: str = "dirs"
    edition: str | None = None
    mode: str = "split"
    editions: list[str] | None = None
    juans: list[int] | None = None


class RecipeError(ValueError):
    """Raised for malformed recipes."""


def load_recipe(path: Path) -> Recipe:
    """Load and validate a recipe file. Paths in the recipe are resolved
    relative to the recipe file's directory."""
    if not path.exists():
        raise RecipeError(f"recipe not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RecipeError(f"recipe at {path} must be a YAML mapping")

    keys = set(raw.keys())
    missing = _REQUIRED - keys
    if missing:
        raise RecipeError(
            f"recipe at {path} is missing required keys: {sorted(missing)}"
        )
    unknown = keys - _KNOWN
    if unknown:
        raise RecipeError(
            f"recipe at {path} has unknown keys: {sorted(unknown)} "
            f"(known: {sorted(_KNOWN)})"
        )

    fmt = raw["format"]
    if fmt not in _SUPPORTED_FORMATS:
        raise RecipeError(
            f"recipe at {path} requests unsupported format: {fmt!r} "
            f"(supported: {sorted(_SUPPORTED_FORMATS)})"
        )

    krp_keys = (keys & _KRP_OPTIONAL)
    if fmt != "krp" and krp_keys:
        raise RecipeError(
            f"recipe at {path} uses krp-only keys {sorted(krp_keys)} "
            f"with format {fmt!r}"
        )

    shape = raw.get("shape", "dirs")
    if shape not in _SHAPES:
        raise RecipeError(
            f"recipe at {path} has invalid shape {shape!r} "
            f"(supported: {sorted(_SHAPES)})"
        )
    mode = raw.get("mode", "split")
    if mode not in _MODES:
        raise RecipeError(
            f"recipe at {path} has invalid mode {mode!r} "
            f"(supported: {sorted(_MODES)})"
        )

    edition = raw.get("edition")
    if shape == "single" and not edition:
        raise RecipeError(
            f"recipe at {path}: shape: single requires `edition:`"
        )
    if shape != "single" and edition:
        raise RecipeError(
            f"recipe at {path}: `edition:` is only valid with shape: single"
        )

    editions = raw.get("editions")
    if editions is not None:
        if not isinstance(editions, list) or not all(
            isinstance(e, str) for e in editions
        ):
            raise RecipeError(
                f"recipe at {path}: `editions` must be a list of strings"
            )

    juans = raw.get("juans")
    if juans is not None:
        if not isinstance(juans, list) or not all(
            isinstance(j, int) for j in juans
        ):
            raise RecipeError(
                f"recipe at {path}: `juans` must be a list of integers"
            )

    base = path.parent
    bundle = (base / str(raw["bundle"])).resolve()
    output_dir = (base / str(raw["output_dir"])).resolve()
    return Recipe(
        format=fmt, bundle=bundle, output_dir=output_dir, source_path=path,
        shape=shape, edition=edition, mode=mode,
        editions=editions, juans=juans,
    )
