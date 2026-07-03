"""Orchestrator for ``bkk chars canonicalize``.

Walks each bundle directory under the corpus root, processes the master
juan files in place (documentary editions are not touched in v1),
rewrites text + markers + hashes, and patches the master manifest's
reference-asset declarations.
"""

from __future__ import annotations

import copy
import datetime
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, TextIO

import yaml

from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.index.merge import discover_bundles, find_bundle
from bkk.marker_assets import (
    hydrate_juan_markers,
    load_marker_asset,
    marker_asset_entries,
    marker_asset_filename,
)

from .canonicalize import (
    InvalidSubstitutionMarkerError,
    UnmappedCodepointError,
    canonicalize_text,
    canonicalize_text_lenient,
    revert_substitution_markers,
)
from .refs import CanonicalizationContext


def _ts() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _log(log_fh: TextIO | None, level: str, msg: str) -> None:
    if log_fh is None:
        return
    log_fh.write(f"[{_ts()}] {level} {msg}\n")
    log_fh.flush()


def _emit_error(log_fh: TextIO | None, msg: str) -> None:
    print(msg, file=sys.stderr)
    _log(log_fh, "ERROR", msg)


def _emit_warning(log_fh: TextIO | None, msg: str) -> None:
    print(msg, file=sys.stderr)
    _log(log_fh, "WARN", msg)


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
    log_file: Path | None = None,
    abort_on_error: bool = False,
) -> int:
    """Process every master bundle under ``out_root``. Returns an exit code.

    When ``abort_on_error`` is False (the default) the canonicalizer scans
    each bundle to completion, logging every unmapped codepoint occurrence
    (with juan/bucket/offset/codepoint) to ``log_file`` and to stderr; any
    bundle with at least one unmapped codepoint is treated as failed
    (writes skipped). With ``abort_on_error=True`` the legacy behaviour is
    restored: the first unmapped codepoint in a bundle aborts that bundle.
    """
    log_fh: TextIO | None = None
    if log_file is not None:
        log_path = Path(log_file).expanduser().resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = log_path.open("a", encoding="utf-8")
        log_fh.write(
            f"=== [{_ts()}] bkk chars canonicalize "
            f"out_root={out_root} dry_run={dry_run} "
            f"abort_on_error={abort_on_error} "
            f"text_ids={text_ids or 'ALL'} ===\n"
        )
        log_fh.flush()

    try:
        out_root = Path(out_root).expanduser().resolve()
        if not out_root.is_dir():
            _emit_error(log_fh, f"error: corpus root not found: {out_root}")
            return 2

        bundle_dirs = _select_bundles(out_root, text_ids, log_fh=log_fh)
        if not bundle_dirs:
            _emit_error(log_fh, f"no bundles found under {out_root}")
            return 1

        total_subs = 0
        total_juans = 0
        total_unmapped = 0
        failed: list[str] = []
        unmapped_bundles: list[str] = []
        rewrote_bundles = 0

        for bundle_dir in bundle_dirs:
            text_id = bundle_dir.name
            rel = bundle_dir.relative_to(out_root)
            print(f"[{rel}]" if str(rel) != text_id else f"[{text_id}]")
            try:
                stats = _process_bundle(
                    bundle_dir, text_id,
                    ctx=ctx, dry_run=dry_run,
                    log_fh=log_fh, abort_on_error=abort_on_error,
                )
            except (RuntimeError, ValueError, FileNotFoundError) as exc:
                _emit_error(log_fh, f"[{text_id}] error: {exc}")
                failed.append(text_id)
                continue
            total_subs += stats["substitutions"]
            total_juans += stats["juans"]
            bundle_unmapped = stats.get("unmapped", 0)
            if bundle_unmapped:
                total_unmapped += bundle_unmapped
                unmapped_bundles.append(text_id)
                _emit_warning(
                    log_fh,
                    f"[{text_id}] {bundle_unmapped} unmapped codepoint "
                    f"occurrence(s); writes skipped",
                )
            elif stats["substitutions"] or stats["manifest_changed"]:
                rewrote_bundles += 1
            for line in stats["lines"]:
                print(line)

        verb = "would substitute" if dry_run else "substituted"
        print(
            f"{verb} {total_subs} codepoint(s) across {total_juans} juan file(s) "
            f"in {rewrote_bundles}/{len(bundle_dirs)} bundle(s)"
        )
        if total_unmapped:
            print(
                f"{total_unmapped} unmapped codepoint occurrence(s) across "
                f"{len(unmapped_bundles)} bundle(s); writes skipped for those bundles",
                file=sys.stderr,
            )
        if failed:
            _emit_error(
                log_fh,
                f"skipped {len(failed)} bundle(s) due to errors: "
                f"{', '.join(failed)}",
            )
        if failed or unmapped_bundles:
            return 1
        return 0
    finally:
        if log_fh is not None:
            log_fh.close()



def run_revert(
    out_root: Path,
    *,
    text_ids: list[str] | None = None,
    dry_run: bool = False,
    log_file: Path | None = None,
    jobs: int = 1,
) -> int:
    """Undo substitutions emitted by ``bkk chars canonicalize``.

    For each master juan, every ``substitution`` marker is applied in reverse:
    the text character at the marker's offset is changed from ``replacement``
    back to ``original``, and the marker is removed. Hashes, marker assets, and
    manifest asset references are then refreshed.
    """
    log_fh: TextIO | None = None
    if log_file is not None:
        log_path = Path(log_file).expanduser().resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = log_path.open("a", encoding="utf-8")
        log_fh.write(
            f"=== [{_ts()}] bkk chars revert "
            f"out_root={out_root} dry_run={dry_run} "
            f"text_ids={text_ids or 'ALL'} ===\n"
        )
        log_fh.flush()

    try:
        out_root = Path(out_root).expanduser().resolve()
        if not out_root.is_dir():
            _emit_error(log_fh, f"error: corpus root not found: {out_root}")
            return 2

        bundle_dirs = _select_bundles(out_root, text_ids, log_fh=log_fh)
        if not bundle_dirs:
            _emit_error(log_fh, f"no bundles found under {out_root}")
            return 1
        if jobs < 1:
            _emit_error(log_fh, f"error: jobs must be >= 1, got {jobs}")
            return 2

        total_reverted = 0
        total_juans = 0
        failed: list[str] = []
        rewrote_bundles = 0

        if jobs == 1 or len(bundle_dirs) == 1:
            for bundle_dir in bundle_dirs:
                text_id = bundle_dir.name
                rel = bundle_dir.relative_to(out_root)
                print(f"[{rel}]" if str(rel) != text_id else f"[{text_id}]")
                try:
                    stats = _run_revert_bundle(bundle_dir, dry_run=dry_run)
                except (RuntimeError, ValueError, FileNotFoundError) as exc:
                    _emit_error(log_fh, f"[{text_id}] error: {exc}")
                    failed.append(text_id)
                    continue
                total_reverted += stats["reverted"]
                total_juans += stats["juans"]
                if stats["reverted"] or stats["manifest_changed"]:
                    rewrote_bundles += 1
                for line in stats["lines"]:
                    print(line)
        else:
            max_workers = min(jobs, len(bundle_dirs))
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(_run_revert_bundle, bundle_dir, dry_run=dry_run): bundle_dir
                    for bundle_dir in bundle_dirs
                }
                for fut in as_completed(futures):
                    bundle_dir = futures[fut]
                    text_id = bundle_dir.name
                    rel = bundle_dir.relative_to(out_root)
                    print(f"[{rel}]" if str(rel) != text_id else f"[{text_id}]")
                    try:
                        stats = fut.result()
                    except (RuntimeError, ValueError, FileNotFoundError) as exc:
                        _emit_error(log_fh, f"[{text_id}] error: {exc}")
                        failed.append(text_id)
                        continue
                    total_reverted += stats["reverted"]
                    total_juans += stats["juans"]
                    if stats["reverted"] or stats["manifest_changed"]:
                        rewrote_bundles += 1
                    for line in stats["lines"]:
                        print(line)

        verb = "would revert" if dry_run else "reverted"
        print(
            f"{verb} {total_reverted} substitution marker(s) across "
            f"{total_juans} juan file(s) in "
            f"{rewrote_bundles}/{len(bundle_dirs)} bundle(s)"
        )
        if failed:
            _emit_error(
                log_fh,
                f"skipped {len(failed)} bundle(s) due to errors: "
                f"{', '.join(failed)}",
            )
            return 1
        return 0
    finally:
        if log_fh is not None:
            log_fh.close()


def _process_bundle_revert(
    bundle_dir: Path,
    text_id: str,
    *,
    dry_run: bool,
    log_fh: TextIO | None = None,
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
    total_reverted = 0
    pending_juans: list[tuple[Path, dict, str]] = []
    removed_mapping_ids: set[str] = set()

    for seq, juan_path in juan_entries:
        data = yaml.safe_load(juan_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError(f"{juan_path.name}: top-level YAML is not a mapping")
        marker_asset, stale_asset = _marker_asset_for_revert(
            bundle_dir, manifest, text_id, seq,
        )
        data = hydrate_juan_markers(data, marker_asset)

        juan_reverted = 0
        for bucket_name in _BUCKETS:
            bucket = data.get(bucket_name)
            if not isinstance(bucket, dict):
                continue
            markers = bucket.get("markers") or []
            if not isinstance(markers, list) or not markers:
                continue
            text = bucket.get("text") or ""
            try:
                new_text, kept_markers, removed = revert_substitution_markers(
                    text, markers,
                    allow_already_reverted=stale_asset,
                )
            except InvalidSubstitutionMarkerError as exc:
                raise RuntimeError(
                    f"{juan_path.name} [{bucket_name}]: {exc}"
                ) from exc
            if not removed:
                continue

            for marker in removed:
                mapping = marker.get("mapping") or {}
                if isinstance(mapping, dict):
                    mapping_id = mapping.get("identifier")
                    if isinstance(mapping_id, str) and mapping_id:
                        removed_mapping_ids.add(mapping_id)

            juan_reverted += len(removed)
            bucket["text"] = new_text
            bucket["hash"] = sha256_text(new_text) if new_text else ZERO_HASH
            if kept_markers:
                indexed = list(enumerate(kept_markers))
                indexed.sort(key=lambda p: (_marker_offset(p[1]), p[0]))
                bucket["markers"] = [
                    marker_to_flow(dict(m)) for _, m in indexed
                ]
            else:
                bucket.pop("markers", None)

        if juan_reverted == 0 and not stale_asset:
            lines.append(f"  juan {seq:03d}: no substitution markers")
            continue

        if juan_reverted:
            total_reverted += juan_reverted
            line = f"  juan {seq:03d}: reverted {juan_reverted} substitution(s)"
        else:
            line = f"  juan {seq:03d}: recovered orphan marker asset"
        if stale_asset:
            line += " (from orphan marker asset)"
        lines.append(line)
        new_hash = _juan_self_hash(data)
        data["hash"] = new_hash
        pending_juans.append((juan_path, data, new_hash))

    manifest_changed, new_manifest = _patch_manifest_after_revert(
        manifest,
        removed_mapping_ids,
        affected_marker_seqs={
            int(_JUAN_RE.match(p.name).group("seq"))
            for p, _, _ in pending_juans
        },
        new_hashes={
            int(_JUAN_RE.match(p.name).group("seq")): h
            for p, _, h in pending_juans
        },
    )

    if dry_run:
        return {
            "juans": len(juan_entries),
            "reverted": total_reverted,
            "manifest_changed": manifest_changed,
            "lines": lines,
        }

    if pending_juans:
        for juan_path, data, _ in pending_juans:
            juan_path.write_text(dump(data), encoding="utf-8")

    if manifest_changed:
        manifest_path.write_text(dump(new_manifest), encoding="utf-8")
        if pending_juans:
            from bkk.repair.markers import externalize_markers
            externalize_markers(bundle_dir, dry_run=False)
            _cleanup_unreferenced_marker_assets(
                bundle_dir, text_id,
                {int(_JUAN_RE.match(p.name).group("seq")) for p, _, _ in pending_juans},
                manifest_path,
                dry_run=False,
            )

    return {
        "juans": len(juan_entries),
        "reverted": total_reverted,
        "manifest_changed": manifest_changed,
        "lines": lines,
    }


def _run_revert_bundle(
    bundle_dir: Path,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    return _process_bundle_revert(
        bundle_dir,
        bundle_dir.name,
        dry_run=dry_run,
        log_fh=None,
    )


def _marker_offset(marker: dict[str, Any]) -> int:
    offset = marker.get("offset", 0)
    return offset if isinstance(offset, int) and not isinstance(offset, bool) else 0


def _marker_asset_for_revert(
    bundle_dir: Path,
    manifest: dict,
    text_id: str,
    seq: int,
) -> tuple[dict[str, Any] | None, bool]:
    """Return marker asset plus whether it was an orphan fallback.

    Earlier versions of ``bkk chars revert`` could leave marker asset files on
    disk after removing their manifest entries. Loading the predictable
    per-juan asset filename lets a later run clean those stale substitution
    markers and recover any non-substitution markers in the same asset.
    """
    asset = load_marker_asset(bundle_dir, manifest, seq)
    if asset is not None:
        return asset, False

    path = bundle_dir / marker_asset_filename(text_id, seq, None)
    if not path.exists():
        return None, False
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return (data if isinstance(data, dict) else None), True


def _remove_marker_entries_for_seqs(
    assets: dict[str, Any],
    seqs: set[int],
) -> bool:
    """Drop manifest marker-asset entries for juans about to be rewritten."""
    if not seqs or "markers" not in assets:
        return False
    markers = assets.get("markers")
    if not isinstance(markers, list):
        assets.pop("markers", None)
        return True

    kept = []
    changed = False
    for entry in markers:
        if isinstance(entry, dict) and entry.get("seq") in seqs:
            changed = True
            continue
        kept.append(marker_to_flow(dict(entry)) if isinstance(entry, dict) else entry)
    if kept:
        assets["markers"] = kept
    else:
        assets.pop("markers", None)
    return changed


def _cleanup_unreferenced_marker_assets(
    bundle_dir: Path,
    text_id: str,
    seqs: set[int],
    manifest_path: Path,
    *,
    dry_run: bool,
) -> int:
    """Remove stale per-juan marker files for affected master juans."""
    if not seqs:
        return 0
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    referenced = {
        entry.get("filename")
        for entry in marker_asset_entries(manifest if isinstance(manifest, dict) else {})
        if isinstance(entry.get("filename"), str)
    }
    removed = 0
    for seq in seqs:
        filename = marker_asset_filename(text_id, seq, None)
        if filename in referenced:
            continue
        path = bundle_dir / filename
        if path.exists():
            removed += 1
            if not dry_run:
                path.unlink()
    return removed


def _select_bundles(
    out_root: Path,
    text_ids: list[str] | None,
    *,
    log_fh: TextIO | None = None,
) -> list[Path]:
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
                _emit_error(
                    log_fh,
                    f"error: bundle dir not found for {tid!r} under {out_root}",
                )
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
    log_fh: TextIO | None = None,
    abort_on_error: bool = False,
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
    total_unmapped = 0
    pending_juans: list[tuple[Path, dict, str]] = []  # (path, juan_data, new_hash)
    used_mapping_indices: set[int] = set()

    for seq, juan_path in juan_entries:
        data = yaml.safe_load(juan_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError(f"{juan_path.name}: top-level YAML is not a mapping")
        data = hydrate_juan_markers(data, load_marker_asset(bundle_dir, manifest, seq))

        juan_subs = 0
        juan_unmapped = 0
        for bucket_name in _BUCKETS:
            bucket = data.get(bucket_name)
            if not isinstance(bucket, dict):
                continue
            text = bucket.get("text") or ""
            if not text:
                continue
            if abort_on_error:
                try:
                    new_text, new_markers = canonicalize_text(text, ctx)
                except UnmappedCodepointError as exc:
                    raise RuntimeError(
                        f"{juan_path.name} [{bucket_name}]: {exc}"
                    ) from exc
                bucket_unmapped: list[UnmappedCodepointError] = []
            else:
                new_text, new_markers, bucket_unmapped = canonicalize_text_lenient(
                    text, ctx,
                )
            for u in bucket_unmapped:
                ch = chr(u.codepoint)
                _emit_warning(
                    log_fh,
                    f"[{text_id}] {juan_path.name} [{bucket_name}] "
                    f"offset {u.offset}: unmapped U+{u.codepoint:04X} {ch!r}",
                )
            juan_unmapped += len(bucket_unmapped)
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

        total_unmapped += juan_unmapped
        if juan_subs == 0 and juan_unmapped == 0:
            lines.append(f"  juan {seq:03d}: no substitutions")
            continue
        if juan_unmapped:
            lines.append(
                f"  juan {seq:03d}: {juan_subs} substitution(s), "
                f"{juan_unmapped} unmapped"
            )
        else:
            lines.append(f"  juan {seq:03d}: {juan_subs} substitution(s)")
        total_subs += juan_subs
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

    skip_writes = dry_run or total_unmapped > 0

    if skip_writes:
        return {
            "juans": len(juan_entries),
            "substitutions": total_subs,
            "unmapped": total_unmapped,
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
            _cleanup_unreferenced_marker_assets(
                bundle_dir, text_id,
                {int(_JUAN_RE.match(p.name).group("seq")) for p, _, _ in pending_juans},
                manifest_path,
                dry_run=False,
            )

    return {
        "juans": len(juan_entries),
        "substitutions": total_subs,
        "unmapped": total_unmapped,
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



def _patch_manifest_after_revert(
    manifest: dict,
    removed_mapping_ids: set[str],
    *,
    affected_marker_seqs: set[int],
    new_hashes: dict[int, str],
) -> tuple[bool, dict]:
    """Patch manifest after substitution markers are reverted."""
    new = copy.deepcopy(manifest)
    changed = False

    if removed_mapping_ids:
        mappings = new.get("mappings")
        if isinstance(mappings, list):
            kept_mappings = []
            for entry in mappings:
                if (
                    isinstance(entry, dict)
                    and entry.get("canonical_identifier") in removed_mapping_ids
                ):
                    changed = True
                    continue
                kept_mappings.append(marker_to_flow(dict(entry)) if isinstance(entry, dict) else entry)
            if kept_mappings:
                if kept_mappings != mappings:
                    changed = True
                new["mappings"] = kept_mappings
            elif "mappings" in new:
                new.pop("mappings", None)
                changed = True

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
        if _remove_marker_entries_for_seqs(assets, affected_marker_seqs):
            # Stale only for juans we rewrote/recovered; keep other marker
            # asset entries so externalize can preserve unchanged juans.
            changed = True

    if changed:
        new["hash"] = manifest_hash(new)
    return changed, new


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
        if _remove_marker_entries_for_seqs(assets, set(new_hashes)):
            # Stale only for juans we re-shuffle; keep unchanged juans' marker
            # asset entries so externalize can preserve them.
            changed = True

    if changed:
        new["hash"] = manifest_hash(new)
    return changed, new
