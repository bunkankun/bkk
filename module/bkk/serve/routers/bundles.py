"""Direct-by-textid bundle endpoints under ``/bundles``."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Path as PathParam, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response

from .. import _examples as ex
from .. import errors
from .._hits import hit_out
from ..resolver import BundleRecord
from ..schemas import (
    BundleAsset,
    BundleAssetsResponse,
    BundleListResponse,
    BundleSearchResponse,
    BundleSummary,
    EditionInfo,
    JuanSliceOut,
)
from ..state import AppState
from .. import selection

router = APIRouter(prefix="/bundles", tags=["bundles"])


def _record(state: AppState, textid: str) -> BundleRecord:
    rec = state.lookup_bundle(textid)
    if rec is None:
        raise errors.bundle_not_found(textid)
    return rec


def _summary_from_manifest(textid: str, manifest: dict[str, Any]) -> BundleSummary:
    metadata = manifest.get("metadata") or {}
    edition_block = metadata.get("edition") or {}
    editions = [
        EditionInfo(short=e.get("short", ""), label=e.get("label"))
        for e in (manifest.get("editions") or [])
        if isinstance(e, dict)
    ]
    return BundleSummary(
        textid=textid,
        canonical_identifier=manifest.get("canonical_identifier"),
        title=metadata.get("title"),
        edition_short=edition_block.get("short") if isinstance(edition_block, dict) else None,
        editions=editions,
    )


@router.get("", response_model=BundleListResponse, summary="List bundles in the corpus")
def list_bundles(
    request: Request,
    prefix: str | None = Query(
        None,
        description="restrict to textids starting with PREFIX",
        openapi_examples=ex.PREFIX,
    ),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> BundleListResponse:
    state: AppState = request.app.state.bkk
    records = state.cache.get().records
    if prefix:
        records = [r for r in records if r.textid.startswith(prefix)]
    total = len(records)
    page = records[offset:offset + limit]
    summaries = [_summary_from_manifest(r.textid, r.manifest) for r in page]
    return BundleListResponse(
        bundles=summaries, total=total, offset=offset, limit=limit
    )


@router.get(
    "/{textid}",
    response_model=BundleSummary,
    summary="Bundle summary (textid, canonical id, title, editions)",
)
def get_bundle(request: Request, textid: str = PathParam(..., openapi_examples=ex.TEXTID)) -> BundleSummary:
    rec = _record(request.app.state.bkk, textid)
    return _summary_from_manifest(rec.textid, rec.manifest)


@router.get(
    "/{textid}/manifest",
    response_model=dict,
    summary="Full master manifest for the bundle",
)
def get_manifest(request: Request, textid: str = PathParam(..., openapi_examples=ex.TEXTID)) -> dict[str, Any]:
    state: AppState = request.app.state.bkk
    rec = _record(state, textid)
    return _manifest_with_image_overrides(rec.manifest, state)


_BUCKET_ORDER = {"front": 0, "body": 1, "back": 2}


@router.get(
    "/{textid}/search",
    response_model=BundleSearchResponse,
    summary="Substring search within one bundle's .bkkx, in text order",
)
def bundle_search(
    request: Request,
    textid: str = PathParam(..., openapi_examples=ex.TEXTID),
    q: str = Query(..., min_length=1, description="substring query (NFC-normalised server-side)"),
    context: int = Query(7, ge=0, le=200, description="KWIC context window each side"),
    limit: int = Query(200, ge=1, le=2000),
    master_only: bool = Query(
        False,
        description="when true, drop witness-side hits and count only master matches against the cap",
    ),
) -> BundleSearchResponse:
    state: AppState = request.app.state.bkk
    _record(state, textid)  # 404 if unknown textid
    # Prefer the per-bundle .bkkx; fall back to the corpus index with textid filter.
    ix = state.open_bundle_index(textid) or state.open_index()
    if ix is None:
        raise errors.index_unavailable(state._index_error or "index not built")
    cap = state.config.max_search_hits
    try:
        cand, total = ix.candidates_and_total(q)
        # Iterate hits up to cap+1; the textid filter in ix.search guarantees
        # we only materialise master/witness rows from this bundle, so even on
        # the corpus-index fallback we bound the work to this text.
        hits = []
        for h in ix.search(q, context=context, textid=textid, candidates=cand):
            if master_only and h.matched_via != "master":
                continue
            hits.append(h)
            if len(hits) > cap:
                return BundleSearchResponse(query=q, total=total, capped=True, hits=[])
    finally:
        ix.close()
    hits.sort(key=lambda h: (h.juan_seq, _BUCKET_ORDER.get(h.bucket, 99), h.master_offset))
    return BundleSearchResponse(
        query=q,
        total=len(hits),
        capped=False,
        hits=[hit_out(textid, h) for h in hits[:limit]],
    )


def _manifest_with_image_overrides(
    manifest: dict[str, Any], state: AppState,
) -> dict[str, Any]:
    override = state.config.image_base_urls
    if not override:
        return manifest
    # Don't mutate the cached manifest; rebuild only the touched subtrees.
    metadata = dict(manifest.get("metadata") or {})
    existing = dict(metadata.get("image_base_urls") or {})
    existing.update(override)
    metadata["image_base_urls"] = existing
    return {**manifest, "metadata": metadata}


def _image_base_url(
    manifest: dict[str, Any], edition: str,
) -> str | None:
    metadata = manifest.get("metadata") or {}
    bases = metadata.get("image_base_urls") or {}
    if not isinstance(bases, dict):
        return None
    for key in (edition, edition.lower(), edition.upper()):
        value = bases.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _local_file_image_path(base_url: str, image_path: str) -> Path | None:
    parsed = urlparse(base_url)
    if parsed.scheme != "file":
        return None
    if parsed.netloc not in ("", "localhost"):
        return None
    base = Path(unquote(parsed.path)).resolve()
    target = (base / image_path).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise errors.bad_request("bad_image_path", image_path=image_path)
    return target


@router.get(
    "/{textid}/juan",
    response_model=list,
    summary="List of juan declared in the manifest's assets.parts",
)
def list_juan(request: Request, textid: str = PathParam(..., openapi_examples=ex.TEXTID)) -> list[dict[str, Any]]:
    rec = _record(request.app.state.bkk, textid)
    parts = (rec.manifest.get("assets") or {}).get("parts") or []
    return list(parts)


@router.get(
    "/{textid}/juan/{seq}",
    response_model=dict,
    summary="Whole juan (front/body/back + markers) by sequence number",
)
def get_juan(
    request: Request,
    textid: str = PathParam(..., openapi_examples=ex.TEXTID),
    seq: int = PathParam(..., ge=0, openapi_examples=ex.SEQ),
) -> dict[str, Any]:
    rec = _record(request.app.state.bkk, textid)
    return selection.load_juan_file(rec.bundle_dir, rec.manifest, rec.textid, seq)


VALID_BUCKETS = ("front", "body", "back")


@router.get(
    "/{textid}/juan/{seq}/slice",
    response_model=JuanSliceOut,
    summary="Slice a juan bucket by markers, char range, or TOC entry",
)
def get_juan_slice(
    request: Request,
    textid: str = PathParam(..., openapi_examples=ex.TEXTID),
    seq: int = PathParam(..., ge=0, openapi_examples=ex.SEQ),
    bucket: str = Query(
        "body",
        description="bucket to slice (front | body | back)",
        openapi_examples=ex.BUCKET,
    ),
    from_: str | None = Query(
        None,
        alias="from",
        description="marker id to start from (paired with ?to=)",
        openapi_examples=ex.SLICE_FROM_MARKER,
    ),
    to: str | None = Query(
        None,
        description="marker id to slice to (paired with ?from=)",
        openapi_examples=ex.SLICE_TO_MARKER,
    ),
    offset: int | None = Query(
        None,
        ge=0,
        description="char offset within bucket (paired with ?length=)",
        openapi_examples=ex.SLICE_OFFSET,
    ),
    length: int | None = Query(
        None,
        ge=0,
        description="char length from offset (paired with ?offset=)",
        openapi_examples=ex.SLICE_LENGTH,
    ),
    toc: str | None = Query(
        None,
        description="TOC marker id; bucket and span come from the manifest entry",
        openapi_examples=ex.SLICE_TOC,
    ),
) -> JuanSliceOut:
    rec = _record(request.app.state.bkk, textid)
    forms_used = sum(
        1 for f in (from_ or to, offset is not None or length is not None, toc) if f
    )
    if forms_used > 1:
        raise errors.bad_request(
            "slice_form_conflict",
            hint="use exactly one of (from+to), (offset+length), or toc",
        )

    if toc is not None:
        def _loader(s: int) -> dict[str, Any]:
            return selection.load_juan_file(rec.bundle_dir, rec.manifest, rec.textid, s)

        sl = selection.slice_by_toc(rec.manifest, _loader, toc)
        if sl.juan_seq != seq:
            raise errors.bad_request(
                "toc_seq_mismatch",
                marker_id=toc,
                requested_seq=seq,
                toc_seq=sl.juan_seq,
            )
    else:
        juan = selection.load_juan_file(rec.bundle_dir, rec.manifest, rec.textid, seq)
        if from_ is not None or to is not None:
            if not (from_ and to):
                raise errors.bad_request(
                    "marker_range_requires_both",
                    **{"from": from_, "to": to},
                )
            sl = selection.slice_by_markers(juan, seq, from_, to, bucket=bucket)
        elif offset is not None or length is not None:
            if offset is None or length is None:
                raise errors.bad_request(
                    "offset_range_requires_both",
                    offset=offset, length=length,
                )
            sl = selection.slice_by_offset(juan, seq, offset, length, bucket=bucket)
        else:
            sl = selection.slice_whole(juan, seq, bucket=bucket)

    return JuanSliceOut(
        textid=textid,
        juan_seq=sl.juan_seq,
        bucket=sl.bucket,
        span=[sl.span[0], sl.span[1]],
        text=sl.text,
        markers=sl.markers,
    )


@router.get(
    "/{textid}/juan/{seq}/{bucket}",
    response_model=dict,
    summary="One bucket of a juan: front, body, or back",
)
def get_juan_bucket(
    request: Request,
    textid: str = PathParam(..., openapi_examples=ex.TEXTID),
    seq: int = PathParam(..., ge=0, openapi_examples=ex.SEQ),
    bucket: str = PathParam(..., openapi_examples=ex.BUCKET),
) -> dict[str, Any]:
    if bucket not in VALID_BUCKETS:
        raise errors.bad_request(
            "bad_bucket", bucket=bucket, valid=list(VALID_BUCKETS)
        )
    rec = _record(request.app.state.bkk, textid)
    juan = selection.load_juan_file(rec.bundle_dir, rec.manifest, rec.textid, seq)
    return juan.get(bucket) or {}


@router.get(
    "/{textid}/juan/{seq}/{bucket}/text",
    response_class=PlainTextResponse,
    summary="Raw UTF-8 text of one bucket of a juan",
)
def get_juan_bucket_text(
    request: Request,
    textid: str = PathParam(..., openapi_examples=ex.TEXTID),
    seq: int = PathParam(..., ge=0, openapi_examples=ex.SEQ),
    bucket: str = PathParam(..., openapi_examples=ex.BUCKET),
) -> PlainTextResponse:
    if bucket not in VALID_BUCKETS:
        raise errors.bad_request(
            "bad_bucket", bucket=bucket, valid=list(VALID_BUCKETS)
        )
    rec = _record(request.app.state.bkk, textid)
    juan = selection.load_juan_file(rec.bundle_dir, rec.manifest, rec.textid, seq)
    body = juan.get(bucket) or {}
    text = body.get("text") if isinstance(body, dict) else ""
    return PlainTextResponse(text or "", media_type="text/plain; charset=utf-8")


@router.get(
    "/{textid}/juan/{seq}/{bucket}/markers",
    response_model=list,
    summary="Markers in a bucket; filter by ?type and master-offset window",
)
def get_juan_bucket_markers(
    request: Request,
    textid: str = PathParam(..., openapi_examples=ex.TEXTID),
    seq: int = PathParam(..., ge=0, openapi_examples=ex.SEQ),
    bucket: str = PathParam(..., openapi_examples=ex.BUCKET),
    type: str | None = Query(
        None,
        description="restrict to markers with this type",
        openapi_examples=ex.MARKER_TYPE,
    ),
    from_: int | None = Query(
        None,
        alias="from",
        ge=0,
        description="inclusive master-offset lower bound",
        openapi_examples=ex.FROM,
    ),
    to: int | None = Query(
        None,
        ge=0,
        description="exclusive master-offset upper bound",
        openapi_examples=ex.TO,
    ),
) -> list[dict[str, Any]]:
    if bucket not in VALID_BUCKETS:
        raise errors.bad_request(
            "bad_bucket", bucket=bucket, valid=list(VALID_BUCKETS)
        )
    rec = _record(request.app.state.bkk, textid)
    juan = selection.load_juan_file(rec.bundle_dir, rec.manifest, rec.textid, seq)
    body = juan.get(bucket) or {}
    markers = body.get("markers") or [] if isinstance(body, dict) else []
    out: list[dict[str, Any]] = []
    for m in markers:
        if not isinstance(m, dict):
            continue
        if type is not None and m.get("type") != type:
            continue
        offset = m.get("master_offset")
        if from_ is not None and isinstance(offset, int) and offset < from_:
            continue
        if to is not None and isinstance(offset, int) and offset >= to:
            continue
        out.append(m)
    return out


@router.get(
    "/{textid}/images/{edition}/{image_path:path}",
    response_class=Response,
    summary="Fetch a local file-backed page image declared by image_base_urls",
)
def get_local_image(
    request: Request,
    textid: str = PathParam(..., openapi_examples=ex.TEXTID),
    edition: str = PathParam(..., description="KRP page-break edition short id"),
    image_path: str = PathParam(..., description="image path from the page-break marker"),
) -> Response:
    state: AppState = request.app.state.bkk
    rec = _record(state, textid)
    manifest = _manifest_with_image_overrides(rec.manifest, state)
    base_url = _image_base_url(manifest, edition)
    if base_url is None:
        raise errors.bad_request("image_base_url_not_declared", textid=textid, edition=edition)
    path = _local_file_image_path(base_url, image_path)
    if path is None:
        raise errors.bad_request("image_base_url_not_file", textid=textid, edition=edition)
    if not path.exists() or not path.is_file():
        raise errors.bad_request(
            "image_missing_on_disk",
            textid=textid,
            edition=edition,
            image_path=image_path,
        )
    return FileResponse(path)


def _asset_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    refs = (manifest.get("assets") or {}).get("references") or []
    return [r for r in refs if isinstance(r, dict)]


@router.get(
    "/{textid}/assets",
    response_model=BundleAssetsResponse,
    summary="Reference assets declared on the bundle's manifest",
)
def list_assets(
    request: Request, textid: str = PathParam(..., openapi_examples=ex.TEXTID)
) -> BundleAssetsResponse:
    rec = _record(request.app.state.bkk, textid)
    out: list[BundleAsset] = []
    for entry in _asset_entries(rec.manifest):
        name = entry.get("filename") or entry.get("name")
        if not isinstance(name, str):
            continue
        path = rec.bundle_dir / name
        size = path.stat().st_size if path.exists() else None
        out.append(
            BundleAsset(
                name=name,
                role=entry.get("role"),
                hash=entry.get("hash"),
                size=size,
            )
        )
    return BundleAssetsResponse(textid=textid, assets=out)


@router.get(
    "/{textid}/assets/{name}",
    response_class=Response,
    summary="Fetch one declared reference asset by filename",
)
def get_asset(
    request: Request,
    textid: str = PathParam(..., openapi_examples=ex.TEXTID),
    name: str = PathParam(..., openapi_examples=ex.ASSET_NAME),
) -> Response:
    rec = _record(request.app.state.bkk, textid)
    declared = {
        entry.get("filename") or entry.get("name")
        for entry in _asset_entries(rec.manifest)
    }
    if name not in declared:
        raise errors.bad_request(
            "asset_not_declared", textid=textid, name=name
        )
    if "/" in name or ".." in name:
        raise errors.bad_request("bad_asset_name", name=name)
    path = rec.bundle_dir / name
    if not path.exists() or not path.is_file():
        raise errors.bad_request(
            "asset_missing_on_disk", textid=textid, name=name
        )
    return FileResponse(path)
