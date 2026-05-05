# bkk.data

Static runtime data shipped with the `bkk` wheel. Drop new files here when the package needs to know a fact at runtime that is not derivable from the corpus itself (taxonomy labels, default sort orders, seed dictionaries, …).

## Conventions

- **YAML** for hand-edited data — matches the rest of the bundle ecosystem.
- **JSON** for machine-generated artefacts (e.g. an Unihan slice).
- Each file should carry a top-level `_provenance` block (source, retrieval date, brief note) so future maintainers know what to update against.

## Access

```python
from importlib.resources import files
import yaml

data = yaml.safe_load(
    files("bkk.data").joinpath("kr_categories.yaml").read_text("utf-8")
)
```

Cache at module level (`functools.lru_cache(maxsize=1)` or a module constant) — these files don't change at runtime.

## Packaging

Inclusion in the wheel is declared in [module/pyproject.toml](../../pyproject.toml) under `[tool.setuptools.package-data]`. New file extensions need adding there.
