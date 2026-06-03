"""Server configuration: defaults < env vars < CLI flags."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path


@dataclass(frozen=True)
class ServeConfig:
    corpus_root: Path
    index_path: Path
    catalog_path: Path | None = None
    translation_search_path: Path | None = None
    core_root: Path | None = None
    core_index_path: Path | None = None
    annotations_root: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8000
    admin_token: str | None = None
    reload: bool = False
    upstream_repo: str | None = None
    web_dist: Path | None = None
    image_base_urls: dict[str, str] = field(default_factory=dict)
    github_client_id: str | None = None
    github_client_secret: str | None = None
    github_callback_url: str | None = None
    workspace_template_repo: str = "bunkankun/BKK-Workspace"
    workspace_repo_name: str = "BKK-Workspace"

    def __post_init__(self) -> None:
        if self.catalog_path is None:
            object.__setattr__(
                self, "catalog_path", self.corpus_root / "_catalog.bkkc"
            )
        if self.translation_search_path is None:
            object.__setattr__(
                self, "translation_search_path", self.corpus_root / "_translations.bkkt"
            )

    @classmethod
    def from_env(
        cls,
        *,
        corpus_root: Path | str | None = None,
        rc: dict | None = None,
        core_rc: dict | None = None,
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

        env_catalog = os.environ.get("BKK_CATALOG_PATH")
        rc_catalog = rc.get("catalog")
        if env_catalog:
            catalog: Path | None = Path(env_catalog).resolve()
        elif rc_catalog:
            catalog = Path(rc_catalog).resolve()
        else:
            catalog = root / "_catalog.bkkc"

        core_rc = core_rc or {}
        env_core_root = os.environ.get("BKK_CORE_ROOT")
        if env_core_root:
            core_root: Path | None = Path(env_core_root).resolve()
        elif core_rc.get("root"):
            core_root = Path(core_rc["root"]).resolve()
        else:
            core_root = None

        env_core_index = os.environ.get("BKK_CORE_INDEX_PATH")
        if env_core_index:
            core_index: Path | None = Path(env_core_index).resolve()
        elif core_rc.get("index"):
            core_index = Path(core_rc["index"]).resolve()
        elif core_root is not None:
            core_index = core_root / "_core.bkki"
        else:
            core_index = None

        env_annotations_root = os.environ.get("BKK_ANNOTATIONS_ROOT")
        rc_annotations_root = rc.get("annotations_root")
        if env_annotations_root:
            annotations_root: Path | None = Path(env_annotations_root).resolve()
        elif rc_annotations_root:
            annotations_root = Path(rc_annotations_root).resolve()
        else:
            annotations_root = None

        env_translation_search = os.environ.get("BKK_TRANSLATION_SEARCH_PATH")
        rc_translation_search = rc.get("translation_search")
        if env_translation_search:
            translation_search: Path | None = Path(env_translation_search).resolve()
        elif rc_translation_search:
            translation_search = Path(rc_translation_search).resolve()
        else:
            translation_search = root / "_translations.bkkt"

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

        env_github_client_id = os.environ.get("BKK_GITHUB_CLIENT_ID")
        github_client_id = (
            env_github_client_id
            if env_github_client_id is not None
            else rc.get("github_client_id")
        )

        env_github_client_secret = os.environ.get("BKK_GITHUB_CLIENT_SECRET")
        github_client_secret = (
            env_github_client_secret
            if env_github_client_secret is not None
            else rc.get("github_client_secret")
        )

        env_github_callback_url = os.environ.get("BKK_GITHUB_CALLBACK_URL")
        github_callback_url = (
            env_github_callback_url
            if env_github_callback_url is not None
            else rc.get("github_callback_url")
        )

        env_workspace_template = os.environ.get("BKK_WORKSPACE_TEMPLATE_REPO")
        workspace_template_repo = (
            env_workspace_template
            if env_workspace_template is not None
            else rc.get("workspace_template_repo", "bunkankun/BKK-Workspace")
        )

        env_workspace_repo_name = os.environ.get("BKK_WORKSPACE_REPO_NAME")
        workspace_repo_name = (
            env_workspace_repo_name
            if env_workspace_repo_name is not None
            else rc.get("workspace_repo_name", "BKK-Workspace")
        )

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
            catalog_path=catalog,
            translation_search_path=translation_search,
            core_root=core_root,
            core_index_path=core_index,
            annotations_root=annotations_root,
            host=host,
            port=port,
            admin_token=admin_token,
            reload=False,
            upstream_repo=upstream_repo,
            web_dist=web_dist,
            image_base_urls=dict(rc_image_base_urls),
            github_client_id=github_client_id,
            github_client_secret=github_client_secret,
            github_callback_url=github_callback_url,
            workspace_template_repo=workspace_template_repo,
            workspace_repo_name=workspace_repo_name,
        )

    def merge_cli(
        self,
        *,
        corpus_root: Path | str | None = None,
        index_path: Path | str | None = None,
        catalog_path: Path | str | None = None,
        translation_search_path: Path | str | None = None,
        core_root: Path | str | None = None,
        core_index_path: Path | str | None = None,
        host: str | None = None,
        port: int | None = None,
        admin_token: str | None = None,
        reload: bool | None = None,
        upstream_repo: str | None = None,
        web_dist: Path | str | None = None,
        github_client_id: str | None = None,
        github_client_secret: str | None = None,
        github_callback_url: str | None = None,
        workspace_template_repo: str | None = None,
        workspace_repo_name: str | None = None,
    ) -> "ServeConfig":
        """Return a copy with any non-``None`` argument overriding the field."""
        updates: dict = {}
        if corpus_root is not None:
            updates["corpus_root"] = Path(corpus_root).resolve()
        if index_path is not None:
            updates["index_path"] = Path(index_path).resolve()
        if catalog_path is not None:
            updates["catalog_path"] = Path(catalog_path).resolve()
        if translation_search_path is not None:
            updates["translation_search_path"] = Path(translation_search_path).resolve()
        if core_root is not None:
            updates["core_root"] = Path(core_root).resolve()
        if core_index_path is not None:
            updates["core_index_path"] = Path(core_index_path).resolve()
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
        if github_client_id is not None:
            updates["github_client_id"] = github_client_id
        if github_client_secret is not None:
            updates["github_client_secret"] = github_client_secret
        if github_callback_url is not None:
            updates["github_callback_url"] = github_callback_url
        if workspace_template_repo is not None:
            updates["workspace_template_repo"] = workspace_template_repo
        if workspace_repo_name is not None:
            updates["workspace_repo_name"] = workspace_repo_name
        return replace(self, **updates)
