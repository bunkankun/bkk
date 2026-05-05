"""Validate every bundle in a corpus directory and dump per-bundle + summary
reports to an output directory.

Usage:
    python tools/validate_corpus.py --input module/output --output wip/val
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from bkk.validator import validate_bundle


def _iter_bundles(input_dir: Path):
    for child in sorted(input_dir.iterdir()):
        if not child.is_dir():
            continue
        manifest = child / f"{child.name}.manifest.yaml"
        if manifest.exists():
            yield child


def _validate_one(args: tuple[str, str]) -> tuple[str, list[tuple[str, str]]]:
    """Worker: validate one bundle, write its JSON if it has findings, and
    return (bundle_name, [(rule_id, severity), ...])."""
    bundle_path, output_dir = args
    bundle_dir = Path(bundle_path)
    report = validate_bundle(bundle_dir)
    findings = [(f.rule_id, f.severity) for f in report.findings]
    if findings:
        (Path(output_dir) / f"{bundle_dir.name}.json").write_text(
            report.render_json(), encoding="utf-8",
        )
    return bundle_dir.name, findings


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True,
                   help="corpus directory containing bundle subdirs")
    p.add_argument("--output", type=Path, required=True,
                   help="output directory for per-bundle + summary reports")
    p.add_argument("--workers", type=int, default=os.cpu_count() or 4,
                   help="parallel worker processes (default: CPU count)")
    args = p.parse_args(argv)

    if not args.input.is_dir():
        print(f"error: not a directory: {args.input}", file=sys.stderr)
        return 2
    args.output.mkdir(parents=True, exist_ok=True)

    bundles = list(_iter_bundles(args.input))
    total = len(bundles)
    print(f"validating {total} bundles from {args.input} "
          f"with {args.workers} workers")

    rule_counts: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()
    per_bundle: list[dict] = []
    clean = 0

    work = [(str(b), str(args.output)) for b in bundles]
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for name, findings in pool.map(_validate_one, work, chunksize=8):
            done += 1
            if findings:
                n_err = sum(1 for _, sev in findings if sev == "error")
                n_warn = sum(1 for _, sev in findings if sev == "warning")
                for rule_id, sev in findings:
                    rule_counts[rule_id] += 1
                    severity_counts[sev] += 1
                per_bundle.append({
                    "bundle": name,
                    "errors": n_err,
                    "warnings": n_warn,
                })
            else:
                clean += 1
            if done % 200 == 0:
                print(f"  {done}/{total} ({clean} clean so far)", flush=True)

    per_bundle.sort(key=lambda r: (-r["errors"], -r["warnings"], r["bundle"]))

    summary = {
        "input": str(args.input),
        "total_bundles": total,
        "clean_bundles": clean,
        "bundles_with_findings": total - clean,
        "severity_totals": dict(severity_counts),
        "rule_id_totals": dict(rule_counts.most_common()),
        "by_bundle": per_bundle,
    }
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    lines = [
        f"input:                 {args.input}",
        f"total bundles:         {total}",
        f"clean bundles:         {clean}",
        f"bundles with findings: {total - clean}",
        f"errors:                {severity_counts.get('error', 0)}",
        f"warnings:              {severity_counts.get('warning', 0)}",
        "",
        "rule_id histogram:",
    ]
    for rule_id, n in rule_counts.most_common():
        lines.append(f"  {n:>8}  {rule_id}")
    lines += ["", "top 20 bundles by error count:"]
    for entry in per_bundle[:20]:
        lines.append(
            f"  {entry['errors']:>5} err  {entry['warnings']:>4} warn  {entry['bundle']}"
        )
    (args.output / "summary.txt").write_text("\n".join(lines) + "\n",
                                             encoding="utf-8")

    print(f"\ndone: {clean}/{total} clean, "
          f"{severity_counts.get('error', 0)} errors, "
          f"{severity_counts.get('warning', 0)} warnings")
    print(f"reports written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
