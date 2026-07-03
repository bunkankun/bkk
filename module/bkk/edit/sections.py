"""Excise a juan bucket or a list of spans within a bucket.

Both operations rewrite the juan YAML, the marker asset (if present),
and the affected entries of the master manifest (``assets.parts``,
``assets.markers``, ``table_of_contents``, ``hash``). They do not touch
edition-specific manifests under ``editions/<short>/`` — duplications
act on the master text only; per-edition repair is out of scope.

The bundle's ``.bkkx`` is *not* rebuilt here; the caller does that after
the mutation so a single rebuild covers several operations if needed.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from bkk.importer.hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from bkk.importer.write.yaml_writer import dump, marker_to_flow, reflow_manifest
from bkk.marker_assets import (
    VALID_BUCKETS,
    marker_asset_entry_for_seq,
    marker_asset_hash,
)


class EditError(ValueError):
    """Raised when an edit cannot proceed against the bundle's state."""


def delete_juan_bucket(
    bundle_dir: Path | str,
    text_id: str,
    juan_seq: int,
    bucket: str,
) -> dict[str, Any]:
    """Drop one bucket from a juan; remove the juan entirely if nothing is left."""
    if bucket not in VALID_BUCKETS:
        raise EditError(f"bucket must be one of {VALID_BUCKETS!r}; got {bucket!r}")
    ctx = _load(Path(bundle_dir), text_id, juan_seq)

    juan = ctx.juan
    if bucket not in juan:
        raise EditError(
            f"{text_id}/{juan_seq}: bucket {bucket!r} not present; nothing to delete"
        )
    juan.pop(bucket, None)
    if ctx.marker_asset is not None:
        markers_obj = ctx.marker_asset.get("markers") or {}
        if isinstance(markers_obj, dict):
            markers_obj.pop(bucket, None)

    remaining_buckets = [b for b in VALID_BUCKETS if b in juan]
    juan_removed = not remaining_buckets

    if juan_removed:
        _delete_juan_files(ctx)
        _drop_part_entry(ctx.manifest, juan_seq)
        _drop_marker_asset_entry(ctx.manifest, juan_seq)
        _drop_toc_for_seq(ctx.manifest, juan_seq)
    else:
        _drop_toc_for_bucket(ctx.manifest, juan_seq, bucket)
        _write_juan_and_marker_asset(ctx)
        _refresh_part_entry(ctx, juan_seq)
        _refresh_marker_asset_entry(ctx, juan_seq)

    _rehash_and_write_manifest(ctx)
    return {
        "text_id": text_id,
        "juan_seq": juan_seq,
        "bucket": bucket,
        "juan_removed": juan_removed,
        "remaining_buckets": remaining_buckets,
    }


def delete_spans(
    bundle_dir: Path | str,
    text_id: str,
    juan_seq: int,
    bucket: str,
    spans: list[tuple[int, int]] | tuple[tuple[int, int], ...],
) -> dict[str, Any]:
    """Splice ``spans`` out of one bucket; rebase markers and TOC entries."""
    if bucket not in VALID_BUCKETS:
        raise EditError(f"bucket must be one of {VALID_BUCKETS!r}; got {bucket!r}")
    merged = _merge_spans(spans)
    if not merged:
        raise EditError("spans must be a non-empty list of [start, end) ranges")

    ctx = _load(Path(bundle_dir), text_id, juan_seq)
    bucket_obj = ctx.juan.get(bucket)
    if not isinstance(bucket_obj, dict):
        raise EditError(f"{text_id}/{juan_seq}: bucket {bucket!r} not present")

    text = bucket_obj.get("text") or ""
    new_text, deleted_chars = _splice_text(text, merged)
    if not new_text:
        raise EditError(
            f"{text_id}/{juan_seq}/{bucket}: deletion would empty the bucket — "
            "use delete_juan_bucket to drop the bucket instead",
        )
    bucket_obj["text"] = new_text
    bucket_obj["hash"] = sha256_text(new_text)

    inline = bucket_obj.get("markers") or []
    if isinstance(inline, list):
        kept = _rebase_markers(inline, merged)
        if kept:
            bucket_obj["markers"] = [marker_to_flow(dict(m)) for m in kept]
        else:
            bucket_obj.pop("markers", None)

    if ctx.marker_asset is not None:
        ext_obj = ctx.marker_asset.get("markers") or {}
        if isinstance(ext_obj, dict):
            ext_markers = ext_obj.get(bucket) or []
            if isinstance(ext_markers, list):
                kept_ext = _rebase_markers(ext_markers, merged)
                if kept_ext:
                    ext_obj[bucket] = [marker_to_flow(dict(m)) for m in kept_ext]
                else:
                    ext_obj.pop(bucket, None)

    _rebase_toc_for_bucket(ctx.manifest, juan_seq, bucket, merged)
    _write_juan_and_marker_asset(ctx)
    _refresh_part_entry(ctx, juan_seq)
    _refresh_marker_asset_entry(ctx, juan_seq)
    _rehash_and_write_manifest(ctx)

    return {
        "text_id": text_id,
        "juan_seq": juan_seq,
        "bucket": bucket,
        "spans_removed": [list(s) for s in merged],
        "deleted_chars": deleted_chars,
        "new_bucket_length": len(new_text),
    }


# ---------- file IO ---------------------------------------------------------


class _EditContext:
    __slots__ = (
        "bundle_dir",
        "text_id",
        "manifest_path",
        "manifest",
        "juan_path",
        "juan",
        "marker_asset_path",
        "marker_asset",
    )

    def __init__(
        self,
        bundle_dir: Path,
        text_id: str,
        manifest_path: Path,
        manifest: dict,
        juan_path: Path,
        juan: dict,
        marker_asset_path: Path | None,
        marker_asset: dict | None,
    ) -> None:
        self.bundle_dir = bundle_dir
        self.text_id = text_id
        self.manifest_path = manifest_path
        self.manifest = manifest
        self.juan_path = juan_path
        self.juan = juan
        self.marker_asset_path = marker_asset_path
        self.marker_asset = marker_asset


def _load(bundle_dir: Path, text_id: str, juan_seq: int) -> _EditContext:
    bundle_dir = bundle_dir.resolve()
    if not bundle_dir.is_dir():
        raise EditError(f"not a bundle directory: {bundle_dir}")
    manifest_path = bundle_dir / f"{text_id}.manifest.yaml"
    if not manifest_path.is_file():
        raise EditError(f"manifest not found: {manifest_path}")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(manifest, dict):
        raise EditError(f"manifest not a mapping: {manifest_path}")

    parts = (manifest.get("assets") or {}).get("parts") or []
    part = next(
        (p for p in parts if isinstance(p, dict) and p.get("seq") == juan_seq),
        None,
    )
    if part is None:
        raise EditError(f"{text_id}/{juan_seq}: no such part in manifest")
    filename = part.get("filename")
    if not isinstance(filename, str):
        raise EditError(f"{text_id}/{juan_seq}: part entry missing filename")
    juan_path = bundle_dir / filename
    if not juan_path.is_file():
        raise EditError(f"juan file missing: {juan_path}")
    juan = yaml.safe_load(juan_path.read_text(encoding="utf-8")) or {}
    if not isinstance(juan, dict):
        raise EditError(f"juan not a mapping: {juan_path}")

    marker_entry = marker_asset_entry_for_seq(manifest, juan_seq)
    marker_path: Path | None = None
    marker_asset: dict | None = None
    if marker_entry is not None:
        mfile = marker_entry.get("filename")
        if isinstance(mfile, str):
            mp = bundle_dir / mfile
            if mp.is_file():
                data = yaml.safe_load(mp.read_text(encoding="utf-8")) or {}
                if isinstance(data, dict):
                    marker_path = mp
                    marker_asset = data
    return _EditContext(
        bundle_dir=bundle_dir,
        text_id=text_id,
        manifest_path=manifest_path,
        manifest=manifest,
        juan_path=juan_path,
        juan=juan,
        marker_asset_path=marker_path,
        marker_asset=marker_asset,
    )


def _write_juan_and_marker_asset(ctx: _EditContext) -> None:
    _patch_juan_hash(ctx.juan)
    ctx.juan_path.write_text(dump(ctx.juan), encoding="utf-8")
    if ctx.marker_asset is not None and ctx.marker_asset_path is not None:
        ctx.marker_asset["hash"] = ZERO_HASH
        ctx.marker_asset["hash"] = marker_asset_hash(ctx.marker_asset)
        ctx.marker_asset_path.write_text(
            dump(ctx.marker_asset), encoding="utf-8",
        )


def _delete_juan_files(ctx: _EditContext) -> None:
    ctx.juan_path.unlink(missing_ok=True)
    if ctx.marker_asset_path is not None:
        ctx.marker_asset_path.unlink(missing_ok=True)


def _patch_juan_hash(juan: dict) -> None:
    m = copy.deepcopy(juan)
    m["hash"] = ZERO_HASH
    juan["hash"] = sha256_jcs(m)


# ---------- manifest fixups -------------------------------------------------


def _refresh_part_entry(ctx: _EditContext, juan_seq: int) -> None:
    parts = (ctx.manifest.get("assets") or {}).get("parts") or []
    for entry in parts:
        if isinstance(entry, dict) and entry.get("seq") == juan_seq:
            entry["hash"] = ctx.juan.get("hash", ZERO_HASH)
            return


def _refresh_marker_asset_entry(ctx: _EditContext, juan_seq: int) -> None:
    if ctx.marker_asset is None:
        return
    entry = marker_asset_entry_for_seq(ctx.manifest, juan_seq)
    if entry is None:
        return
    entry["hash"] = ctx.marker_asset.get("hash", ZERO_HASH)


def _drop_part_entry(manifest: dict, juan_seq: int) -> None:
    assets = manifest.get("assets") or {}
    parts = assets.get("parts") or []
    assets["parts"] = [
        p for p in parts
        if not (isinstance(p, dict) and p.get("seq") == juan_seq)
    ]


def _drop_marker_asset_entry(manifest: dict, juan_seq: int) -> None:
    assets = manifest.get("assets") or {}
    markers = assets.get("markers") or []
    if not markers:
        return
    assets["markers"] = [
        m for m in markers
        if not (isinstance(m, dict) and m.get("seq") == juan_seq)
    ]


def _drop_toc_for_seq(manifest: dict, juan_seq: int) -> None:
    toc = manifest.get("table_of_contents") or []
    manifest["table_of_contents"] = [
        e for e in toc
        if not (
            isinstance(e, dict)
            and isinstance(e.get("ref"), dict)
            and e["ref"].get("seq") == juan_seq
        )
    ]


def _drop_toc_for_bucket(manifest: dict, juan_seq: int, bucket: str) -> None:
    toc = manifest.get("table_of_contents") or []
    kept: list[dict] = []
    for entry in toc:
        if not isinstance(entry, dict):
            continue
        ref = entry.get("ref")
        if not isinstance(ref, dict) or ref.get("seq") != juan_seq:
            kept.append(entry)
            continue
        span = ref.get("span")
        if isinstance(span, list) and span and span[0] == bucket:
            continue
        kept.append(entry)
    manifest["table_of_contents"] = kept


def _rebase_toc_for_bucket(
    manifest: dict, juan_seq: int, bucket: str,
    spans: tuple[tuple[int, int], ...],
) -> None:
    """Drop TOC entries fully inside a deleted span; shift the rest."""
    toc = manifest.get("table_of_contents") or []
    kept: list[dict] = []
    for entry in toc:
        if not isinstance(entry, dict):
            continue
        ref = entry.get("ref")
        if not isinstance(ref, dict) or ref.get("seq") != juan_seq:
            kept.append(entry)
            continue
        span = ref.get("span")
        if not (
            isinstance(span, list) and len(span) == 3 and span[0] == bucket
        ):
            kept.append(entry)
            continue
        new_range = _rebase_range((int(span[1]), int(span[2])), spans)
        if new_range is None:
            continue
        ref["span"] = [bucket, new_range[0], new_range[1]]
        kept.append(entry)
    manifest["table_of_contents"] = kept


def _rehash_and_write_manifest(ctx: _EditContext) -> None:
    reflow_manifest(ctx.manifest)
    ctx.manifest["hash"] = ZERO_HASH
    ctx.manifest["hash"] = manifest_hash(ctx.manifest)
    ctx.manifest_path.write_text(dump(ctx.manifest), encoding="utf-8")


# ---------- pure helpers ----------------------------------------------------


def _merge_spans(
    spans: list[tuple[int, int]] | tuple[tuple[int, int], ...],
) -> tuple[tuple[int, int], ...]:
    cleaned: list[tuple[int, int]] = []
    for s in spans:
        lo, hi = int(s[0]), int(s[1])
        if hi <= lo:
            continue
        cleaned.append((lo, hi))
    if not cleaned:
        return ()
    cleaned.sort()
    merged: list[tuple[int, int]] = [cleaned[0]]
    for lo, hi in cleaned[1:]:
        last_lo, last_hi = merged[-1]
        if lo <= last_hi:
            merged[-1] = (last_lo, max(last_hi, hi))
        else:
            merged.append((lo, hi))
    return tuple(merged)


def _splice_text(
    text: str, spans: tuple[tuple[int, int], ...],
) -> tuple[str, int]:
    out: list[str] = []
    cursor = 0
    deleted = 0
    for lo, hi in spans:
        lo_clamped = max(cursor, min(lo, len(text)))
        hi_clamped = max(lo_clamped, min(hi, len(text)))
        out.append(text[cursor:lo_clamped])
        deleted += hi_clamped - lo_clamped
        cursor = hi_clamped
    out.append(text[cursor:])
    return "".join(out), deleted


def _rebase_offset(
    offset: int, spans: tuple[tuple[int, int], ...],
) -> int | None:
    """Return the new offset after deletions, or None if inside a span.

    Half-open: an offset equal to a span's ``hi`` survives and shifts.
    """
    shift = 0
    for lo, hi in spans:
        if offset < lo:
            return offset - shift
        if offset < hi:
            return None
        shift += hi - lo
    return offset - shift


def _rebase_range(
    rng: tuple[int, int], spans: tuple[tuple[int, int], ...],
) -> tuple[int, int] | None:
    """Map ``[start, end)`` through deletions; drop if fully inside any span.

    Partial overlap clamps to the surviving portion outside the deletions.
    Returns ``None`` if the range collapses to zero length.
    """
    start, end = rng
    if end <= start:
        return None
    # Compute new start: shift by deletions strictly before ``start``;
    # if start lies inside a deleted span, snap forward to that span's end
    # (shifted), so a section that begins inside a cut keeps whatever tail
    # of itself survives.
    shift_before = 0
    new_start = None
    for lo, hi in spans:
        if start < lo:
            new_start = start - shift_before
            break
        if start < hi:
            new_start = lo - shift_before
            shift_before += hi - lo
            break
        shift_before += hi - lo
    if new_start is None:
        new_start = start - shift_before

    # Compute new end: subtract the total length of deletions that fall
    # strictly before ``end``; deletions that straddle ``end`` clamp it
    # down to the start of the deletion.
    shift_end = 0
    clipped_end = end
    for lo, hi in spans:
        if hi <= end:
            shift_end += hi - lo
        elif lo < end:
            clipped_end = lo
            shift_end += clipped_end - lo  # zero, but kept for clarity
            break
        else:
            break
    new_end = clipped_end - shift_end

    if new_end <= new_start:
        return None
    return (new_start, new_end)


def _rebase_markers(
    markers: list[dict],
    spans: tuple[tuple[int, int], ...],
) -> list[dict]:
    out: list[dict] = []
    for m in markers:
        if not isinstance(m, dict):
            continue
        offset = m.get("offset")
        if not isinstance(offset, int):
            out.append(m)
            continue
        new_offset = _rebase_offset(offset, spans)
        if new_offset is None:
            continue
        if new_offset == offset:
            out.append(m)
        else:
            new = dict(m)
            new["offset"] = new_offset
            out.append(new)
    return out
