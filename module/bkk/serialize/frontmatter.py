"""Stable parse/serialize for ``---\\nyaml\\n---\\nbody`` markdown files.

The single source of truth for how every bkk writer formats a markdown
file with YAML frontmatter. Routing all writes through this module keeps
byte-level output stable across the project so that read-modify-write of
an unchanged record is a no-op diff.

Rules enforced by :func:`serialize_frontmatter`:

1. Key order is preserved as read.
2. Block style only (never flow).
3. Quote only when YAML semantics force it (``default_style=None``).
4. ``allow_unicode=True`` — CJK / accented Latin render as themselves.
5. 2-space indent, ``width=10**6`` (no wrap on long single-line values).
6. Output framing is exactly ``"---\\n" + dump + "---\\n" + body``.
7. Output ends with exactly one trailing newline.
8. No anchors / aliases.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

import yaml

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", re.S)


class _NoAliasDumper(yaml.SafeDumper):
    """SafeDumper that refuses to emit YAML anchors / aliases."""

    def ignore_aliases(self, data: Any) -> bool:  # noqa: D401, ARG002
        return True


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split ``text`` into ``(frontmatter_dict, body)``.

    Returns ``({}, text)`` for input without a ``---`` block. Returns
    ``({}, body_after_fence)`` if the YAML fails to parse (matches the
    historical permissive behavior of the previous helper).

    The returned dict preserves key order (Python 3.7+ dict semantics).
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    try:
        fm = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return {}, match.group(2)
    return (fm if isinstance(fm, dict) else {}), match.group(2)


def serialize_frontmatter(fm: Mapping[str, Any], body: str) -> str:
    """Serialize ``(fm, body)`` back into a stable ``---\\n…\\n---\\nbody`` string.

    ``fm`` may be empty — the function still emits an empty frontmatter
    block (``"---\\n---\\n"``) so writers don't accidentally strip the
    fence. Callers that want a bodyless or fenceless file should not use
    this helper.

    ``body`` is normalized to end in exactly one ``\\n``.
    """
    dumped = yaml.dump(
        dict(fm),
        Dumper=_NoAliasDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=10**6,
        indent=2,
    )
    # PyYAML always appends a trailing newline; an empty dict dumps as
    # "{}\n", which we want to render as an empty mapping block instead.
    if not fm:
        dumped = ""
    if body.endswith("\n"):
        body_out = body
    else:
        body_out = body + "\n"
    return "---\n" + dumped + "---\n" + body_out
