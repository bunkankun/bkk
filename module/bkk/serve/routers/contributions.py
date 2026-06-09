"""Read-path endpoint for the live Bluesky contributions feed.

The feed is populated by ``bkk.serve.contributions_feed.ContributionFeed``, a
background poller attached to ``AppState.contributions`` by ``app.py``'s
lifespan. When the subscriber is disabled (e.g. ``BKK_DISABLE_JETSTREAM=1`` or
tests), this endpoint returns an empty list.

Items carry a ``kind`` discriminator (``annotation`` / ``comment`` /
``translation``); the SPA branches on kind to render each shape.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel


router = APIRouter(tags=["contributions"])


class ContributionParentRef(BaseModel):
    uri: str
    cid: str | None = None


class ContributionOut(BaseModel):
    kind: Literal["annotation", "comment", "translation"]
    did: str
    cid: str
    uri: str
    text_id: str
    created_at: str | None = None
    time_us: int

    # Anchor: present for all annotations + translations, optional for comments.
    edition: str | None = None
    marker_id: str | None = None
    offset: int | None = None
    length: int | None = None
    end_marker_id: str | None = None
    end_length: int | None = None

    # Annotation-only.
    payload: dict[str, Any] | None = None
    source_role: str | None = None

    # Comment-only.
    body: str | None = None
    parent: ContributionParentRef | None = None

    # Translation-only.
    translation_id: str | None = None
    text: str | None = None

    # Shared between comment + translation.
    lang: str | None = None


class ContributionsResponse(BaseModel):
    items: list[ContributionOut]
    truncated: bool


@router.get(
    "/contributions",
    response_model=ContributionsResponse,
    summary="Most recent BKK records (annotations, comments, translations) seen on Bluesky",
)
def list_contributions(
    request: Request,
    limit: int = Query(200, ge=1, le=500),
) -> ContributionsResponse:
    feed = request.app.state.bkk.contributions
    if feed is None:
        return ContributionsResponse(items=[], truncated=False)
    items, truncated = feed.snapshot(limit)
    return ContributionsResponse(
        items=[ContributionOut(**i) for i in items],
        truncated=truncated,
    )


__all__ = ["router"]
