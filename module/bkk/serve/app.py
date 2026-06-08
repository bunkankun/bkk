"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import errors
from .config import ServeConfig
from .routers import admin as admin_router
from .routers import annotations as annotations_router
from .routers import annotations_write as annotations_write_router
from .routers import auth as auth_router
from .routers import bundles as bundles_router
from .routers import catalog as catalog_router
from .routers import core as core_router
from .routers import core_edit as core_edit_router
from .routers import recipes as recipes_router
from .routers import redirects as redirects_router
from .routers import search as search_router
from .routers import texts as texts_router
from .routers import translations as translations_router
from .routers import workspace as workspace_router
from .state import AppState
from .static import mount_spa


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
            {"name": "annotations", "description": "Per-juan annotations pinned to text offsets (sibling *.ann.yaml)."},
            {"name": "annotations-write", "description": "Compose annotations: Bluesky session + record creation."},
            {"name": "catalog", "description": "Browse the corpus with curated metadata filters."},
            {"name": "core", "description": "Browse the bkk-core knowledge layer (concepts, graphs, words, …)."},
            {"name": "search", "description": "Variant-aware KWIC search across the corpus."},
            {"name": "translations", "description": "Translation overlay discovery and alignment."},
            {"name": "recipes", "description": "Recipe-as-request: assemble pinned slices."},
            {"name": "auth", "description": "GitHub login and per-user BKK workspace setup."},
            {"name": "workspace", "description": "GitHub-backed user workspace files."},
            {"name": "admin", "description": "Maintenance: rebuild indexes, validate bundles."},
            {"name": "redirects", "description": "Cross-tree convenience redirects."},
            {"name": "meta", "description": "Server health and configuration."},
        ],
    )
    app.state.bkk = AppState(config=config)

    if config.reload:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

    spa_index = (
        config.web_dist / "index.html"
        if config.web_dist and (config.web_dist / "index.html").is_file()
        else None
    )
    errors.install_handlers(app, spa_index=spa_index)
    # Register specific sub-routes BEFORE bundles/texts so they win over the
    # generic /juan/{seq}/{bucket} wildcard in bundles_router.
    app.include_router(annotations_router.router)
    app.include_router(annotations_write_router.router)
    app.include_router(translations_router.router)
    app.include_router(bundles_router.router)
    app.include_router(texts_router.router)
    app.include_router(catalog_router.router)
    app.include_router(core_router.router)
    app.include_router(core_edit_router.router)
    app.include_router(search_router.router)
    app.include_router(recipes_router.router)
    app.include_router(auth_router.router)
    # Alias under /api so the GitHub OAuth callback registered for vite dev
    # (http://localhost:5173/api/auth/github/callback) also resolves when
    # bkk serve hosts the dist on :5173 without vite in front.
    app.include_router(auth_router.router, prefix="/api")
    app.include_router(workspace_router.router)
    app.include_router(admin_router.router)
    app.include_router(redirects_router.router)

    spa_will_mount = (
        config.web_dist is not None
        and config.web_dist.is_dir()
        and (config.web_dist / "index.html").is_file()
    )

    if not spa_will_mount:
        @app.get("/", tags=["meta"], summary="Server identity + corpus pointer")
        def root() -> dict:
            return {
                "service": "bkk-serve",
                "version": "0.1.0",
                "corpus_root": str(config.corpus_root),
                "index_path": str(config.index_path),
                "catalog_path": str(config.catalog_path),
                "upstream_repo": config.upstream_repo,
                "docs": "/docs",
                "openapi": "/openapi.json",
            }

    @app.get("/healthz", tags=["meta"], summary="Liveness probe")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/server-welcome", tags=["meta"], summary="Welcome markdown (if configured)")
    def server_welcome() -> dict:
        path = config.welcome_path
        if path is None:
            raise HTTPException(status_code=404, detail="no welcome message configured")
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise HTTPException(
                status_code=404, detail=f"welcome file not found: {path}"
            )
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"cannot read welcome file: {exc}"
            )
        return {"markdown": text}

    @app.get("/server-info", tags=["meta"], summary="Server identity + corpus pointer (always JSON)")
    def server_info() -> dict:
        return {
            "service": "bkk-serve",
            "version": "0.1.0",
            "corpus_root": str(config.corpus_root),
            "index_path": str(config.index_path),
            "catalog_path": str(config.catalog_path),
            "upstream_repo": config.upstream_repo,
            "docs": "/docs",
            "openapi": "/openapi.json",
        }

    if config.web_dist is not None:
        mount_spa(app, config.web_dist)

    return app
