"""Direct-by-textid bundle endpoints under ``/bundles``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Path as PathParam, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response

from bkk.index.merge import discover_bundles

from .. import _examples as ex
from .. import errors
from ..schemas import (
    BundleAsset,
    BundleAssetsResponse,
    BundleListResponse,
    BundleSummary,
    EditionInfo,
    JuanSliceOut,
)
from .. import selection

router = APIRouter(prefix="/bundles", tags=["bundles"])


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _bundle_dir(corpus_root: Path, textid: str) -> Path:
    bundle = corpus_root / textid
    manifest = bundle / f"{textid}.manifest.yaml"
    if not manifest.exists():
        raise errors.bundle_not_found(textid)
    return bundle


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
    state = request.app.state.bkk
    bundle_dirs = discover_bundles(state.corpus_root, prefix=prefix)
    total = len(bundle_dirs)
    page = bundle_dirs[offset:offset + limit]
    summaries: list[BundleSummary] = []
    for bd in page:
        manifest_path = bd / f"{bd.name}.manifest.yaml"
        try:
            manifest = _load_yaml(manifest_path)
        except yaml.YAMLError:
            # Surface the bundle in the listing with empty metadata rather than
            # 500ing the whole list call when a single manifest is malformed.
            manifest = {}
        summaries.append(_summary_from_manifest(bd.name, manifest))
    return BundleListResponse(
        bundles=summaries, total=total, offset=offset, limit=limit
    )


@router.get(
    "/{textid}",
    response_model=BundleSummary,
    summary="Bundle summary (textid, canonical id, title, editions)",
)
def get_bundle(request: Request, textid: str = PathParam(..., openapi_examples=ex.TEXTID)) -> BundleSummary:
    state = request.app.state.bkk
    bundle = _bundle_dir(state.corpus_root, textid)
    manifest = _load_yaml(bundle / f"{textid}.manifest.yaml")
    return _summary_from_manifest(textid, manifest)


@router.get(
    "/{textid}/manifest",
    response_model=dict,
    summary="Full master manifest for the bundle",
)
def get_manifest(request: Request, textid: str = PathParam(..., openapi_examples=ex.TEXTID)) -> dict[str, Any]:
    state = request.app.state.bkk
    bundle = _bundle_dir(state.corpus_root, textid)
    return _load_yaml(bundle / f"{textid}.manifest.yaml")


@router.get(
    "/{textid}/juan",
    response_model=list,
    summary="List of juan declared in the manifest's assets.parts",
)
def list_juan(request: Request, textid: str = PathParam(..., openapi_examples=ex.TEXTID)) -> list[dict[str, Any]]:
    state = request.app.state.bkk
    bundle = _bundle_dir(state.corpus_root, textid)
    manifest = _load_yaml(bundle / f"{textid}.manifest.yaml")
    parts = (manifest.get("assets") or {}).get("parts") or []
    return list(parts)


def _juan_path(corpus_root: Path, textid: str, seq: int) -> Path:
    bundle = _bundle_dir(corpus_root, textid)
    manifest = _load_yaml(bundle / f"{textid}.manifest.yaml")
    parts = (manifest.get("assets") or {}).get("parts") or []
    entry = next((p for p in parts if p.get("seq") == seq), None)
    if entry is None:
        raise errors.juan_not_found(textid, seq)
    juan_path = bundle / entry["filename"]
    if not juan_path.exists():
        raise errors.juan_not_found(textid, seq)
    return juan_path


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
    state = request.app.state.bkk
    return _load_yaml(_juan_path(state.corpus_root, textid, seq))


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
    state = request.app.state.bkk
    forms_used = sum(
        1 for f in (from_ or to, offset is not None or length is not None, toc) if f
    )
    if forms_used > 1:
        raise errors.bad_request(
            "slice_form_conflict",
            hint="use exactly one of (from+to), (offset+length), or toc",
        )

    if toc is not None:
        manifest = selection.load_manifest(state.corpus_root, textid)

        def _loader(s: int) -> dict[str, Any]:
            _, j = selection.load_juan(state.corpus_root, textid, s)
            return j

        sl = selection.slice_by_toc(manifest, _loader, toc)
        if sl.juan_seq != seq:
            raise errors.bad_request(
                "toc_seq_mismatch",
                marker_id=toc,
                requested_seq=seq,
                toc_seq=sl.juan_seq,
            )
    else:
        _, juan = selection.load_juan(state.corpus_root, textid, seq)
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
    state = request.app.state.bkk
    juan = _load_yaml(_juan_path(state.corpus_root, textid, seq))
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
    state = request.app.state.bkk
    juan = _load_yaml(_juan_path(state.corpus_root, textid, seq))
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
    state = request.app.state.bkk
    juan = _load_yaml(_juan_path(state.corpus_root, textid, seq))
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
    state = request.app.state.bkk
    bundle = _bundle_dir(state.corpus_root, textid)
    manifest = _load_yaml(bundle / f"{textid}.manifest.yaml")
    out: list[BundleAsset] = []
    for entry in _asset_entries(manifest):
        name = entry.get("filename") or entry.get("name")
        if not isinstance(name, str):
            continue
        path = bundle / name
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
    state = request.app.state.bkk
    bundle = _bundle_dir(state.corpus_root, textid)
    manifest = _load_yaml(bundle / f"{textid}.manifest.yaml")
    declared = {
        entry.get("filename") or entry.get("name")
        for entry in _asset_entries(manifest)
    }
    if name not in declared:
        raise errors.bad_request(
            "asset_not_declared", textid=textid, name=name
        )
    if "/" in name or ".." in name:
        raise errors.bad_request("bad_asset_name", name=name)
    path = bundle / name
    if not path.exists() or not path.is_file():
        raise errors.bad_request(
            "asset_missing_on_disk", textid=textid, name=name
        )
    return FileResponse(path)
