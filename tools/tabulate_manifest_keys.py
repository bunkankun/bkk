"""Tabulate the key names under ``metadata.identifiers`` and
``metadata.source`` across every manifest in the corpus.

Useful for spotting stray / one-off keys that crept into the bundles.
For each key, prints the count and up to N sample text-ids where it
occurs (so low-frequency keys are easy to investigate).

Usage:
    python tools/tabulate_manifest_keys.py --input module/output
    python tools/tabulate_manifest_keys.py --input module/output --samples 5
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import yaml


def _iter_bundle_dirs(root: Path):
    """Yield text-id directories. Handles both flat (``<root>/<text_id>/``)
    and ``--by-section`` (``<root>/<section>/<text_id>/``) layouts."""
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if (child / f"{child.name}.manifest.yaml").is_file():
            yield child
            continue
        # Section directory: descend one level.
        for sub in sorted(child.iterdir()):
            if not sub.is_dir():
                continue
            if (sub / f"{sub.name}.manifest.yaml").is_file():
                yield sub


def _iter_manifests(root: Path):
    for bundle in _iter_bundle_dirs(root):
        yield bundle / f"{bundle.name}.manifest.yaml"
        editions = bundle / "editions"
        if editions.is_dir():
            for ed in sorted(editions.iterdir()):
                if not ed.is_dir():
                    continue
                for m in sorted(ed.glob("*.manifest.yaml")):
                    yield m


def _tally(
    manifests,
) -> dict[str, dict[str, list[str]]]:
    """Return {section: {key: [text_id, ...]}} for the two metadata
    sub-sections we care about."""
    out: dict[str, dict[str, list[str]]] = {
        "metadata.identifiers": defaultdict(list),
        "metadata.source": defaultdict(list),
    }
    for path in manifests:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"# skip {path}: {exc}")
            continue
        if not isinstance(data, dict):
            continue
        md = data.get("metadata") or {}
        if not isinstance(md, dict):
            continue
        # The filename is "<text_id>.manifest.yaml" or
        # "<text_id>-<short>.manifest.yaml"; split off any edition suffix.
        stem = path.name[: -len(".manifest.yaml")]
        text_id = stem.split("-", 1)[0]
        for section in ("identifiers", "source"):
            sub = md.get(section) or {}
            if not isinstance(sub, dict):
                continue
            for key in sub:
                out[f"metadata.{section}"][key].append(text_id)
    return out


def _print_table(title: str, keys: dict[str, list[str]], samples: int) -> None:
    print(f"\n=== {title} ===")
    if not keys:
        print("(no entries)")
        return
    rows = sorted(keys.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    width = max(len(k) for k, _ in rows)
    for key, ids in rows:
        sample = ", ".join(sorted(set(ids))[:samples])
        print(f"  {key.ljust(width)}  {len(ids):6d}   {sample}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--input", type=Path, required=True,
        help="corpus root containing <text_id>/<text_id>.manifest.yaml",
    )
    ap.add_argument(
        "--samples", type=int, default=3,
        help="number of sample text-ids to list per key (default: 3)",
    )
    args = ap.parse_args()

    manifests = list(_iter_manifests(args.input))
    print(f"# scanned {len(manifests)} manifests under {args.input}")
    tally = _tally(manifests)
    for section, keys in tally.items():
        _print_table(section, keys, args.samples)


if __name__ == "__main__":
    main()
