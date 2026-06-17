"""``bkk validate chars`` — read-only character-set conformance check.

Walks a bundle (or a tree of bundles) and reports codepoints that are
either outside the declared canonical character set (errors) or excluded
by the charset without a designated substitution in the loaded mappings
(warnings). Master juan ``text`` buckets and manifest string fields are
both scanned.

The classification logic is shared with the canonicalizer via
:class:`bkk.chars.refs.CanonicalizationContext`:

- ``cp in ctx.mapping_entries``         → silent (canonicalize handles it)
- ``cp in ctx.excluded``                → warning (no mapping designated)
- ``ctx.in_inclusion_block(cp)``        → silent (admissible)
- otherwise                              → error (out-of-charset)

Juan text is scanned strictly; any non-CJK codepoint (including stray
Latin letters) is reported. Manifest fields use ``allow_ascii=True`` and
pure-ASCII strings (identifiers, filenames, numeric labels) are skipped
entirely, so the report focuses on CJK content like ``metadata.title``
and TOC labels without flooding on bookkeeping fields.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import yaml

from bkk.chars.refs import (
    DEFAULT_REFS_DIR,
    CanonicalizationContext,
    load_context,
)
from bkk.index.merge import discover_bundles, find_bundle


_JUAN_RE = re.compile(
    r"^(?P<text_id>.+?)_(?P<seq>\d{3})(?:-(?P<short>[A-Za-z0-9][A-Za-z0-9_-]*))?\.yaml$",
)
_BUCKETS = ("front", "body", "back")


@dataclass(frozen=True)
class CharFinding:
    location: str           # e.g. "juan 003 [body]" or "manifest metadata.title"
    cp: int
    char: str
    severity: str           # "error" | "warning"
    reason: str             # "out-of-charset" | "kZVariant" | "kSpoofingVariant" | ...
    count: int


@dataclass
class BundleCharsReport:
    text_id: str
    bundle_dir: Path
    findings: list[CharFinding] = field(default_factory=list)

    @property
    def errors(self) -> int:
        return sum(f.count for f in self.findings if f.severity == "error")

    @property
    def warnings(self) -> int:
        return sum(f.count for f in self.findings if f.severity == "warning")


def classify(
    cp: int,
    ctx: CanonicalizationContext,
    *,
    allow_ascii: bool = False,
) -> tuple[str, str] | None:
    """Return ``(severity, reason)`` for an offending codepoint, or ``None``.

    Codepoints with a mapping entry are silent (canonicalize will rewrite
    them). Codepoints listed as excluded but unmapped warn. Codepoints
    outside every inclusion block error. When ``allow_ascii`` is true,
    ASCII (U+0000..U+007F) is silent — used for manifest string scans
    where identifiers and filenames legitimately contain ASCII.
    """
    if allow_ascii and cp < 0x80:
        return None
    if cp in ctx.mapping_entries:
        return None
    if cp in ctx.excluded:
        reason = ctx.excluded[cp].get("reason") or "excluded"
        return ("warning", str(reason))
    if ctx.in_inclusion_block(cp):
        return None
    return ("error", "out-of-charset")


def scan_text(
    text: str,
    ctx: CanonicalizationContext,
    *,
    location: str,
    allow_ascii: bool = False,
) -> list[CharFinding]:
    """Scan *text* and return one :class:`CharFinding` per offending codepoint.

    Multiple occurrences of the same codepoint at the same location are
    aggregated into a single finding with ``count`` set.
    """
    if not text:
        return []
    counter: Counter[int] = Counter()
    for ch in text:
        cp = ord(ch)
        if classify(cp, ctx, allow_ascii=allow_ascii) is not None:
            counter[cp] += 1
    findings: list[CharFinding] = []
    for cp, count in sorted(counter.items()):
        severity, reason = classify(cp, ctx, allow_ascii=allow_ascii)  # type: ignore[misc]
        findings.append(
            CharFinding(
                location=location,
                cp=cp,
                char=chr(cp),
                severity=severity,
                reason=reason,
                count=count,
            )
        )
    return findings


def _iter_manifest_strings(
    node: Any, path: str,
) -> Iterator[tuple[str, str]]:
    """Yield ``(dotted_path, string_value)`` for every leaf string in a manifest.

    Skips any value under a key named ``hash`` and any string that looks
    like a ``sha256:`` digest, to avoid noise from integrity fields.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "hash":
                continue
            yield from _iter_manifest_strings(
                value, f"{path}.{key}" if path else str(key),
            )
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _iter_manifest_strings(value, f"{path}[{i}]")
    elif isinstance(node, str):
        if node.startswith("sha256:"):
            return
        yield path, node


def _master_juan_entries(bundle_dir: Path, text_id: str) -> list[tuple[int, Path]]:
    entries: list[tuple[int, Path]] = []
    for entry in sorted(bundle_dir.iterdir()):
        if not entry.is_file():
            continue
        name = entry.name
        if name.endswith(".manifest.yaml") or name.endswith(".ann.yaml"):
            continue
        m = _JUAN_RE.match(name)
        if not m or m.group("text_id") != text_id:
            continue
        if m.group("short") is not None:
            continue
        entries.append((int(m.group("seq")), entry))
    entries.sort(key=lambda t: t[0])
    return entries


def check_bundle(
    bundle_dir: Path, *, ctx: CanonicalizationContext,
) -> BundleCharsReport:
    text_id = bundle_dir.name
    report = BundleCharsReport(text_id=text_id, bundle_dir=bundle_dir)

    manifest_path = bundle_dir / f"{text_id}.manifest.yaml"
    if manifest_path.exists():
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        if isinstance(manifest, dict):
            for dotted, value in _iter_manifest_strings(manifest, ""):
                if all(ord(c) < 0x80 for c in value):
                    continue  # pure-ASCII identifiers/paths/labels
                report.findings.extend(
                    scan_text(
                        value, ctx,
                        location=f"manifest {dotted}",
                        allow_ascii=True,
                    )
                )

    for seq, juan_path in _master_juan_entries(bundle_dir, text_id):
        data = yaml.safe_load(juan_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        for bucket_name in _BUCKETS:
            bucket = data.get(bucket_name)
            if not isinstance(bucket, dict):
                continue
            text = bucket.get("text")
            if not isinstance(text, str) or not text:
                continue
            report.findings.extend(
                scan_text(text, ctx, location=f"juan {seq:03d} [{bucket_name}]")
            )

    return report


def render_bundle_text(r: BundleCharsReport) -> str:
    """Render one bundle's findings as the text-mode report block."""
    lines = [f"[{r.text_id}]"]
    if not r.findings:
        lines.append("  ok")
        return "\n".join(lines)
    for f in r.findings:
        lines.append(
            f"  {f.location} U+{f.cp:04X} {f.char} "
            f"{f.reason} {f.severity} \u00d7{f.count}"
        )
    lines.append(
        f"  {r.text_id}: {r.errors} error(s), {r.warnings} warning(s) "
        f"across {len(r.findings)} location(s)"
    )
    return "\n".join(lines)


def render_text(reports: list[BundleCharsReport]) -> str:
    """Render every bundle plus a trailing summary line."""
    blocks = [render_bundle_text(r) for r in reports]
    total_err = sum(r.errors for r in reports)
    total_warn = sum(r.warnings for r in reports)
    bundles_with_findings = sum(1 for r in reports if r.findings)
    blocks.append(
        f"summary: {total_err} error(s), {total_warn} warning(s) "
        f"in {bundles_with_findings}/{len(reports)} bundle(s)"
    )
    return "\n".join(blocks)


def render_json(reports: list[BundleCharsReport]) -> str:
    payload = {
        "bundles": [
            {
                "text_id": r.text_id,
                "bundle_dir": str(r.bundle_dir),
                "findings": [
                    {
                        "location": f.location,
                        "cp": f.cp,
                        "char": f.char,
                        "severity": f.severity,
                        "reason": f.reason,
                        "count": f.count,
                    }
                    for f in r.findings
                ],
                "errors": r.errors,
                "warnings": r.warnings,
            }
            for r in reports
        ],
        "totals": {
            "errors": sum(r.errors for r in reports),
            "warnings": sum(r.warnings for r in reports),
            "bundles": len(reports),
            "bundles_with_findings": sum(1 for r in reports if r.findings),
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bkk validate chars",
        description="Check bundle text against the canonical character set.",
    )
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--text-id", dest="text_ids", action="append", default=None,
        help="restrict the run to the named bundle (repeatable)",
    )
    group.add_argument(
        "--in", dest="in_dir", type=Path, default=None,
        help="walk this directory for bundle dirs",
    )
    p.add_argument(
        "--refs-dir", dest="refs_dir", type=Path, default=None,
        help=f"override the reference-assets directory (default: {DEFAULT_REFS_DIR})",
    )
    p.add_argument(
        "--json", action="store_true",
        help="emit JSON instead of text output",
    )
    return p


def _resolve_corpus_root() -> Path | None:
    from bkk.config import load_rc
    rc = load_rc()
    root = (
        rc.get("validate", {}).get("out")
        or rc.get("import", {}).get("out")
        or rc.get("global", {}).get("corpus")
    )
    return Path(root) if root else None


def run(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        ctx = load_context(args.refs_dir)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    bundles: list[Path] = []
    if args.text_ids:
        root = args.in_dir or _resolve_corpus_root()
        if root is None:
            print(
                "error: no corpus root resolved; pass --in or set "
                "validate.out / import.out / global.corpus in .bkkrc",
                file=sys.stderr,
            )
            return 2
        for tid in args.text_ids:
            b = find_bundle(root, tid)
            if b is None:
                print(
                    f"error: bundle dir not found for {tid!r} under {root}",
                    file=sys.stderr,
                )
                return 2
            bundles.append(b)
    else:
        root = args.in_dir or _resolve_corpus_root()
        if root is None:
            print(
                "error: no corpus root resolved; pass --in or set "
                "validate.out / import.out / global.corpus in .bkkrc",
                file=sys.stderr,
            )
            return 2
        if not root.is_dir():
            print(f"error: not a directory: {root}", file=sys.stderr)
            return 2
        bundles = discover_bundles(root)
        if not bundles:
            print(f"no bundles found under {root}", file=sys.stderr)
            return 1

    reports: list[BundleCharsReport] = []
    total_err = 0
    total_warn = 0
    bundles_with_findings = 0

    if args.json:
        # JSON is a single document; render after all bundles are scanned.
        # Emit progress to stderr so the user sees activity on big trees.
        for i, b in enumerate(bundles, 1):
            print(f"[{i}/{len(bundles)}] {b.name}", file=sys.stderr, flush=True)
            reports.append(check_bundle(b, ctx=ctx))
        print(render_json(reports))
        total_err = sum(r.errors for r in reports)
    else:
        # Text mode: stream per-bundle output so a big tree shows progress.
        for b in bundles:
            r = check_bundle(b, ctx=ctx)
            reports.append(r)
            print(render_bundle_text(r), flush=True)
            total_err += r.errors
            total_warn += r.warnings
            if r.findings:
                bundles_with_findings += 1
        print(
            f"summary: {total_err} error(s), {total_warn} warning(s) "
            f"in {bundles_with_findings}/{len(reports)} bundle(s)",
            flush=True,
        )

    return 1 if total_err else 0
