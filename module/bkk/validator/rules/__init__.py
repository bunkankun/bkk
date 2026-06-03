"""Rule registry. ``run_all`` invokes every rule module in order."""

from __future__ import annotations

from ..context import ValidationContext
from . import char_count, filesystem, juan, manifest, parens, pua

# Order matters only in that filesystem checks gate later checks (they do not
# short-circuit, but later rules tolerate missing files via context flags).
_MODULES = (filesystem, manifest, juan, parens, pua, char_count)


def run_all(ctx: ValidationContext) -> None:
    for mod in _MODULES:
        mod.run(ctx)
