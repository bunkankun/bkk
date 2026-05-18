"""Command-line entry point for ``bkk-serve`` / ``python -m bkk.serve``.

Usage::

    bkk-serve --corpus <dir> [--index PATH] [--catalog PATH] [--host H] [--port N]
              [--admin-token TOKEN] [--reload]
              [--upstream-repo ORG/REPO] [--web-dist PATH]
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .config import ServeConfig


def app_factory():
    """Uvicorn import-string entry point used in --reload mode."""
    from .app import create_app
    return create_app(ServeConfig.from_env())


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bkk-serve")
    p.add_argument("--corpus", type=Path, default=None,
                   help="corpus root directory (default: $BKK_CORPUS_ROOT)")
    p.add_argument("--index", type=Path, default=None,
                   help="merged .bkkx index path "
                        "(default: <corpus>/_corpus.bkkx, built on startup if missing)")
    p.add_argument("--catalog", type=Path, default=None, dest="catalog_path",
                   help="catalog .bkkc index path "
                        "(default: <corpus>/_catalog.bkkc)")
    p.add_argument("--host", default=None, help="bind address (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=None, help="port (default: 8000)")
    p.add_argument("--admin-token", default=None,
                   help="bearer token required for /admin/* "
                        "(default: $BKK_ADMIN_TOKEN; if unset, admin is open)")
    p.add_argument("--reload", action="store_true",
                   help="auto-reload on code changes (development only)")
    p.add_argument("--upstream-repo", default=None,
                   help="GitHub upstream texts repo as ORG/REPO "
                        "(default: $BKK_UPSTREAM_REPO; surfaced on GET / for the SPA)")
    p.add_argument("--web-dist", type=Path, default=None,
                   help="directory containing the built SPA to mount at / "
                        "(default: $BKK_WEB_DIST)")
    return p


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from bkk.config import load_rc
    rc = load_rc()
    rc_serve = {**rc.get("global", {}), **rc.get("serve", {})}

    base = ServeConfig.from_env(corpus_root=args.corpus, rc=rc_serve)
    config = base.merge_cli(
        corpus_root=args.corpus,
        index_path=args.index,
        catalog_path=args.catalog_path,
        host=args.host,
        port=args.port,
        admin_token=args.admin_token,
        reload=args.reload or None,
        upstream_repo=args.upstream_repo,
        web_dist=args.web_dist,
    )

    import uvicorn

    if config.reload:
        # uvicorn reload mode requires an import string + a factory.
        # Re-export the resolved config so the factory recovers the same
        # values on each reload cycle.
        os.environ["BKK_CORPUS_ROOT"] = str(config.corpus_root)
        os.environ["BKK_INDEX_PATH"] = str(config.index_path)
        if config.catalog_path is not None:
            os.environ["BKK_CATALOG_PATH"] = str(config.catalog_path)
        os.environ["BKK_HOST"] = config.host
        os.environ["BKK_PORT"] = str(config.port)
        if config.admin_token is not None:
            os.environ["BKK_ADMIN_TOKEN"] = config.admin_token
        if config.upstream_repo is not None:
            os.environ["BKK_UPSTREAM_REPO"] = config.upstream_repo
        if config.web_dist is not None:
            os.environ["BKK_WEB_DIST"] = str(config.web_dist)
        uvicorn.run(
            "bkk.serve.cli:app_factory",
            factory=True,
            host=config.host,
            port=config.port,
            reload=True,
        )
    else:
        from .app import create_app
        app = create_app(config)
        uvicorn.run(app, host=config.host, port=config.port)
    return 0


def main() -> None:
    raise SystemExit(run())
