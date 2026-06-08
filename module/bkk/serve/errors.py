"""HTTPException factories + a JSON-shaped exception handler."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


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


def install_handlers(app: FastAPI, *, spa_index: Path | None = None) -> None:
    """Normalize HTTPException bodies to a stable ``{error, ...}`` shape.

    If ``spa_index`` is provided, 404s on non-API paths return that file so
    client-side routes work after a hard refresh.
    """
    from .static import API_PREFIX, NON_API_BACKEND_PATHS

    @app.exception_handler(StarletteHTTPException)
    async def _on_http_exc(request: Request, exc: StarletteHTTPException):
        if (
            spa_index is not None
            and exc.status_code == 404
            and not _is_backend_path(request.url.path, API_PREFIX, NON_API_BACKEND_PATHS)
        ):
            return FileResponse(spa_index)
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            body = exc.detail
        else:
            body = {"error": "http_error", "detail": exc.detail}
        return JSONResponse(
            status_code=exc.status_code,
            content=body,
            headers=exc.headers,
        )


def _is_backend_path(path: str, api_prefix: str, extras: tuple[str, ...]) -> bool:
    if path == api_prefix or path.startswith(api_prefix + "/"):
        return True
    return any(path == p or path.startswith(p + "/") for p in extras)
