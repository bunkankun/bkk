"""Command-line entry point for ``bkk-serve`` / ``python -m bkk.serve``.

Usage::

    bkk-serve --corpus <dir> [--index PATH] [--catalog PATH] [--host H] [--port N]
              [--admin-team ORG/SLUG] [--reload]
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
    # core_root/core_index_path are picked up from BKK_CORE_* env vars set by
    # the parent process before uvicorn forks the reload worker.
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
    p.add_argument("--core-root", type=Path, default=None, dest="core_root",
                   help="bkk-core knowledge layer root directory "
                        "(default: core.root from .bkkrc; enables /core/* endpoints)")
    p.add_argument("--core-index", type=Path, default=None, dest="core_index_path",
                   help="core .bkki index path "
                        "(default: core.index from .bkkrc, else <core-root>/_core.bkki)")
    p.add_argument("--core-upstream-repo", default=None, dest="core_upstream_repo",
                   help="GitHub upstream bkk-core repo as ORG/REPO; required "
                        "to enable inline editing of core records "
                        "(default: core.upstream_repo from .bkkrc, "
                        "else $BKK_CORE_UPSTREAM_REPO)")
    p.add_argument("--core-pr-base", default=None, dest="core_pr_base",
                   help="base branch on the upstream bkk-core repo that "
                        "edit PRs target (default: master)")
    p.add_argument("--annotations-root", type=Path, default=None,
                   help="bkk-annotations archive root "
                        "(default: annotations.annotations_root / serve.annotations_root from .bkkrc)")
    p.add_argument("--annotations-index", type=Path, default=None, dest="annotations_index_path",
                   help="annotation .bkka index path "
                        "(default: <annotations-root>/_annotations.bkka)")
    p.add_argument("--host", default=None, help="bind address (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=None, help="port (default: 8000)")
    p.add_argument("--admin-team", default=None, dest="admin_team",
                   help="GitHub team (ORG/SLUG) whose members may access /admin/* "
                        "(default: $BKK_ADMIN_TEAM, else bunkankun/bkk-admin)")
    p.add_argument("--reload", action="store_true",
                   help="auto-reload on code changes (development only)")
    p.add_argument("--upstream-repo", default=None,
                   help="GitHub upstream texts repo as ORG/REPO "
                        "(default: $BKK_UPSTREAM_REPO; surfaced on GET / for the SPA)")
    p.add_argument("--web-dist", type=Path, default=None,
                   help="directory containing the built SPA to mount at / "
                        "(default: $BKK_WEB_DIST)")
    p.add_argument("--welcome", type=Path, default=None, dest="welcome_path",
                   help="markdown file shown in the empty workspace and when "
                        "the user clicks the logo "
                        "(default: serve.welcome from .bkkrc, else $BKK_WELCOME_PATH)")
    p.add_argument("--github-client-id", default=None,
                   help="GitHub OAuth app client id (default: $BKK_GITHUB_CLIENT_ID)")
    p.add_argument("--github-client-secret", default=None,
                   help="GitHub OAuth app client secret (default: $BKK_GITHUB_CLIENT_SECRET)")
    p.add_argument("--github-callback-url", default=None,
                   help="OAuth callback URL registered with GitHub "
                        "(default: <server-origin>/api/auth/github/callback)")
    p.add_argument("--workspace-template-repo", default=None,
                   help="template repo for first-login workspaces "
                        "(default: bunkankun/BKK-Workspace)")
    p.add_argument("--workspace-repo-name", default=None,
                   help="workspace repo name created under each user "
                        "(default: BKK-Workspace)")
    p.add_argument("--bundle-github-org", default=None,
                   help="GitHub organization containing per-text bundle repositories "
                        "(default: bkkbooks)")
    p.add_argument("--bundle-github-branch", default=None,
                   help="base branch for bundle edits "
                        "(default: auto-detect each repository's default branch)")
    return p


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from bkk.config import load_rc
    rc = load_rc()
    rc_serve = {**rc.get("global", {}), **rc.get("serve", {}), **rc.get("annotations", {})}
    rc_core = rc.get("core", {})
    rc_duplications = rc.get("duplications", {})

    base = ServeConfig.from_env(
        corpus_root=args.corpus, rc=rc_serve, core_rc=rc_core,
        duplications_rc=rc_duplications,
    )
    config = base.merge_cli(
        corpus_root=args.corpus,
        index_path=args.index,
        catalog_path=args.catalog_path,
        core_root=args.core_root,
        core_index_path=args.core_index_path,
        core_upstream_repo=args.core_upstream_repo,
        core_pr_base=args.core_pr_base,
        annotations_root=args.annotations_root,
        annotations_index_path=args.annotations_index_path,
        host=args.host,
        port=args.port,
        admin_team=args.admin_team,
        reload=args.reload or None,
        upstream_repo=args.upstream_repo,
        web_dist=args.web_dist,
        welcome_path=args.welcome_path,
        github_client_id=args.github_client_id,
        github_client_secret=args.github_client_secret,
        github_callback_url=args.github_callback_url,
        workspace_template_repo=args.workspace_template_repo,
        workspace_repo_name=args.workspace_repo_name,
        bundle_github_org=args.bundle_github_org,
        bundle_github_branch=args.bundle_github_branch,
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
        if config.core_root is not None:
            os.environ["BKK_CORE_ROOT"] = str(config.core_root)
        if config.core_index_path is not None:
            os.environ["BKK_CORE_INDEX_PATH"] = str(config.core_index_path)
        if config.core_upstream_repo is not None:
            os.environ["BKK_CORE_UPSTREAM_REPO"] = config.core_upstream_repo
        os.environ["BKK_CORE_PR_BASE"] = config.core_pr_base
        if config.annotations_root is not None:
            os.environ["BKK_ANNOTATIONS_ROOT"] = str(config.annotations_root)
        if config.annotations_index_path is not None:
            os.environ["BKK_ANNOTATIONS_INDEX_PATH"] = str(config.annotations_index_path)
        os.environ["BKK_HOST"] = config.host
        os.environ["BKK_PORT"] = str(config.port)
        os.environ["BKK_ADMIN_TEAM"] = config.admin_team
        if config.upstream_repo is not None:
            os.environ["BKK_UPSTREAM_REPO"] = config.upstream_repo
        if config.web_dist is not None:
            os.environ["BKK_WEB_DIST"] = str(config.web_dist)
        if config.welcome_path is not None:
            os.environ["BKK_WELCOME_PATH"] = str(config.welcome_path)
        if config.github_client_id is not None:
            os.environ["BKK_GITHUB_CLIENT_ID"] = config.github_client_id
        if config.github_client_secret is not None:
            os.environ["BKK_GITHUB_CLIENT_SECRET"] = config.github_client_secret
        if config.github_callback_url is not None:
            os.environ["BKK_GITHUB_CALLBACK_URL"] = config.github_callback_url
        os.environ["BKK_WORKSPACE_TEMPLATE_REPO"] = config.workspace_template_repo
        os.environ["BKK_WORKSPACE_REPO_NAME"] = config.workspace_repo_name
        os.environ["BKK_BUNDLE_GITHUB_ORG"] = config.bundle_github_org
        os.environ["BKK_BUNDLE_GITHUB_BRANCH"] = config.bundle_github_branch
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
