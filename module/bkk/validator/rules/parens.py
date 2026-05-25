"""Paren-balance check for ``(`` / ``)`` punctuation markers.

In KRP-derived bundles, double-column small-character commentary is fenced
by paired ``(`` … ``)`` punctuation markers (with ``/`` as an internal
column-break, ignored here). A well-formed juan bucket has exactly one
``)`` after each ``(``, never nested. Violations are not fatal — the
punctuation markers still round-trip back to the KRP source — but they
block ``bkk voice add`` from deriving root/commentary voice markers for
that bucket, so we surface them as warnings.

Reports the first stray/unmatched paren per bucket (the per-rule cap on
the Report then collapses any repeats in the same file).
"""

from __future__ import annotations

from ..context import ValidationContext, LoadedFile
from bkk.marker_assets import effective_markers_for_bucket


def run(ctx: ValidationContext) -> None:
    for lf in ctx.master_juans.values():
        seq = lf.data.get("seq") if isinstance(lf.data, dict) else None
        asset_lf = ctx.marker_assets.get(seq) if isinstance(seq, int) else None
        _check_juan(
            ctx, lf,
            asset_lf.data if asset_lf is not None and isinstance(asset_lf.data, dict) else None,
        )
    for ed in ctx.editions.values():
        for lf in ed.juans.values():
            seq = lf.data.get("seq") if isinstance(lf.data, dict) else None
            asset_lf = ed.marker_assets.get(seq) if isinstance(seq, int) else None
            _check_juan(
                ctx, lf,
                asset_lf.data if asset_lf is not None and isinstance(asset_lf.data, dict) else None,
            )


def _check_juan(ctx: ValidationContext, lf: LoadedFile, marker_asset: dict | None) -> None:
    if not lf.exists or lf.parse_error is not None:
        return
    if not isinstance(lf.data, dict):
        return
    for bucket_name in ("front", "body", "back"):
        bucket = lf.data.get(bucket_name)
        if not isinstance(bucket, dict):
            continue
        markers = effective_markers_for_bucket(lf.data, bucket_name, marker_asset)
        _check_bucket(ctx, lf, bucket_name, markers)


def _check_bucket(
    ctx: ValidationContext, lf: LoadedFile, bucket_name: str, markers: list,
) -> None:
    parens: list[tuple[int, str]] = []
    for m in markers:
        if not isinstance(m, dict):
            continue
        if m.get("type") != "punctuation":
            continue
        ch = m.get("content")
        if ch not in ("(", ")"):
            continue
        off = m.get("offset")
        if isinstance(off, int):
            parens.append((off, ch))
    if not parens:
        return

    parens.sort(key=lambda p: p[0])
    depth = 0
    n_open = 0
    n_close = 0
    for off, ch in parens:
        if ch == "(":
            n_open += 1
            if depth > 0:
                ctx.report.add(
                    "JUAN_PARENS_BALANCED", "warning", lf.rel,
                    f"{bucket_name}: nested '(' at offset {off} "
                    "(commentary brackets must not nest)",
                )
            depth += 1
        else:
            n_close += 1
            if depth == 0:
                ctx.report.add(
                    "JUAN_PARENS_BALANCED", "warning", lf.rel,
                    f"{bucket_name}: stray ')' at offset {off} "
                    "with no matching '('",
                )
            else:
                depth -= 1
    if depth > 0:
        ctx.report.add(
            "JUAN_PARENS_BALANCED", "warning", lf.rel,
            f"{bucket_name}: {depth} unmatched '(' "
            f"(opens={n_open}, closes={n_close})",
        )
