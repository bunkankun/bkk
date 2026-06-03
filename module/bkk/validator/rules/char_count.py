"""Char-count parity check between the TLS source XML and the imported bundle.

The TLS source records `<measure unit="char" quantity="N"/>` in the TEI
header. This rule compares that quantity against the total chars actually
present in the imported master juans (front + body + back text). A relative
diff above THRESHOLD is reported as a warning — useful tripwire for missing
or truncated imports.

No-op when `ctx.tls_source_root` is unset or no TLS source XML matches the
bundle id (KRP-only bundles or unmapped TLS texts).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from ..context import ValidationContext

THRESHOLD = 0.05


def run(ctx: ValidationContext) -> None:
    if ctx.tls_source_root is None:
        return
    src = _find_tls_source(ctx.tls_source_root, ctx.bundle_dir.name)
    if src is None:
        return
    expected = _read_measure_quantity(src)
    if expected is None or expected <= 0:
        return

    actual = 0
    for lf in ctx.master_juans.values():
        if not isinstance(lf.data, dict):
            continue
        for bucket in ("front", "body", "back"):
            section = lf.data.get(bucket)
            if isinstance(section, dict):
                text = section.get("text") or ""
                if isinstance(text, str):
                    actual += len(text)

    diff_pct = abs(actual - expected) / expected
    if diff_pct > THRESHOLD:
        ctx.report.add(
            "TLS_CHAR_COUNT_MISMATCH", "warning",
            ctx.master_manifest.rel,
            f"imported chars {actual} vs TLS source {expected} "
            f"(diff {diff_pct:.1%}, threshold {THRESHOLD:.0%})",
        )


def _find_tls_source(root: Path, bundle_id: str) -> Path | None:
    chant = root / "tls-chant" / "chant"
    if not chant.is_dir():
        return None
    matches = list(chant.glob(f"*/{bundle_id}.xml"))
    return matches[0] if matches else None


def _read_measure_quantity(path: Path) -> int | None:
    """Return the integer quantity of `<measure unit="char">` in `<extent>`.

    Streams via iterparse and stops as soon as the measure is seen (or extent
    closes without one) — avoids loading the full document.
    """
    try:
        for _, el in ET.iterparse(path, events=("end",)):
            tag = el.tag.split("}", 1)[-1]
            if tag == "measure" and el.get("unit") == "char":
                q = el.get("quantity")
                if q is None:
                    return None
                try:
                    return int(q)
                except ValueError:
                    return None
            if tag == "extent":
                return None
    except ET.ParseError:
        return None
    return None
