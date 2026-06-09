"""Read-path endpoint for the live Bluesky annotation feed.

The feed is populated by ``bkk.serve.jetstream.ContributionFeed``, a background
WebSocket subscriber attached to ``AppState.contributions`` by ``app.py``'s
lifespan. When the subscriber is disabled (e.g. ``BKK_DISABLE_JETSTREAM=1`` or
tests), this endpoint returns an empty list.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel


router = APIRouter(tags=["contributions"])


class ContributionOut(BaseModel):
    did: str
    cid: str
    uri: str
    text_id: str
    edition: str
    marker_id: str
    offset: int
    length: int
    end_marker_id: str | None = None
    end_length: int | None = None
    payload: dict[str, Any] = {}
    created_at: str | None = None
    time_us: int
    source_role: str | None = None


class ContributionsResponse(BaseModel):
    items: list[ContributionOut]
    truncated: bool


@router.get(
    "/contributions",
    response_model=ContributionsResponse,
    summary="Most recent org.bunkankun.annotation records seen on the Bluesky firehose",
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
