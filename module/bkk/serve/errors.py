"""HTTPException factories + a JSON-shaped exception handler."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse


def bundle_not_found(textid: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": "bundle_not_found", "textid": textid},
    )


def juan_not_found(textid: str, seq: int) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": "juan_not_found", "textid": textid, "seq": seq},
    )


def index_unavailable(reason: str) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"error": "index_unavailable", "reason": reason},
    )


def bad_request(error: str, **extra: Any) -> HTTPException:
    payload: dict[str, Any] = {"error": error}
    payload.update(extra)
    return HTTPException(status_code=400, detail=payload)


def install_handlers(app: FastAPI) -> None:
    """Normalize HTTPException bodies to a stable ``{error, ...}`` shape."""

    @app.exception_handler(HTTPException)
    async def _on_http_exc(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            body = exc.detail
        else:
            body = {"error": "http_error", "detail": exc.detail}
        return JSONResponse(
            status_code=exc.status_code,
            content=body,
            headers=exc.headers,
        )
