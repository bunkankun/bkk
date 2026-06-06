"""Stable YAML I/O for one-document-per-file records.

The single source of truth for how bkk-core writes records. Routing every
record through this module keeps byte-level output stable so that a
read-modify-write of an unchanged record is a no-op diff.

Rules enforced by :func:`dumps_record`:

1. Key order is preserved as built (``sort_keys=False``).
2. Block style only (``default_flow_style=False``).
3. Quote only when YAML semantics would otherwise misparse the scalar
   (e.g. bare ``y`` / ``n`` would round-trip as booleans).
4. ``allow_unicode=True`` — CJK / accented Latin render as themselves.
5. 2-space indent, ``width=10**6`` (no wrapping on long single-line values).
6. No anchors / aliases.
7. Output ends with exactly one trailing newline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml


_AMBIGUOUS_SCALARS = frozenset({
    "", "y", "Y", "n", "N",
    "yes", "Yes", "YES", "no", "No", "NO",
    "true", "True", "TRUE", "false", "False", "FALSE",
    "on", "On", "ON", "off", "Off", "OFF",
    "null", "Null", "NULL", "~",
})


def _represent_str(dumper: yaml.SafeDumper, data: str):
    """Quote only strings that YAML would otherwise misparse on round-trip."""
    style: str | None = None
    if data in _AMBIGUOUS_SCALARS:
        style = "'"
    elif data and data[0] in "*&!|>%@`":
        style = "'"
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


class _RecordDumper(yaml.SafeDumper):
    """SafeDumper that refuses anchors and quotes ambiguous scalars."""

    def ignore_aliases(self, data: Any) -> bool:  # noqa: ARG002
        return True


_RecordDumper.add_representer(str, _represent_str)


def loads_record(text: str) -> dict[str, Any]:
    """Parse one YAML record from ``text``.

    Empty or non-mapping documents return ``{}`` (permissive, matching the
    historical behavior of ``parse_frontmatter``).
    """
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def load_record(path: Path) -> dict[str, Any]:
    """Load one YAML record from ``path``."""
    return loads_record(path.read_text(encoding="utf-8"))


def dumps_record(data: Mapping[str, Any]) -> str:
    """Serialize ``data`` to a stable YAML string."""
    return yaml.dump(
        dict(data),
        Dumper=_RecordDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=10**6,
        indent=2,
    )


def dump_record(path: Path, data: Mapping[str, Any]) -> None:
    """Serialize ``data`` to ``path``, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_record(data), encoding="utf-8")
