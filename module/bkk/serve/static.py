"""Mount the built SPA at ``/`` with an SPA-style fallback to ``index.html``.

The frontend lives at ``module/web/`` and produces a static bundle in
``module/web/dist/`` via ``npm run build``. When ``ServeConfig.web_dist``
points at that directory (or any directory containing an ``index.html``),
:func:`mount_spa` mounts it after the API routers so backend paths win and
unmatched non-API routes fall back to ``index.html`` for client-side routing.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

log = logging.getLogger("bkk.serve")


# Path prefixes that must always 404 cleanly instead of falling back to
# index.html. Used by errors.install_handlers when web_dist is configured.
API_PREFIXES = (
    "/server-info",
    "/bundles",
    "/texts",
    "/catalog",
    "/search",
    "/overlays",
    "/translations",
    "/recipes",
    "/admin",
    "/by-canonical",
    "/healthz",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def mount_spa(app: FastAPI, web_dist: Path) -> None:
    """Mount ``web_dist`` at ``/``. No-op if the directory is missing."""
    if not web_dist.is_dir() or not (web_dist / "index.html").is_file():
        log.warning("web_dist=%s missing or has no index.html; SPA not mounted", web_dist)
        return
    app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="spa")
    log.info("mounted SPA from %s", web_dist)
