"""Recipe-file loader for the BKK exporter.

A recipe is a small YAML file that names what to export and where, but every
field is optional — the loader's job is to parse what's present and validate
internal consistency, while the CLI is responsible for ensuring the final
Recipe has the fields it needs to dispatch (`format`, `bundle`, `output_dir`).

A fully-pinned single-text recipe::

    format: tls
    bundle: ./output/KR6q0053
    output_dir: ./exports/KR6q0053

A generic KRP recipe (used together with ``--bundle`` / ``--output-dir`` or
``--corpus`` on the CLI)::

    format: krp
    shape: single        # dirs | git | single   (default: dirs)
    edition: WYG         # required iff shape: single
    mode: split          # split | concat        (default: split)

Unknown top-level keys still raise so silent typos don't get a free pass.

Optional KRP knobs (``shape``, ``edition``, ``mode``, ``editions``,
``juans``) are only valid when ``format: krp`` is set somewhere — either in
the file or as a CLI override applied via :func:`apply_overrides`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import yaml


_KRP_OPTIONAL = {"shape", "edition", "mode", "editions", "juans"}
_KNOWN = {"format", "bundle", "output_dir"} | _KRP_OPTIONAL
_SUPPORTED_FORMATS = {"tls", "krp"}
_SHAPES = {"dirs", "git", "single"}
_MODES = {"split", "concat"}


@dataclass
class Recipe:
    format: str | None = None
    bundle: Path | None = None
    output_dir: Path | None = None
    source_path: Path | None = None  # path to the recipe file itself, if any
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
    relative to the recipe file's directory. Every field is optional; the
    caller must layer in any missing required fields (typically via
    :func:`apply_overrides`) before dispatching."""
    if not path.exists():
        raise RecipeError(f"recipe not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise RecipeError(f"recipe at {path} must be a YAML mapping")

    keys = set(raw.keys())
    unknown = keys - _KNOWN
    if unknown:
        raise RecipeError(
            f"recipe at {path} has unknown keys: {sorted(unknown)} "
            f"(known: {sorted(_KNOWN)})"
        )

    fmt = raw.get("format")
    if fmt is not None and fmt not in _SUPPORTED_FORMATS:
        raise RecipeError(
            f"recipe at {path} requests unsupported format: {fmt!r} "
            f"(supported: {sorted(_SUPPORTED_FORMATS)})"
        )

    krp_keys = (keys & _KRP_OPTIONAL)
    if fmt is not None and fmt != "krp" and krp_keys:
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
    # The shape↔edition consistency check is deferred to apply_overrides /
    # _validate_executable so a generic recipe can omit `edition:` and have
    # the CLI fill it in.
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
        if shape == "git":
            raise RecipeError(
                f"recipe at {path}: `editions:` is not supported with "
                f"shape: git (shape: git always emits all editions)"
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
    bundle = (base / str(raw["bundle"])).resolve() if "bundle" in raw else None
    output_dir = (
        (base / str(raw["output_dir"])).resolve() if "output_dir" in raw
        else None
    )
    return Recipe(
        format=fmt, bundle=bundle, output_dir=output_dir, source_path=path,
        shape=shape, edition=edition, mode=mode,
        editions=editions, juans=juans,
    )


def apply_overrides(
    recipe: Recipe | None, *,
    format: str | None = None,
    bundle: Path | None = None,
    output_dir: Path | None = None,
    shape: str | None = None,
    edition: str | None = None,
    mode: str | None = None,
    editions: list[str] | None = None,
    juans: list[int] | None = None,
) -> Recipe:
    """Layer CLI overrides on top of a (possibly missing) recipe.

    CLI-supplied paths are resolved against cwd. Returns a Recipe ready
    to dispatch and raises :class:`RecipeError` if the result is missing
    required fields or is internally inconsistent.
    """
    base = recipe if recipe is not None else Recipe()
    merged = replace(
        base,
        format=format if format is not None else base.format,
        bundle=bundle.resolve() if bundle is not None else base.bundle,
        output_dir=(
            output_dir.resolve() if output_dir is not None else base.output_dir
        ),
        shape=shape if shape is not None else base.shape,
        edition=edition if edition is not None else base.edition,
        mode=mode if mode is not None else base.mode,
        editions=editions if editions is not None else base.editions,
        juans=juans if juans is not None else base.juans,
    )
    _validate_executable(merged)
    return merged


def _validate_executable(recipe: Recipe) -> None:
    """Final consistency check before dispatch."""
    if recipe.format is None:
        raise RecipeError("no format set: pass --format or set it in the recipe")
    if recipe.format not in _SUPPORTED_FORMATS:
        raise RecipeError(
            f"unsupported format: {recipe.format!r} "
            f"(supported: {sorted(_SUPPORTED_FORMATS)})"
        )
    if recipe.bundle is None:
        raise RecipeError(
            "no bundle set: pass --bundle / --corpus or set `bundle:` in the recipe"
        )
    if recipe.output_dir is None:
        raise RecipeError(
            "no output_dir set: pass --output-dir or set `output_dir:` in the recipe"
        )
    if recipe.shape not in _SHAPES:
        raise RecipeError(
            f"invalid shape {recipe.shape!r} (supported: {sorted(_SHAPES)})"
        )
    if recipe.mode not in _MODES:
        raise RecipeError(
            f"invalid mode {recipe.mode!r} (supported: {sorted(_MODES)})"
        )

    krp_set = (
        recipe.edition is not None
        or recipe.editions is not None
        or recipe.juans is not None
        or recipe.shape != "dirs"
        or recipe.mode != "split"
    )
    if recipe.format != "krp" and krp_set:
        raise RecipeError(
            f"krp-only options used with format {recipe.format!r}"
        )

    if recipe.shape == "single" and not recipe.edition:
        raise RecipeError("shape: single requires `edition:`")
    if recipe.shape != "single" and recipe.edition:
        raise RecipeError("`edition:` is only valid with shape: single")
    if recipe.shape == "git" and recipe.editions is not None:
        raise RecipeError(
            "`editions:` is not supported with shape: git "
            "(shape: git always emits all editions)"
        )
