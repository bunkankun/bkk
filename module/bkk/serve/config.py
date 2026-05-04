"""Server configuration: defaults < env vars < CLI flags."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path


@dataclass(frozen=True)
class ServeConfig:
    corpus_root: Path
    index_path: Path
    host: str = "127.0.0.1"
    port: int = 8000
    admin_token: str | None = None
    reload: bool = False
    upstream_repo: str | None = None
    web_dist: Path | None = None

    @classmethod
    def from_env(cls, *, corpus_root: Path | str | None = None) -> "ServeConfig":
        env_corpus = os.environ.get("BKK_CORPUS_ROOT")
        root_str = corpus_root or env_corpus
        if root_str is None:
            raise ValueError(
                "corpus_root is required: pass --corpus or set BKK_CORPUS_ROOT"
            )
        root = Path(root_str).resolve()

        env_index = os.environ.get("BKK_INDEX_PATH")
        index = Path(env_index).resolve() if env_index else root / "_corpus.bkkx"

        env_web_dist = os.environ.get("BKK_WEB_DIST")
        web_dist = Path(env_web_dist).resolve() if env_web_dist else None

        return cls(
            corpus_root=root,
            index_path=index,
            host=os.environ.get("BKK_HOST", "127.0.0.1"),
            port=int(os.environ.get("BKK_PORT", "8000")),
            admin_token=os.environ.get("BKK_ADMIN_TOKEN"),
            reload=False,
            upstream_repo=os.environ.get("BKK_UPSTREAM_REPO"),
            web_dist=web_dist,
        )

    def merge_cli(
        self,
        *,
        corpus_root: Path | str | None = None,
        index_path: Path | str | None = None,
        host: str | None = None,
        port: int | None = None,
        admin_token: str | None = None,
        reload: bool | None = None,
        upstream_repo: str | None = None,
        web_dist: Path | str | None = None,
    ) -> "ServeConfig":
        """Return a copy with any non-``None`` argument overriding the field."""
        updates: dict = {}
        if corpus_root is not None:
            updates["corpus_root"] = Path(corpus_root).resolve()
        if index_path is not None:
            updates["index_path"] = Path(index_path).resolve()
        if host is not None:
            updates["host"] = host
        if port is not None:
            updates["port"] = port
        if admin_token is not None:
            updates["admin_token"] = admin_token
        if reload is not None:
            updates["reload"] = reload
        if upstream_repo is not None:
            updates["upstream_repo"] = upstream_repo
        if web_dist is not None:
            updates["web_dist"] = Path(web_dist).resolve()
        return replace(self, **updates)
