"""BKK bundle validator.

Public entry point: :func:`validate_bundle`. Returns a :class:`Report` whose
``findings`` list each rule violation.
"""

from __future__ import annotations

from pathlib import Path

from .context import ValidationContext, load_context
from .report import Finding, Report
from .rules import run_all

__all__ = ["validate_bundle", "Report", "Finding", "ValidationContext"]


def validate_bundle(
    bundle_dir: str | Path,
    tls_source_root: str | Path | None = None,
) -> Report:
    """Validate the bundle at *bundle_dir* and return a populated Report."""
    src_root = Path(tls_source_root) if tls_source_root is not None else None
    ctx = load_context(Path(bundle_dir), tls_source_root=src_root)
    run_all(ctx)
    return ctx.report
