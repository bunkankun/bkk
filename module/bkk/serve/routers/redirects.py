"""Cross-tree convenience redirects."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from bkk.serve import _examples as ex, errors
from bkk.serve.resolver import IdentifierResolver
from bkk.serve.schemas import CollisionCandidate, MultipleChoicesResponse

router = APIRouter(tags=["redirects"])


@router.get(
    "/by-canonical",
    summary="Redirect to /api/bundles/{textid} for a canonical_identifier",
    responses={
        302: {"description": "redirect to the resolved bundle"},
        300: {"model": MultipleChoicesResponse},
        404: {"description": "no bundle carries this canonical_identifier"},
    },
)
def by_canonical(
    request: Request,
    id: str = Query(
        ...,
        description="canonical_identifier as it appears in a manifest",
        openapi_examples=ex.CANONICAL,
    ),
):
    resolver: IdentifierResolver = request.app.state.bkk.resolver
    candidates = resolver.lookup(id)
    if not candidates:
        raise errors.bad_request("identifier_not_found", identifier=id)
    chosen = resolver.disambiguate(candidates)
    if chosen is None:
        body = MultipleChoicesResponse(
            identifier=id,
            candidates=[
                CollisionCandidate(
                    textid=c.textid,
                    canonical_identifier=c.canonical_identifier,
                    edition_short=c.edition_short,
                    base_edition=c.base_edition,
                    title=c.title,
                    link=f"/api/bundles/{c.textid}",
                )
                for c in candidates
            ],
        )
        return JSONResponse(status_code=300, content=body.model_dump())
    return RedirectResponse(url=f"/api/bundles/{chosen.textid}", status_code=302)
