"""Per-juan parallel passage assets and remote text context."""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import APIRouter, HTTPException, Path as PathParam, Query, Request
from pydantic import BaseModel, Field, model_validator

from .. import errors
from ..state import AppState, UserSession
from bkk.index.parallel import _align_ops, discover_parallel_passages
from bkk.index.parallel_assets import (
    assert_index_unchanged,
    capture_index_snapshot,
    derive_index_name,
    write_target_parallel_assets,
)
from .auth import SESSION_COOKIE


router = APIRouter(tags=["parallels"])

_REF_RE = re.compile(
    r"^(?P<section>[0-9][a-z])(?P<serial>[0-9]{1,4})/"
    r"(?P<seq>[0-9]+)/(?P<bucket>front|back)?@"
    r"(?P<offset>[0-9]+)\+(?P<length>[1-9][0-9]*)$"
)
_BUCKET_ORDER = {"front": 0, "body": 1, "back": 2}
_CONTEXT = 20
_GENERATION_LOCK = threading.Lock()
_PARALLEL_CACHE_LIMIT = 512


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


class JuanParallelsStatus(BaseModel):
    textid: str
    juan_seq: int
    has_assets: bool
    has_parallels: bool
    sources: list[Literal["corpus", "bundle"]]
    can_generate: bool


class JuanParallelsGeneration(BaseModel):
    textid: str
    juan_seq: int
    generated: bool
    has_parallels: bool
    clusters: int = 0
    markers: int = 0
    files: int = 0
    message: str


class JuanParallelsGenerationParams(BaseModel):
    bucket: Literal["front", "body", "back", "all"] = "all"
    min_length: int = Field(12, ge=3)
    max_length: int | None = Field(None, ge=3)
    min_occurrences: int = Field(2, ge=2)
    max_postings: int = Field(500, ge=2)
    max_edits: int = Field(0, ge=0, le=4)
    context: int = Field(20, ge=0, le=500)
    include_contained: bool = False

    @model_validator(mode="after")
    def validate_length_range(self) -> "JuanParallelsGenerationParams":
        if self.max_length is not None and self.max_length < self.min_length:
            raise ValueError("max_length must be greater than or equal to min_length")
        return self


def _require_user(request: Request) -> UserSession:
    session = request.app.state.bkk.sessions.get(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise HTTPException(status_code=401, detail="GitHub login required")
    return session


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


def _path_signature(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return str(path), stat.st_mtime_ns, stat.st_size


def _asset_signature(paths: list[Path]) -> tuple[tuple[str, int, int], ...]:
    return tuple(_path_signature(path) for path in paths)


def _cache_set_bounded(
    cache: dict[Any, Any], key: Any, value: Any,
    *, limit: int = _PARALLEL_CACHE_LIMIT,
) -> None:
    cache[key] = value
    while len(cache) > limit:
        cache.pop(next(iter(cache)))


def _asset_paths(
    state: AppState, textid: str, seq: int,
) -> tuple[list[Path], list[Literal["corpus", "bundle"]]]:
    pattern = f"{textid}_{seq:03d}.*.parallels.yaml"
    paths: list[Path] = []
    sources: list[Literal["corpus", "bundle"]] = []
    root = state.parallels_root
    if root is not None:
        found = sorted((root / textid).glob(pattern))
        if found:
            paths.extend(found)
            sources.append("corpus")
    rec = state.lookup_bundle(textid)
    if rec is not None:
        found = sorted((rec.bundle_dir / "parallels").glob(pattern))
        if found:
            paths.extend(found)
            sources.append("bundle")
    return paths, sources


def _load_markers(
    state: AppState, paths: list[Path], textid: str, seq: int,
) -> list[dict[str, Any]]:
    cache_key = (textid, seq)
    try:
        signature = _asset_signature(paths)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"cannot inspect parallel assets for {textid}/{seq}: {exc}",
        ) from exc
    with state._parallel_cache_lock:
        cached = state._parallel_marker_cache.get(cache_key)
        if cached is not None and cached.get("signature") == signature:
            return [dict(row) for row in cached["rows"]]

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
    with state._parallel_cache_lock:
        _cache_set_bounded(
            state._parallel_marker_cache,
            cache_key,
            {"signature": signature, "rows": [dict(row) for row in markers]},
        )
    return [dict(row) for row in markers]


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


def _typed_file_signature(kind: str, path: Path) -> tuple[str, str, int, int]:
    stat = path.stat()
    return kind, str(path), stat.st_mtime_ns, stat.st_size


def _index_signature(state: AppState) -> tuple[str, str, int, int] | None:
    try:
        if state.index_path.exists():
            return _typed_file_signature("index", state.index_path)
    except OSError:
        return None
    return None


def _source_juan_path(rec: Any, seq: int) -> Path | None:
    parts = (rec.manifest.get("assets") or {}).get("parts") or []
    entry = next(
        (part for part in parts if isinstance(part, dict) and part.get("seq") == seq),
        None,
    )
    filename = entry.get("filename") if isinstance(entry, dict) else None
    return rec.bundle_dir / filename if isinstance(filename, str) else None


def _cached_bucket_text_is_valid(
    cached: dict[str, Any],
    index_sig: tuple[str, str, int, int] | None,
) -> bool:
    signature = cached.get("signature")
    if signature == index_sig:
        return True
    if not (
        isinstance(signature, tuple)
        and len(signature) == 4
        and signature[0] == "source"
        and isinstance(signature[1], str)
    ):
        return False
    try:
        return _typed_file_signature("source", Path(signature[1])) == signature
    except OSError:
        return False


def _load_bucket_text_from_source(
    state: AppState, textid: str, seq: int, bucket: str,
) -> tuple[str | None, tuple[str, str, int, int] | None]:
    rec = state.lookup_bundle(textid)
    if rec is None:
        return None, None
    juan_path = _source_juan_path(rec, seq)
    if juan_path is None or not juan_path.exists():
        return None, None
    try:
        signature = _typed_file_signature("source", juan_path)
        juan = yaml.safe_load(juan_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None, None
    bucket_obj = juan.get(bucket) if isinstance(juan, dict) else None
    text = bucket_obj.get("text") if isinstance(bucket_obj, dict) else None
    return text if isinstance(text, str) else None, signature


def _load_bucket_texts(
    state: AppState, keys: list[tuple[str, int, str]],
) -> dict[tuple[str, int, str], str | None]:
    unique_keys = list(dict.fromkeys(keys))
    index_sig = _index_signature(state)
    text_cache: dict[tuple[str, int, str], str | None] = {}
    missing: list[tuple[str, int, str]] = []

    with state._parallel_cache_lock:
        for key in unique_keys:
            cached = state._parallel_bucket_text_cache.get(key)
            if cached is not None and _cached_bucket_text_is_valid(cached, index_sig):
                text_cache[key] = cached.get("text")
            else:
                missing.append(key)

    found_from_index: set[tuple[str, int, str]] = set()
    if missing and index_sig is not None:
        placeholders = ",".join("(?, ?, ?)" for _ in missing)
        params = [value for key in missing for value in key]
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
            with state._parallel_cache_lock:
                for found_textid, found_seq, found_bucket, found_text in found:
                    key = (found_textid, found_seq, found_bucket)
                    text_cache[key] = found_text
                    found_from_index.add(key)
                    _cache_set_bounded(
                        state._parallel_bucket_text_cache,
                        key,
                        {"signature": index_sig, "text": found_text},
                    )
        except sqlite3.DatabaseError:
            # Source-file hydration below remains a correct, if slower,
            # fallback for deployments without a readable merged index.
            pass

    for key in missing:
        if key in found_from_index or key in text_cache:
            continue
        textid, seq, bucket = key
        text, signature = _load_bucket_text_from_source(state, textid, seq, bucket)
        text_cache[key] = text
        if signature is not None:
            with state._parallel_cache_lock:
                _cache_set_bounded(
                    state._parallel_bucket_text_cache,
                    key,
                    {"signature": signature, "text": text},
                )

    return text_cache


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
    text_cache = _load_bucket_texts(state, keys)

    out: list[ParallelPassageOut] = []
    for row in rows:
        textid = row["textid"]
        seq = row["juan_seq"]
        bucket = row["bucket"]
        local_key = (row["source_textid"], row["source_seq"], row["local_bucket"])
        key = (textid, seq, bucket)
        local_text = text_cache.get(local_key)
        remote_text = text_cache.get(key)
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
    texts = _load_bucket_texts(
        state,
        [(textid, seq, bucket) for bucket in ("front", "body", "back")],
    )
    total = 0
    for bucket in ("front", "body", "back"):
        text = texts.get((textid, seq, bucket))
        if isinstance(text, str):
            total += len(text)
    return total


def _parallel_status(
    state: AppState, textid: str, seq: int,
) -> JuanParallelsStatus:
    paths, sources = _asset_paths(state, textid, seq)
    return JuanParallelsStatus(
        textid=textid,
        juan_seq=seq,
        has_assets=bool(paths),
        has_parallels=bool(_load_markers(state, paths, textid, seq)),
        sources=sources,
        can_generate=state.index_path.is_file() or state.corpus_root.is_dir(),
    )


@router.get(
    "/bundles/{textid}/juan/{seq}/parallels/status",
    response_model=JuanParallelsStatus,
    summary="Whether this juan already has parallel-passage assets",
)
def get_juan_parallels_status(
    request: Request,
    textid: str = PathParam(...),
    seq: int = PathParam(..., ge=0),
) -> JuanParallelsStatus:
    state: AppState = request.app.state.bkk
    if state.lookup_bundle(textid) is None:
        raise errors.bundle_not_found(textid)
    return _parallel_status(state, textid, seq)


@router.post(
    "/bundles/{textid}/juan/{seq}/parallels/generate",
    response_model=JuanParallelsGeneration,
    summary="Generate missing parallel-passage assets for one juan",
)
def generate_juan_parallels(
    request: Request,
    textid: str = PathParam(...),
    seq: int = PathParam(..., ge=0),
    params: JuanParallelsGenerationParams | None = None,
) -> JuanParallelsGeneration:
    state: AppState = request.app.state.bkk
    _require_user(request)
    params = params or JuanParallelsGenerationParams()
    rec = state.lookup_bundle(textid)
    if rec is None:
        raise errors.bundle_not_found(textid)

    # The scan is expensive and the asset writer is process-safe but not
    # intended to run concurrently for the same target. Recheck after taking
    # the lock so simultaneous requests collapse into one scan.
    with _GENERATION_LOCK:
        status = _parallel_status(state, textid, seq)
        if status.has_assets:
            return JuanParallelsGeneration(
                textid=textid,
                juan_seq=seq,
                generated=False,
                has_parallels=status.has_parallels,
                message=(
                    "Stored parallel passages were already available."
                    if status.has_parallels
                    else "An on-demand scan was already recorded and found no matching passages."
                ),
            )

        index_path = state.ensure_index()
        if index_path is None:
            raise HTTPException(
                status_code=503,
                detail="cannot generate parallel passages: the corpus index is unavailable",
            )
        scan = {
            "text_id": textid,
            "juan": seq,
            **params.model_dump(),
        }
        try:
            snapshot = capture_index_snapshot(
                index_path,
                command="bkk serve parallels generate",
                algorithm="targeted-trigram-v1",
                scan=scan,
            )
            clusters = discover_parallel_passages(
                index_path,
                target_textid=textid,
                target_juan_seq=seq,
                bucket=params.bucket,
                min_length=params.min_length,
                min_occurrences=params.min_occurrences,
                max_postings=params.max_postings,
                include_contained=params.include_contained,
                context=params.context,
                max_edits=params.max_edits,
            )
            if params.max_length is not None:
                clusters = [
                    cluster
                    for cluster in clusters
                    if cluster.length <= params.max_length
                ]
            assert_index_unchanged(snapshot)
            cluster_count, marker_count, file_count = write_target_parallel_assets(
                clusters,
                rec.bundle_dir,
                textid=textid,
                target_juan_seq=seq,
                name=derive_index_name(index_path),
                provenance=snapshot.provenance,
                write_empty=True,
            )
        except (OSError, RuntimeError, ValueError, sqlite3.DatabaseError) as exc:
            raise HTTPException(
                status_code=503,
                detail=f"cannot generate parallel passages: {exc}",
            ) from exc

        if marker_count:
            message = (
                f"No stored parallels were found. Generated {marker_count} "
                f"parallel passage{'s' if marker_count != 1 else ''} on demand."
            )
        else:
            message = (
                "No stored parallels were found. The on-demand scan completed "
                "but found no matching passages."
            )
        return JuanParallelsGeneration(
            textid=textid,
            juan_seq=seq,
            generated=True,
            has_parallels=marker_count > 0,
            clusters=cluster_count,
            markers=marker_count,
            files=file_count,
            message=message,
        )


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

    paths, _sources = _asset_paths(state, textid, seq)
    rows = _load_markers(state, paths, textid, seq)
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
