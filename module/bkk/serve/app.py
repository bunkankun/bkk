"""FastAPI application factory."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from . import errors
from .config import ServeConfig
from .routers import admin as admin_router
from .routers import bundles as bundles_router
from .routers import catalog as catalog_router
from .routers import recipes as recipes_router
from .routers import redirects as redirects_router
from .routers import search as search_router
from .routers import texts as texts_router
from .state import AppState

log = logging.getLogger("bkk.serve")


def create_app(config: ServeConfig) -> FastAPI:
    """Build and return a FastAPI app bound to ``config``."""
    if not config.corpus_root.is_dir():
        raise NotADirectoryError(
            f"corpus_root does not exist or is not a directory: {config.corpus_root}"
        )

    app = FastAPI(
        title="BKK serve",
        description=(
            "Read access, search, and maintenance over a BKK bundle corpus. "
            "See the project design document (bunkankun.md) for the underlying "
            "data model: bundles, manifests, juan files, reference assets, "
            "and recipes."
        ),
        version="0.1.0",
        openapi_tags=[
            {"name": "bundles", "description": "Direct-by-textid bundle access."},
            {"name": "texts", "description": "Bundle access by any identifier in metadata.identifiers."},
            {"name": "catalog", "description": "Browse the corpus with curated metadata filters."},
            {"name": "search", "description": "Variant-aware KWIC search across the corpus."},
            {"name": "recipes", "description": "Recipe-as-request: assemble pinned slices."},
            {"name": "admin", "description": "Maintenance: rebuild indexes, validate bundles."},
            {"name": "redirects", "description": "Cross-tree convenience redirects."},
            {"name": "meta", "description": "Server health and configuration."},
        ],
    )
    app.state.bkk = AppState(config=config)

    errors.install_handlers(app)
    app.include_router(bundles_router.router)
    app.include_router(texts_router.router)
    app.include_router(catalog_router.router)
    app.include_router(search_router.router)
    app.include_router(recipes_router.router)
    app.include_router(admin_router.router)
    app.include_router(redirects_router.router)

    @app.get("/", tags=["meta"], summary="Server identity + corpus pointer")
    def root() -> dict:
        return {
            "service": "bkk-serve",
            "version": "0.1.0",
            "corpus_root": str(config.corpus_root),
            "index_path": str(config.index_path),
            "docs": "/docs",
            "openapi": "/openapi.json",
        }

    @app.get("/healthz", tags=["meta"], summary="Liveness probe")
    def healthz() -> dict:
        return {"status": "ok"}

    if config.admin_token is None:
        log.warning(
            "/admin/* endpoints are unauthenticated; "
            "set BKK_ADMIN_TOKEN to require a bearer token"
        )

    return app
