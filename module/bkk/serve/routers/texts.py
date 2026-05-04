"""Identifier-resolved text access under ``/texts/{id}/...``.

This router is a thin alias over :mod:`bkk.serve.routers.bundles`: it resolves
the URL identifier through :class:`IdentifierResolver` and then delegates to
the same handlers used by the direct ``/bundles/{textid}`` tree.

Collision UX (per the user-validated decision recorded in the project plan):

1. zero matches → 404 ``identifier_not_found``
2. exactly one match → forward to the bundles handler
3. multiple matches → prefer the candidate with no ``metadata.base_edition``
   (the canonical "master" view); if exactly one candidate qualifies, use it
4. still ambiguous → ``300 Multiple Choices`` with a body listing every
   candidate so the client can pick one explicitly via
   ``/bundles/{textid}/...`` or ``/texts/{id}@{edition_short}/...``

The ``@edition_short`` suffix on ``id`` always bypasses resolution: it pins
the lookup to a specific edition manifest. In v1 we only support the master
manifest (no separate ``editions/<short>/`` manifest is present in the test
corpus), so the suffix narrows the candidate set rather than redirecting to a
nested manifest.
"""

from __future__ import annotations

from fastapi import APIRouter, Path as PathParam, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from .. import _examples as ex
from .. import errors
from ..resolver import BundleRef, IdentifierResolver
from ..schemas import (
    BundleAssetsResponse,
    BundleSummary,
    CollisionCandidate,
    JuanSliceOut,
    MultipleChoicesResponse,
)
from . import bundles as bundles_router

router = APIRouter(prefix="/texts", tags=["texts"])


def _split_edition(raw: str) -> tuple[str, str | None]:
    """Split ``id@edition`` into ``(id, edition)``; ``edition`` may be None."""
    if "@" in raw:
        head, _, tail = raw.partition("@")
        return head.strip(), (tail.strip() or None)
    return raw.strip(), None


def _candidates_response(
    identifier: str, candidates: list[BundleRef]
) -> JSONResponse:
    body = MultipleChoicesResponse(
        identifier=identifier,
        candidates=[
            CollisionCandidate(
                textid=c.textid,
                canonical_identifier=c.canonical_identifier,
                edition_short=c.edition_short,
                base_edition=c.base_edition,
                title=c.title,
                link=f"/bundles/{c.textid}",
            )
            for c in candidates
        ],
    )
    return JSONResponse(status_code=300, content=body.model_dump())


def _resolve(
    resolver: IdentifierResolver, identifier: str
) -> tuple[BundleRef | None, list[BundleRef]]:
    """Return ``(chosen, all_candidates)``.

    ``chosen`` is None when the identifier is unknown OR when the candidate
    set remains ambiguous after the base_edition tiebreak. Callers use the
    full candidate list to render the 300 response.
    """
    head, edition = _split_edition(identifier)
    candidates = resolver.lookup(head)
    if edition is not None:
        candidates = [c for c in candidates if c.edition_short == edition]
    if not candidates:
        return None, []
    chosen = resolver.disambiguate(candidates)
    return chosen, candidates


def _resolve_or_respond(
    request: Request, identifier: str
) -> tuple[BundleRef | None, JSONResponse | None]:
    """Run resolution; return either ``(ref, None)`` or ``(None, response)``."""
    resolver: IdentifierResolver = request.app.state.bkk.resolver
    chosen, candidates = _resolve(resolver, identifier)
    if not candidates:
        raise errors.bad_request(
            "identifier_not_found", identifier=identifier
        )
    if chosen is None:
        return None, _candidates_response(identifier, candidates)
    return chosen, None


@router.get(
    "/{identifier}",
    response_model=BundleSummary,
    responses={300: {"model": MultipleChoicesResponse}},
    summary="Bundle summary by any identifier from metadata.identifiers",
)
def get_text(
    request: Request, identifier: str = PathParam(..., openapi_examples=ex.IDENTIFIER)
):
    ref, multi = _resolve_or_respond(request, identifier)
    if multi is not None:
        return multi
    return bundles_router.get_bundle(request, textid=ref.textid)


@router.get(
    "/{identifier}/manifest",
    response_model=dict,
    responses={300: {"model": MultipleChoicesResponse}},
    summary="Full master manifest by identifier",
)
def get_text_manifest(
    request: Request, identifier: str = PathParam(..., openapi_examples=ex.IDENTIFIER)
):
    ref, multi = _resolve_or_respond(request, identifier)
    if multi is not None:
        return multi
    return bundles_router.get_manifest(request, textid=ref.textid)


@router.get(
    "/{identifier}/juan",
    response_model=list,
    responses={300: {"model": MultipleChoicesResponse}},
    summary="Juan list by identifier",
)
def list_text_juan(
    request: Request, identifier: str = PathParam(..., openapi_examples=ex.IDENTIFIER)
):
    ref, multi = _resolve_or_respond(request, identifier)
    if multi is not None:
        return multi
    return bundles_router.list_juan(request, textid=ref.textid)


@router.get(
    "/{identifier}/juan/{seq}",
    response_model=dict,
    responses={300: {"model": MultipleChoicesResponse}},
    summary="Whole juan by identifier",
)
def get_text_juan(
    request: Request,
    identifier: str = PathParam(..., openapi_examples=ex.IDENTIFIER),
    seq: int = PathParam(..., ge=0, openapi_examples=ex.SEQ),
):
    ref, multi = _resolve_or_respond(request, identifier)
    if multi is not None:
        return multi
    return bundles_router.get_juan(request, textid=ref.textid, seq=seq)


@router.get(
    "/{identifier}/juan/{seq}/slice",
    response_model=JuanSliceOut,
    responses={300: {"model": MultipleChoicesResponse}},
    summary="Slice a juan bucket by markers, char range, or TOC entry, by identifier",
)
def get_text_juan_slice(
    request: Request,
    identifier: str = PathParam(..., openapi_examples=ex.IDENTIFIER),
    seq: int = PathParam(..., ge=0, openapi_examples=ex.SEQ),
    bucket: str = Query("body", openapi_examples=ex.BUCKET),
    from_: str | None = Query(None, alias="from", openapi_examples=ex.SLICE_FROM_MARKER),
    to: str | None = Query(None, openapi_examples=ex.SLICE_TO_MARKER),
    offset: int | None = Query(None, ge=0, openapi_examples=ex.SLICE_OFFSET),
    length: int | None = Query(None, ge=0, openapi_examples=ex.SLICE_LENGTH),
    toc: str | None = Query(None, openapi_examples=ex.SLICE_TOC),
):
    ref, multi = _resolve_or_respond(request, identifier)
    if multi is not None:
        return multi
    return bundles_router.get_juan_slice(
        request,
        textid=ref.textid,
        seq=seq,
        bucket=bucket,
        from_=from_,
        to=to,
        offset=offset,
        length=length,
        toc=toc,
    )


@router.get(
    "/{identifier}/juan/{seq}/{bucket}",
    response_model=dict,
    responses={300: {"model": MultipleChoicesResponse}},
    summary="Bucket of a juan by identifier",
)
def get_text_juan_bucket(
    request: Request,
    identifier: str = PathParam(..., openapi_examples=ex.IDENTIFIER),
    seq: int = PathParam(..., ge=0, openapi_examples=ex.SEQ),
    bucket: str = PathParam(..., openapi_examples=ex.BUCKET),
):
    ref, multi = _resolve_or_respond(request, identifier)
    if multi is not None:
        return multi
    return bundles_router.get_juan_bucket(
        request, textid=ref.textid, seq=seq, bucket=bucket
    )


@router.get(
    "/{identifier}/juan/{seq}/{bucket}/text",
    response_class=PlainTextResponse,
    responses={300: {"model": MultipleChoicesResponse}},
    summary="Raw text of a juan bucket by identifier",
)
def get_text_juan_bucket_text(
    request: Request,
    identifier: str = PathParam(..., openapi_examples=ex.IDENTIFIER),
    seq: int = PathParam(..., ge=0, openapi_examples=ex.SEQ),
    bucket: str = PathParam(..., openapi_examples=ex.BUCKET),
):
    ref, multi = _resolve_or_respond(request, identifier)
    if multi is not None:
        return multi
    return bundles_router.get_juan_bucket_text(
        request, textid=ref.textid, seq=seq, bucket=bucket
    )


@router.get(
    "/{identifier}/juan/{seq}/{bucket}/markers",
    response_model=list,
    responses={300: {"model": MultipleChoicesResponse}},
    summary="Juan bucket markers by identifier",
)
def get_text_juan_bucket_markers(
    request: Request,
    identifier: str = PathParam(..., openapi_examples=ex.IDENTIFIER),
    seq: int = PathParam(..., ge=0, openapi_examples=ex.SEQ),
    bucket: str = PathParam(..., openapi_examples=ex.BUCKET),
    type: str | None = Query(None, openapi_examples=ex.MARKER_TYPE),
    from_: int | None = Query(None, alias="from", ge=0, openapi_examples=ex.FROM),
    to: int | None = Query(None, ge=0, openapi_examples=ex.TO),
):
    ref, multi = _resolve_or_respond(request, identifier)
    if multi is not None:
        return multi
    return bundles_router.get_juan_bucket_markers(
        request,
        textid=ref.textid,
        seq=seq,
        bucket=bucket,
        type=type,
        from_=from_,
        to=to,
    )


@router.get(
    "/{identifier}/assets",
    response_model=BundleAssetsResponse,
    responses={300: {"model": MultipleChoicesResponse}},
    summary="Bundle assets by identifier",
)
def list_text_assets(
    request: Request, identifier: str = PathParam(..., openapi_examples=ex.IDENTIFIER)
):
    ref, multi = _resolve_or_respond(request, identifier)
    if multi is not None:
        return multi
    return bundles_router.list_assets(request, textid=ref.textid)


@router.get(
    "/{identifier}/assets/{name}",
    response_class=Response,
    responses={300: {"model": MultipleChoicesResponse}},
    summary="One asset by identifier",
)
def get_text_asset(
    request: Request,
    identifier: str = PathParam(..., openapi_examples=ex.IDENTIFIER),
    name: str = PathParam(..., openapi_examples=ex.ASSET_NAME),
):
    ref, multi = _resolve_or_respond(request, identifier)
    if multi is not None:
        return multi
    return bundles_router.get_asset(request, textid=ref.textid, name=name)
