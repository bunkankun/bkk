"""Orchestrator for ``bkk chars canonicalize``.

Walks each bundle directory under the corpus root, processes the master
juan files in place (documentary editions are not touched in v1),
rewrites text + markers + hashes, and patches the master manifest's
reference-asset declarations.
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.index.merge import discover_bundles, find_bundle
from bkk.marker_assets import hydrate_juan_markers, load_marker_asset

from .canonicalize import UnmappedCodepointError, canonicalize_text
from .refs import CanonicalizationContext


_JUAN_RE = re.compile(
    r"^(?P<text_id>.+?)_(?P<seq>\d{3})(?:-(?P<short>[A-Za-z0-9][A-Za-z0-9_-]*))?\.yaml$",
)
_BUCKETS = ("front", "body", "back")


def run_canonicalize(
    out_root: Path,
    *,
    ctx: CanonicalizationContext,
    text_ids: list[str] | None = None,
    dry_run: bool = False,
) -> int:
    """Process every master bundle under ``out_root``. Returns an exit code."""
    out_root = Path(out_root).expanduser().resolve()
    if not out_root.is_dir():
        print(f"error: corpus root not found: {out_root}", file=sys.stderr)
        return 2

    bundle_dirs = _select_bundles(out_root, text_ids)
    if not bundle_dirs:
        print(f"no bundles found under {out_root}", file=sys.stderr)
        return 1

    total_subs = 0
    total_juans = 0
    failed: list[str] = []
    rewrote_bundles = 0

    for bundle_dir in bundle_dirs:
        text_id = bundle_dir.name
        rel = bundle_dir.relative_to(out_root)
        print(f"[{rel}]" if str(rel) != text_id else f"[{text_id}]")
        try:
            stats = _process_bundle(bundle_dir, text_id, ctx=ctx, dry_run=dry_run)
        except (RuntimeError, ValueError, FileNotFoundError) as exc:
            print(f"  error: {exc}", file=sys.stderr)
            failed.append(text_id)
            continue
        total_subs += stats["substitutions"]
        total_juans += stats["juans"]
        if stats["substitutions"] or stats["manifest_changed"]:
            rewrote_bundles += 1
        for line in stats["lines"]:
            print(line)

    verb = "would substitute" if dry_run else "substituted"
    print(
        f"{verb} {total_subs} codepoint(s) across {total_juans} juan file(s) "
        f"in {rewrote_bundles}/{len(bundle_dirs)} bundle(s)"
    )
    if failed:
        print(
            f"skipped {len(failed)} bundle(s) due to errors: "
            f"{', '.join(failed)}",
            file=sys.stderr,
        )
        return 1
    return 0


def _select_bundles(out_root: Path, text_ids: list[str] | None) -> list[Path]:
    """Discover bundle dirs under ``out_root``.

    Resolves both the flat (``<corpus>/<id>/``) and sectioned
    (``<corpus>/<section>/<id>/``) layouts via
    :func:`bkk.index.merge.discover_bundles` so that ``bkk chars
    canonicalize`` works on a corpus written with ``bkk import
    --by-section``.
    """
    if text_ids:
        out = []
        for tid in text_ids:
            bundle = find_bundle(out_root, tid)
            if bundle is None:
                print(f"error: bundle dir not found for {tid!r} under {out_root}", file=sys.stderr)
                return []
            out.append(bundle)
        return out
    return discover_bundles(out_root)


def _process_bundle(
    bundle_dir: Path,
    text_id: str,
    *,
    ctx: CanonicalizationContext,
    dry_run: bool,
) -> dict[str, Any]:
    manifest_path = bundle_dir / f"{text_id}.manifest.yaml"
    if not manifest_path.exists():
        raise FileNotFoundError(f"master manifest not found: {manifest_path}")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(manifest, dict):
        raise RuntimeError(f"{manifest_path.name}: manifest top level is not a mapping")

    juan_entries = _master_juan_entries(bundle_dir, text_id)
    if not juan_entries:
        raise RuntimeError(f"no master juan files found under {bundle_dir}")

    lines: list[str] = []
    total_subs = 0
    pending_juans: list[tuple[Path, dict, str]] = []  # (path, juan_data, new_hash)
    used_mapping_indices: set[int] = set()

    for seq, juan_path in juan_entries:
        data = yaml.safe_load(juan_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError(f"{juan_path.name}: top-level YAML is not a mapping")
        data = hydrate_juan_markers(data, load_marker_asset(bundle_dir, manifest, seq))

        juan_subs = 0
        for bucket_name in _BUCKETS:
            bucket = data.get(bucket_name)
            if not isinstance(bucket, dict):
                continue
            text = bucket.get("text") or ""
            if not text:
                continue
            try:
                new_text, new_markers = canonicalize_text(text, ctx)
            except UnmappedCodepointError as exc:
                raise RuntimeError(
                    f"{juan_path.name} [{bucket_name}]: {exc}"
                ) from exc
            if not new_markers:
                continue
            for m in new_markers:
                used_mapping_indices.add(
                    _mapping_index_from_marker(m, ctx)
                )
            juan_subs += len(new_markers)
            bucket["text"] = new_text
            bucket["hash"] = sha256_text(new_text) if new_text else ZERO_HASH
            existing_markers = bucket.get("markers") or []
            merged = list(existing_markers) + new_markers
            indexed = list(enumerate(merged))
            indexed.sort(key=lambda p: (p[1].get("offset", 0), p[0]))
            bucket["markers"] = [marker_to_flow(dict(m)) for _, m in indexed]

        if juan_subs == 0:
            lines.append(f"  juan {seq:03d}: no substitutions")
            continue

        total_subs += juan_subs
        lines.append(f"  juan {seq:03d}: {juan_subs} substitution(s)")
        new_hash = _juan_self_hash(data)
        data["hash"] = new_hash
        pending_juans.append((juan_path, data, new_hash))

    manifest_changed, new_manifest = _patch_manifest(
        manifest, ctx, used_mapping_indices,
        new_hashes={
            int(_JUAN_RE.match(p.name).group("seq")): h
            for p, _, h in pending_juans
        },
    )

    if dry_run:
        return {
            "juans": len(juan_entries),
            "substitutions": total_subs,
            "manifest_changed": manifest_changed,
            "lines": lines,
        }

    if pending_juans:
        for juan_path, data, _ in pending_juans:
            juan_path.write_text(dump(data), encoding="utf-8")

    if manifest_changed:
        manifest_path.write_text(dump(new_manifest), encoding="utf-8")
        # Re-split inline vs external markers and refresh per-juan asset
        # files for every juan affected by substitution. This mirrors the
        # post-write pass used by `bkk voice`.
        if pending_juans:
            from bkk.repair.markers import externalize_markers
            externalize_markers(bundle_dir, dry_run=False)

    return {
        "juans": len(juan_entries),
        "substitutions": total_subs,
        "manifest_changed": manifest_changed,
        "lines": lines,
    }


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
            continue  # documentary edition — skipped in v1
        entries.append((int(m.group("seq")), entry))
    entries.sort(key=lambda t: t[0])
    return entries


def _mapping_index_from_marker(
    marker: dict[str, Any], ctx: CanonicalizationContext,
) -> int:
    mapping_id = (marker.get("mapping") or {}).get("identifier")
    for i, m in enumerate(ctx.mappings):
        if m.canonical_identifier == mapping_id:
            return i
    raise RuntimeError(
        f"substitution marker references unknown mapping: {mapping_id!r}"
    )


def _juan_self_hash(juan_dict: dict) -> str:
    m = copy.deepcopy(juan_dict)
    m["hash"] = ZERO_HASH
    return sha256_jcs(m)


def _patch_manifest(
    manifest: dict,
    ctx: CanonicalizationContext,
    used_mapping_indices: set[int],
    *,
    new_hashes: dict[int, str],
) -> tuple[bool, dict]:
    """Return (changed, new_manifest).

    The manifest is patched to:

    - fill ``canonical_set.hash`` with ``ctx.charset_hash``;
    - declare the substitution mapping(s) used in this run under
      ``mappings:`` (one entry per mapping; idempotent merge);
    - update ``assets.parts[*].hash`` for every juan that was rewritten;
    - drop ``assets.markers`` so the externalize pass can rebuild it;
    - recompute the manifest's own ``hash``.
    """
    new = copy.deepcopy(manifest)
    changed = False

    cs = new.get("canonical_set")
    if not isinstance(cs, dict):
        cs = {}
        new["canonical_set"] = cs
    if cs.get("identifier") != ctx.charset_id:
        cs["identifier"] = ctx.charset_id
        changed = True
    if cs.get("hash") != ctx.charset_hash:
        cs["hash"] = ctx.charset_hash
        changed = True

    if used_mapping_indices:
        existing = new.get("mappings")
        existing_list = list(existing) if isinstance(existing, list) else []
        by_id = {
            e.get("canonical_identifier"): e
            for e in existing_list
            if isinstance(e, dict)
        }
        for idx in sorted(used_mapping_indices):
            asset = ctx.mappings[idx]
            entry = {
                "canonical_identifier": asset.canonical_identifier,
                "hash": asset.hash,
            }
            if by_id.get(asset.canonical_identifier) != entry:
                by_id[asset.canonical_identifier] = entry
                changed = True
        if by_id:
            new["mappings"] = [
                marker_to_flow(by_id[k]) for k in sorted(by_id.keys())
            ]

    assets = new.get("assets")
    if isinstance(assets, dict):
        parts = assets.get("parts")
        if isinstance(parts, list) and new_hashes:
            new_parts: list = []
            for entry in parts:
                if not isinstance(entry, dict):
                    new_parts.append(entry)
                    continue
                seq = entry.get("seq")
                if isinstance(seq, int) and seq in new_hashes:
                    entry = dict(entry)
                    entry["hash"] = new_hashes[seq]
                    changed = True
                new_parts.append(marker_to_flow(entry))
            assets["parts"] = new_parts
        if new_hashes and "markers" in assets:
            # Stale once we re-shuffle markers; externalize pass rebuilds it.
            assets.pop("markers", None)
            changed = True

    if changed:
        new["hash"] = manifest_hash(new)
    return changed, new
