"""Per-juan parallel passage assets and remote text context."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import APIRouter, HTTPException, Path as PathParam, Query, Request
from pydantic import BaseModel

from .. import errors
from ..state import AppState
from .. import selection
from bkk.index.parallel import _align_ops


router = APIRouter(tags=["parallels"])

_REF_RE = re.compile(
    r"^(?P<section>[0-9][a-z])(?P<serial>[0-9]{1,4})/"
    r"(?P<seq>[0-9]+)/(?P<bucket>front|back)?@"
    r"(?P<offset>[0-9]+)\+(?P<length>[1-9][0-9]*)$"
)
_BUCKET_ORDER = {"front": 0, "body": 1, "back": 2}
_CONTEXT = 20


class ParallelPassageOut(BaseModel):
    id: str
    source: str
    local_bucket: Literal["front", "body", "back"]
    local_offset: int
    local_length: int
    local_text: str = ""
    textid: str
    juan_seq: int
    bucket: Literal["front", "body", "back"]
    offset: int
    length: int
    toc_label: str | None = None
    title: str | None = None
    edit_distance: int = 0
    left: str = ""
    text: str = ""
    right: str = ""
    diff: list[list] = []
    local_gap: int | None = None
    remote_gap: int | None = None
    available: bool = False


class RemoteTextOut(BaseModel):
    textid: str
    title: str | None = None
    count: int
    overlap_length: int


class JuanParallelsResponse(BaseModel):
    textid: str
    juan_seq: int
    source_title: str | None = None
    source_char_count: int = 0
    sort: Literal["local", "remote"]
    remote_textid: str | None = None
    total: int
    offset: int
    limit: int
    available_min_length: int
    available_max_length: int
    remote_texts: list[RemoteTextOut]
    locations: list[ParallelPassageOut]


def _asset_source(path: Path, textid: str, seq: int) -> str:
    prefix = f"{textid}_{seq:03d}."
    suffix = ".parallels.yaml"
    name = path.name
    if name.startswith(prefix) and name.endswith(suffix):
        return name[len(prefix) : -len(suffix)]
    return name


def _parse_ref(ref: Any) -> tuple[str, int, str, int, int] | None:
    if not isinstance(ref, str):
        return None
    match = _REF_RE.fullmatch(ref)
    if match is None:
        return None
    textid = f"KR{match.group('section')}{int(match.group('serial')):04d}"
    return (
        textid,
        int(match.group("seq")),
        match.group("bucket") or "body",
        int(match.group("offset")),
        int(match.group("length")),
    )


def _load_markers(
    root: Path, textid: str, seq: int,
) -> list[dict[str, Any]]:
    bundle_root = root / textid
    paths = sorted(bundle_root.glob(f"{textid}_{seq:03d}.*.parallels.yaml"))
    markers: list[dict[str, Any]] = []
    for path in paths:
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"cannot read parallel asset {path.name}: {exc}",
            ) from exc
        buckets = raw.get("markers") if isinstance(raw, dict) else None
        if not isinstance(buckets, dict):
            continue
        source = _asset_source(path, textid, seq)
        for bucket in ("front", "body", "back"):
            rows = buckets.get(bucket)
            if not isinstance(rows, list):
                continue
            for index, marker in enumerate(rows):
                if not isinstance(marker, dict) or marker.get("type") != "parallel":
                    continue
                local_offset = marker.get("offset")
                local_length = marker.get("length")
                edit_distance = marker.get("edit_distance", 0)
                remote = _parse_ref(marker.get("ref"))
                if (
                    isinstance(local_offset, bool)
                    or not isinstance(local_offset, int)
                    or local_offset < 0
                    or isinstance(local_length, bool)
                    or not isinstance(local_length, int)
                    or local_length < 1
                    or isinstance(edit_distance, bool)
                    or not isinstance(edit_distance, int)
                    or edit_distance < 0
                    or remote is None
                ):
                    continue
                remote_textid, remote_seq, remote_bucket, remote_offset, remote_length = remote
                toc_label = marker.get("toc_label")
                markers.append({
                    "id": (
                        f"{source}:{bucket}:{local_offset}:{local_length}:"
                        f"{marker['ref']}:{index}"
                    ),
                    "source": source,
                    "source_textid": textid,
                    "source_seq": seq,
                    "local_bucket": bucket,
                    "local_offset": local_offset,
                    "local_length": local_length,
                    "textid": remote_textid,
                    "juan_seq": remote_seq,
                    "bucket": remote_bucket,
                    "offset": remote_offset,
                    "length": remote_length,
                    "toc_label": toc_label if isinstance(toc_label, str) else None,
                    "edit_distance": edit_distance,
                })
    markers.sort(key=lambda row: (
        _BUCKET_ORDER[row["local_bucket"]],
        row["local_offset"],
        row["local_length"],
        row["textid"],
        row["juan_seq"],
        _BUCKET_ORDER[row["bucket"]],
        row["offset"],
        row["source"],
    ))
    return markers


def _load_titles(state: AppState, textids: list[str]) -> dict[str, str | None]:
    title_cache: dict[str, str | None] = {}
    catalog = state.open_catalog()
    if catalog is not None and textids:
        try:
            placeholders = ",".join("?" for _ in textids)
            for found_textid, title in catalog.execute(
                f"SELECT textid, title FROM catalog_bundle "
                f"WHERE textid IN ({placeholders})",
                textids,
            ).fetchall():
                title_cache[found_textid] = title
        except sqlite3.DatabaseError:
            pass
        finally:
            catalog.close()
    for textid in textids:
        if textid in title_cache:
            continue
        rec = state.lookup_bundle(textid)
        title_cache[textid] = rec.title if rec is not None else None
    return title_cache


def _remote_group_key(row: dict[str, Any]) -> tuple[str, int, str]:
    return row["textid"], row["juan_seq"], row["bucket"]


def _remote_group_overlap(rows: list[dict[str, Any]]) -> int:
    return sum(row["local_length"] for row in rows)


def _sort_remote_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(_remote_group_key(row), []).append(row)
    groups = list(grouped.values())
    for group in groups:
        group.sort(key=lambda row: (
            row["offset"],
            row["local_offset"],
            row["local_length"],
            row["source"],
        ))
    groups.sort(key=lambda group: (
        -_remote_group_overlap(group),
        group[0]["textid"],
        group[0]["juan_seq"],
        _BUCKET_ORDER[group[0]["bucket"]],
        group[0]["offset"],
        group[0]["source"],
    ))
    return [row for group in groups for row in group]


def _attach_gaps(rows: list[dict[str, Any]]) -> None:
    previous: dict[tuple[str, int, str], dict[str, Any]] = {}
    for row in rows:
        key = _remote_group_key(row)
        prev = previous.get(key)
        if prev is None:
            row["local_gap"] = None
            row["remote_gap"] = None
        else:
            row["local_gap"] = max(
                0,
                row["local_offset"] - (prev["local_offset"] + prev["local_length"]),
            )
            row["remote_gap"] = max(
                0,
                row["offset"] - (prev["offset"] + prev["length"]),
            )
        previous[key] = row


def _hydrate_page(
    state: AppState, rows: list[dict[str, Any]],
) -> list[ParallelPassageOut]:
    text_cache: dict[tuple[str, int, str], str | None] = {}
    title_cache = _load_titles(state, sorted({row["textid"] for row in rows}))
    keys = sorted(
        {
            (row["textid"], row["juan_seq"], row["bucket"]) for row in rows
        }
        | {
            (row["source_textid"], row["source_seq"], row["local_bucket"])
            for row in rows
        }
    )
    if keys and state.index_path.exists():
        placeholders = ",".join("(?, ?, ?)" for _ in keys)
        params = [value for key in keys for value in key]
        try:
            conn = sqlite3.connect(
                f"file:{state.index_path}?mode=ro", uri=True,
            )
            try:
                found = conn.execute(
                    "SELECT j.textid, j.seq, b.kind, b.text "
                    "FROM bucket b JOIN juan j ON b.juan_id = j.juan_id "
                    f"WHERE (j.textid, j.seq, b.kind) IN ({placeholders})",
                    params,
                ).fetchall()
            finally:
                conn.close()
            for found_textid, found_seq, found_bucket, found_text in found:
                text_cache[(found_textid, found_seq, found_bucket)] = found_text
        except sqlite3.DatabaseError:
            # Source-file hydration below remains a correct, if slower,
            # fallback for deployments without a readable merged index.
            pass

    for textid, seq, bucket in keys:
        key = (textid, seq, bucket)
        if key in text_cache:
            continue
        rec = state.lookup_bundle(textid)
        if textid not in title_cache:
            title_cache[textid] = rec.title if rec is not None else None
        if rec is None:
            text_cache[key] = None
            continue
        try:
            juan = selection.load_juan_file(
                rec.bundle_dir, rec.manifest, textid, seq,
            )
            bucket_obj = juan.get(bucket)
            text = bucket_obj.get("text") if isinstance(bucket_obj, dict) else None
            text_cache[key] = text if isinstance(text, str) else None
        except HTTPException:
            text_cache[key] = None

    out: list[ParallelPassageOut] = []
    for row in rows:
        textid = row["textid"]
        seq = row["juan_seq"]
        bucket = row["bucket"]
        local_key = (row["source_textid"], row["source_seq"], row["local_bucket"])
        key = (textid, seq, bucket)
        local_text = text_cache.get(local_key)
        remote_text = text_cache[key]
        local_start = row["local_offset"]
        local_end = local_start + row["local_length"]
        start = row["offset"]
        end = start + row["length"]
        available = (
            remote_text is not None
            and local_text is not None
            and end <= len(remote_text)
            and local_end <= len(local_text)
        )
        local_slice = local_text[local_start:local_end] if local_text is not None and local_end <= len(local_text) else ""
        remote_slice = remote_text[start:end] if remote_text is not None and end <= len(remote_text) else ""
        diff = [list(op) for op in _align_ops(local_slice, remote_slice)] if available else []
        out.append(ParallelPassageOut(
            **row,
            title=title_cache.get(textid),
            local_text=local_slice,
            left=remote_text[max(0, start - _CONTEXT) : start] if available else "",
            text=remote_slice if available else "",
            right=remote_text[end : end + _CONTEXT] if available else "",
            diff=diff,
            available=available,
        ))
    return out


def _remote_text_options(
    state: AppState,
    rows: list[dict[str, Any]],
) -> list[RemoteTextOut]:
    titles = _load_titles(state, sorted({row["textid"] for row in rows}))
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = grouped.setdefault(row["textid"], {
            "textid": row["textid"],
            "title": titles.get(row["textid"]),
            "count": 0,
            "overlap_length": 0,
        })
        entry["count"] += 1
        entry["overlap_length"] += row["local_length"]
    options = [
        RemoteTextOut(**entry)
        for entry in grouped.values()
    ]
    options.sort(key=lambda item: (
        -item.overlap_length,
        -item.count,
        item.title or item.textid,
        item.textid,
    ))
    return options


def _source_char_count(state: AppState, textid: str, seq: int) -> int:
    rec = state.lookup_bundle(textid)
    if rec is None:
        return 0
    try:
        juan = selection.load_juan_file(rec.bundle_dir, rec.manifest, textid, seq)
    except HTTPException:
        return 0
    total = 0
    for bucket in ("front", "body", "back"):
        bucket_obj = juan.get(bucket)
        text = bucket_obj.get("text") if isinstance(bucket_obj, dict) else None
        if isinstance(text, str):
            total += len(text)
    return total


@router.get(
    "/bundles/{textid}/juan/{seq}/parallels",
    response_model=JuanParallelsResponse,
    summary="Parallel passages linked to locations in this juan",
)
def get_juan_parallels(
    request: Request,
    textid: str = PathParam(...),
    seq: int = PathParam(..., ge=0),
    bucket: Literal["front", "body", "back"] | None = Query(None),
    start: int | None = Query(None, ge=0),
    end: int | None = Query(None, ge=1),
    min_length: int | None = Query(None, ge=1),
    max_length: int | None = Query(None, ge=1),
    sort: Literal["local", "remote"] = Query("local"),
    remote_textid: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> JuanParallelsResponse:
    state: AppState = request.app.state.bkk
    if state.lookup_bundle(textid) is None:
        raise errors.bundle_not_found(textid)
    if (start is None) != (end is None):
        raise errors.bad_request("parallel filter requires both start and end")
    if start is not None and bucket is None:
        raise errors.bad_request("parallel range filter requires bucket")
    if start is not None and end is not None and end <= start:
        raise errors.bad_request("parallel filter end must be greater than start")
    if (min_length is None) != (max_length is None):
        raise errors.bad_request("parallel length filter requires both min_length and max_length")
    if min_length is not None and max_length is not None and max_length < min_length:
        raise errors.bad_request("parallel length filter max_length must be greater than or equal to min_length")
    if remote_textid is not None and not remote_textid.strip():
        remote_textid = None

    root = state.parallels_root
    rows = _load_markers(root, textid, seq) if root is not None and root.is_dir() else []
    if bucket is not None:
        rows = [row for row in rows if row["local_bucket"] == bucket]
    if start is not None and end is not None:
        rows = [
            row for row in rows
            if row["local_offset"] < end
            and row["local_offset"] + row["local_length"] > start
        ]
    available_lengths = [row["local_length"] for row in rows]
    available_min_length = min(available_lengths) if available_lengths else 0
    available_max_length = max(available_lengths) if available_lengths else 0
    if min_length is not None and max_length is not None:
        rows = [
            row for row in rows
            if min_length <= row["local_length"] <= max_length
        ]
    remote_texts = _remote_text_options(state, rows)
    if remote_textid is not None:
        rows = [row for row in rows if row["textid"] == remote_textid]
    if sort == "remote":
        rows = _sort_remote_rows(rows)
    else:
        rows.sort(key=lambda row: (
            _BUCKET_ORDER[row["local_bucket"]],
            row["local_offset"],
            row["local_length"],
            row["textid"],
            row["juan_seq"],
            _BUCKET_ORDER[row["bucket"]],
            row["offset"],
            row["source"],
        ))
    _attach_gaps(rows)
    total = len(rows)
    page = rows[offset : offset + limit]
    source_titles = _load_titles(state, [textid])
    return JuanParallelsResponse(
        textid=textid,
        juan_seq=seq,
        source_title=source_titles.get(textid),
        source_char_count=_source_char_count(state, textid, seq),
        sort=sort,
        remote_textid=remote_textid,
        total=total,
        offset=offset,
        limit=limit,
        available_min_length=available_min_length,
        available_max_length=available_max_length,
        remote_texts=remote_texts,
        locations=_hydrate_page(state, page),
    )
