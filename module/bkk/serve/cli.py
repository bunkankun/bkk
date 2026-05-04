"""Command-line entry point for ``bkk-serve`` / ``python -m bkk.serve``.

Usage::

    bkk-serve --corpus <dir> [--index PATH] [--host H] [--port N]
              [--admin-token TOKEN] [--reload]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import ServeConfig


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bkk-serve")
    p.add_argument("--corpus", type=Path, default=None,
                   help="corpus root directory (default: $BKK_CORPUS_ROOT)")
    p.add_argument("--index", type=Path, default=None,
                   help="merged .bkkx index path "
                        "(default: <corpus>/_corpus.bkkx, built on startup if missing)")
    p.add_argument("--host", default=None, help="bind address (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=None, help="port (default: 8000)")
    p.add_argument("--admin-token", default=None,
                   help="bearer token required for /admin/* "
                        "(default: $BKK_ADMIN_TOKEN; if unset, admin is open)")
    p.add_argument("--reload", action="store_true",
                   help="auto-reload on code changes (development only)")
    return p


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    base = ServeConfig.from_env(corpus_root=args.corpus)
    config = base.merge_cli(
        corpus_root=args.corpus,
        index_path=args.index,
        host=args.host,
        port=args.port,
        admin_token=args.admin_token,
        reload=args.reload or None,
    )

    import uvicorn

    from .app import create_app

    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port, reload=config.reload)
    return 0


def main() -> None:
    raise SystemExit(run())
