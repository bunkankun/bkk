"""Slice a juan body by markers, by char offset+length, or by TOC entry.

Used by both the ``/bundles/{textid}/juan/{seq}/slice`` endpoint and by
:mod:`bkk.serve.recipe_fulfil` so the same slicing semantics back direct
URL access and recipe-as-request fulfillment.

A slice always carries the bucket it came from, the absolute ``[start, end)``
span within that bucket, the sliced text, and any markers that fall within
the span (with offsets re-based to slice start).

Errors are signalled with :class:`fastapi.HTTPException` so router code can
let them propagate, while :mod:`recipe_fulfil` catches them and converts
them to per-pin errors in the response.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml
from fastapi import HTTPException

from . import errors

VALID_BUCKETS = ("front", "body", "back")


@dataclass(frozen=True)
class JuanSlice:
    juan_seq: int
    bucket: str
    span: tuple[int, int]
    text: str
    markers: list[dict[str, Any]]


def load_juan(corpus_root: Path, textid: str, seq: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return ``(manifest, juan)`` dicts for ``(textid, seq)``."""
    bundle = corpus_root / textid
    manifest_path = bundle / f"{textid}.manifest.yaml"
    if not manifest_path.exists():
        raise errors.bundle_not_found(textid)
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    parts = (manifest.get("assets") or {}).get("parts") or []
    entry = next((p for p in parts if p.get("seq") == seq), None)
    if entry is None:
        raise errors.juan_not_found(textid, seq)
    juan_path = bundle / entry["filename"]
    if not juan_path.exists():
        raise errors.juan_not_found(textid, seq)
    juan = yaml.safe_load(juan_path.read_text(encoding="utf-8")) or {}
    return manifest, juan


def load_manifest(corpus_root: Path, textid: str) -> dict[str, Any]:
    bundle = corpus_root / textid
    manifest_path = bundle / f"{textid}.manifest.yaml"
    if not manifest_path.exists():
        raise errors.bundle_not_found(textid)
    return yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}


def _bucket_or_400(juan: dict[str, Any], bucket: str) -> dict[str, Any]:
    if bucket not in VALID_BUCKETS:
        raise errors.bad_request("bad_bucket", bucket=bucket, valid=list(VALID_BUCKETS))
    body = juan.get(bucket) or {}
    if not isinstance(body, dict):
        raise errors.bad_request("bucket_not_object", bucket=bucket)
    return body


def slice_whole(juan: dict[str, Any], seq: int, *, bucket: str = "body") -> JuanSlice:
    body = _bucket_or_400(juan, bucket)
    text = body.get("text") or ""
    markers = [m for m in (body.get("markers") or []) if isinstance(m, dict)]
    return JuanSlice(
        juan_seq=seq, bucket=bucket, span=(0, len(text)),
        text=text, markers=list(markers),
    )


def slice_by_offset(
    juan: dict[str, Any], seq: int, offset: int, length: int,
    *, bucket: str = "body",
) -> JuanSlice:
    body = _bucket_or_400(juan, bucket)
    text = body.get("text") or ""
    if offset < 0 or length < 0 or offset + length > len(text):
        raise errors.bad_request(
            "bad_slice_range",
            offset=offset, length=length, bucket_size=len(text),
        )
    end = offset + length
    sliced_text = text[offset:end]
    sliced_markers: list[dict[str, Any]] = []
    for m in body.get("markers") or []:
        if not isinstance(m, dict):
            continue
        mo = m.get("offset")
        if not isinstance(mo, int):
            continue
        if offset <= mo < end:
            adjusted = dict(m)
            adjusted["offset"] = mo - offset
            sliced_markers.append(adjusted)
    return JuanSlice(
        juan_seq=seq, bucket=bucket, span=(offset, end),
        text=sliced_text, markers=sliced_markers,
    )


def slice_by_markers(
    juan: dict[str, Any], seq: int, from_id: str, to_id: str,
    *, bucket: str = "body",
) -> JuanSlice:
    body = _bucket_or_400(juan, bucket)
    markers = [m for m in (body.get("markers") or []) if isinstance(m, dict)]
    by_id: dict[str, dict[str, Any]] = {}
    for m in markers:
        mid = m.get("id")
        if isinstance(mid, str) and mid:
            by_id.setdefault(mid, m)
    if from_id not in by_id:
        raise errors.bad_request("marker_not_found", marker_id=from_id, bucket=bucket)
    if to_id not in by_id:
        raise errors.bad_request("marker_not_found", marker_id=to_id, bucket=bucket)
    start = by_id[from_id].get("offset")
    end = by_id[to_id].get("offset")
    if not (isinstance(start, int) and isinstance(end, int)):
        raise errors.bad_request("marker_no_offset", from_id=from_id, to_id=to_id)
    if end < start:
        start, end = end, start
    return slice_by_offset(juan, seq, start, end - start, bucket=bucket)


def slice_by_toc(
    manifest: dict[str, Any],
    juan_loader: Callable[[int], dict[str, Any]],
    toc_marker_id: str,
) -> JuanSlice:
    """Slice the bucket span declared by a TOC entry.

    ``juan_loader`` is invoked lazily after the TOC entry is found so the
    caller can defer reading juan files until needed.
    """
    toc = manifest.get("table_of_contents") or []
    for entry in toc:
        if not isinstance(entry, dict):
            continue
        ref = entry.get("ref") or {}
        if ref.get("marker_id") != toc_marker_id:
            continue
        seq = ref.get("seq")
        span = ref.get("span")
        if not isinstance(seq, int) or not isinstance(span, list) or len(span) != 3:
            raise errors.bad_request(
                "toc_no_span", marker_id=toc_marker_id,
            )
        bucket, start, end = span
        if not isinstance(bucket, str) or not isinstance(start, int) or not isinstance(end, int):
            raise errors.bad_request(
                "toc_bad_span", marker_id=toc_marker_id, span=span,
            )
        juan = juan_loader(seq)
        return slice_by_offset(juan, seq, start, end - start, bucket=bucket)
    raise errors.bad_request("toc_marker_not_found", marker_id=toc_marker_id)


def apply_selection(
    selection: dict[str, Any] | None,
    *,
    corpus_root: Path,
    textid: str,
) -> JuanSlice | list[JuanSlice]:
    """Apply one of the recipe selection forms; raise HTTPException on error.

    Forms:
      None or {}                          → whole bundle (one slice per juan)
      {"juan": N}                          → whole juan N body
      {"juan": N, "bucket": "front"|...}  → whole bucket of juan N
      {"juan": N, "from": MID, "to": MID} → marker range in juan N body
      {"juan": N, "offset": O, "length": L} → char range in juan N body
      {"toc": MID}                         → TOC entry's [bucket, start, end] span
    """
    manifest = load_manifest(corpus_root, textid)
    if not selection:
        # Whole bundle: return body slice for every part.
        out: list[JuanSlice] = []
        parts = (manifest.get("assets") or {}).get("parts") or []
        for entry in parts:
            seq = entry.get("seq")
            if not isinstance(seq, int):
                continue
            _, juan = load_juan(corpus_root, textid, seq)
            out.append(slice_whole(juan, seq, bucket="body"))
        return out

    if "toc" in selection:
        marker_id = selection["toc"]
        if not isinstance(marker_id, str):
            raise errors.bad_request("bad_toc_id", value=marker_id)

        def _loader(seq: int) -> dict[str, Any]:
            _, j = load_juan(corpus_root, textid, seq)
            return j

        return slice_by_toc(manifest, _loader, marker_id)

    seq = selection.get("juan")
    if not isinstance(seq, int):
        raise errors.bad_request("missing_juan", selection=selection)
    bucket = selection.get("bucket", "body")
    if not isinstance(bucket, str):
        raise errors.bad_request("bad_bucket", bucket=bucket)
    _, juan = load_juan(corpus_root, textid, seq)

    if "from" in selection or "to" in selection:
        from_id = selection.get("from")
        to_id = selection.get("to")
        if not (isinstance(from_id, str) and isinstance(to_id, str)):
            raise errors.bad_request(
                "marker_range_requires_strings", **{"from": from_id, "to": to_id}
            )
        return slice_by_markers(juan, seq, from_id, to_id, bucket=bucket)

    if "offset" in selection or "length" in selection:
        offset = selection.get("offset")
        length = selection.get("length")
        if not (isinstance(offset, int) and isinstance(length, int)):
            raise errors.bad_request(
                "offset_range_requires_ints", offset=offset, length=length
            )
        return slice_by_offset(juan, seq, offset, length, bucket=bucket)

    return slice_whole(juan, seq, bucket=bucket)
