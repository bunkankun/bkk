"""Load and merge .bkkrc configuration files.

Search order: ~/.bkkrc (lowest priority) → intermediate dirs → cwd/.bkkrc (highest).
All files are deep-merged per section so that, e.g., ~/.bkkrc credentials carry through
even when a project-specific file overrides other keys.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

_VALID_SECTIONS = {
    "global", "import", "export", "index", "validate", "serve", "repair",
    "voice", "recipe", "info", "cbeta", "core",
}

_PATH_KEYS = frozenset(
    {"corpus", "tls_root", "krp_root", "in", "out", "output_dir",
     "cache_dir", "tls_source", "web_dist", "index", "catalog",
     "cbeta_root",
     "root", "mapping",
     "annotations_out", "annotations_root"}
)


def _collect_rc_files() -> list[Path]:
    """Return existing .bkkrc paths ordered from home (lowest priority) to cwd (highest)."""
    home = Path.home()
    cwd = Path.cwd().resolve()

    try:
        rel = cwd.relative_to(home)
        dirs: list[Path] = [home]
        for i in range(len(rel.parts)):
            dirs.append(home.joinpath(*rel.parts[: i + 1]))
    except ValueError:
        # cwd is outside the home tree — use home + cwd only
        dirs = [home, cwd]

    return [d / ".bkkrc" for d in dirs if (d / ".bkkrc").is_file()]


def _resolve_section_paths(section: dict, rc_dir: Path) -> dict:
    """Expand ~ and resolve relative path strings for known path keys."""
    result = {}
    for k, v in section.items():
        if k in _PATH_KEYS and isinstance(v, str):
            p = Path(v).expanduser()
            if not p.is_absolute():
                p = (rc_dir / p).resolve()
            result[k] = p
        else:
            result[k] = v
    return result


def rc_files() -> list[Path]:
    """Public accessor for the .bkkrc files that ``load_rc()`` would consume."""
    return _collect_rc_files()


def load_rc() -> dict:
    """Load and deep-merge all .bkkrc files; return {} if none found."""
    files = _collect_rc_files()
    merged: dict[str, dict] = {}

    for rc_path in files:
        with rc_path.open() as fh:
            data = yaml.safe_load(fh) or {}

        unknown = set(data) - _VALID_SECTIONS
        if unknown:
            sys.exit(
                f"error: {rc_path}: unknown section(s) {sorted(unknown)!r}; "
                f"valid sections are {sorted(_VALID_SECTIONS)!r}"
            )

        rc_dir = rc_path.parent
        for section, values in data.items():
            if not isinstance(values, dict):
                sys.exit(
                    f"error: {rc_path}: section [{section}] must be a mapping"
                )
            resolved = _resolve_section_paths(values, rc_dir)
            merged.setdefault(section, {}).update(resolved)

    return merged
