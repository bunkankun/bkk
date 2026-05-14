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
    image_base_urls: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(
        cls,
        *,
        corpus_root: Path | str | None = None,
        rc: dict | None = None,
    ) -> "ServeConfig":
        """Build config from defaults < rc file < env vars.

        ``rc`` is the merged global+serve section dict from ``bkk.config.load_rc()``.
        ``corpus_root`` is the CLI-supplied value (may be None); it is passed here only
        so that the required-field check can surface a useful error early.
        """
        rc = rc or {}

        env_corpus = os.environ.get("BKK_CORPUS_ROOT")
        root_str = corpus_root or env_corpus or rc.get("corpus")
        if root_str is None:
            raise ValueError(
                "corpus_root is required: pass --corpus, set BKK_CORPUS_ROOT, "
                "or add 'corpus' under [global] or [serve] in .bkkrc"
            )
        root = Path(root_str).resolve()

        env_index = os.environ.get("BKK_INDEX_PATH")
        rc_index = rc.get("index")
        if env_index:
            index = Path(env_index).resolve()
        elif rc_index:
            index = Path(rc_index).resolve()
        else:
            index = root / "_corpus.bkkx"

        env_web_dist = os.environ.get("BKK_WEB_DIST")
        rc_web_dist = rc.get("web_dist")
        if env_web_dist:
            web_dist: Path | None = Path(env_web_dist).resolve()
        elif rc_web_dist:
            web_dist = Path(rc_web_dist).resolve()
        else:
            web_dist = None

        env_host = os.environ.get("BKK_HOST")
        host = env_host if env_host is not None else rc.get("host", "127.0.0.1")

        env_port = os.environ.get("BKK_PORT")
        port = int(env_port) if env_port is not None else int(rc.get("port", 8000))

        env_token = os.environ.get("BKK_ADMIN_TOKEN")
        admin_token = env_token if env_token is not None else rc.get("admin_token")

        env_repo = os.environ.get("BKK_UPSTREAM_REPO")
        upstream_repo = env_repo if env_repo is not None else rc.get("upstream_repo")

        rc_image_base_urls = rc.get("image_base_urls") or {}
        if not isinstance(rc_image_base_urls, dict) or not all(
            isinstance(k, str) and isinstance(v, str)
            for k, v in rc_image_base_urls.items()
        ):
            raise ValueError(
                "image_base_urls must be a mapping of edition-short → URL string "
                "(both keys and values must be strings)"
            )

        return cls(
            corpus_root=root,
            index_path=index,
            host=host,
            port=port,
            admin_token=admin_token,
            reload=False,
            upstream_repo=upstream_repo,
            web_dist=web_dist,
            image_base_urls=dict(rc_image_base_urls),
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
