"""Voice maintenance endpoints backed by a precomputed report."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from bkk.voice.problems import (
    VoiceProblemReportError,
    read_voice_problems_report,
)

from fastapi import HTTPException

from ..state import AppState

router = APIRouter(prefix="/voice", tags=["voice"])

def _report_path_or_503(state: AppState):
    path = state.voice_problems_report_path
    if path is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "voice problem report not configured; set [voice].report "
                "in .bkkrc or BKK_VOICE_PROBLEMS_REPORT"
            ),
        )
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail=f"voice problem report missing at {path}; run `bkk voice problems`",
        )
    return path


@router.get("/problems", response_model=dict, summary="List unresolved voice derivation problem markers")
def list_voice_problems(
    request: Request,
    textid: str | None = Query(None, description="restrict to one text id"),
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=5000),
) -> dict[str, Any]:
    state: AppState = request.app.state.bkk
    report_path = _report_path_or_503(state)
    try:
        rows = read_voice_problems_report(report_path)
    except VoiceProblemReportError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if textid is not None:
        rows = [row for row in rows if row.get("textid") == textid]
    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "items": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "returned": len(page),
        "capped": offset + limit < total,
    }
