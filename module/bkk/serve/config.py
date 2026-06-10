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
    core_upstream_repo: str | None = None
    core_pr_base: str = "master"
    annotations_root: Path | None = None
    annotations_index_path: Path | None = None
    annotation_dids: tuple[str, ...] = ()
    annotation_admin_dids: tuple[str, ...] = ()
    host: str = "127.0.0.1"
    port: int = 8000
    admin_team: str = "bunkankun/bkk-admin"
    editor_team: str = "bunkankun/bkk-editor"
    reload: bool = False
    upstream_repo: str | None = None
    web_dist: Path | None = None
    welcome_path: Path | None = None
    image_base_urls: dict[str, str] = field(default_factory=dict)
    github_client_id: str | None = None
    github_client_secret: str | None = None
    github_callback_url: str | None = None
    workspace_template_repo: str = "bunkankun/BKK-Workspace"
    workspace_repo_name: str = "BKK-Workspace"
    source_root: Path | None = None
    source_branch: str = "master"
    max_search_hits: int = 20000

    def __post_init__(self) -> None:
        if self.catalog_path is None:
            object.__setattr__(
                self, "catalog_path", self.corpus_root / "_catalog.bkkc"
            )
        if self.translation_search_path is None:
            object.__setattr__(
                self, "translation_search_path", self.corpus_root / "_translations.bkkt"
            )
        if self.annotations_index_path is None and self.annotations_root is not None:
            object.__setattr__(
                self, "annotations_index_path", self.annotations_root / "_annotations.bkka"
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

        env_core_upstream = os.environ.get("BKK_CORE_UPSTREAM_REPO")
        core_upstream_repo = (
            env_core_upstream
            if env_core_upstream is not None
            else core_rc.get("upstream_repo")
        )

        env_core_pr_base = os.environ.get("BKK_CORE_PR_BASE")
        core_pr_base = (
            env_core_pr_base
            if env_core_pr_base is not None
            else core_rc.get("pr_base", "master")
        )

        env_annotations_root = os.environ.get("BKK_ANNOTATIONS_ROOT")
        rc_annotations_root = rc.get("annotations_root")
        if env_annotations_root:
            annotations_root: Path | None = Path(env_annotations_root).resolve()
        elif rc_annotations_root:
            annotations_root = Path(rc_annotations_root).resolve()
        else:
            annotations_root = None

        env_annotations_index = os.environ.get("BKK_ANNOTATIONS_INDEX_PATH")
        rc_annotations_index = rc.get("annotations_index")
        if env_annotations_index:
            annotations_index: Path | None = Path(env_annotations_index).resolve()
        elif rc_annotations_index:
            annotations_index = Path(rc_annotations_index).resolve()
        elif annotations_root is not None:
            annotations_index = annotations_root / "_annotations.bkka"
        else:
            annotations_index = None

        rc_dids = rc.get("dids") or ()
        if isinstance(rc_dids, str):
            raise ValueError(
                "[annotations].dids must be a YAML list of strings, got a scalar"
            )
        annotation_dids = tuple(d for d in rc_dids if isinstance(d, str))

        rc_admin_dids = rc.get("admin_dids") or ()
        if isinstance(rc_admin_dids, str):
            raise ValueError(
                "[annotations].admin_dids must be a YAML list of strings, got a scalar"
            )
        annotation_admin_dids = tuple(
            d for d in rc_admin_dids if isinstance(d, str)
        )

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

        env_welcome = os.environ.get("BKK_WELCOME_PATH")
        rc_welcome = rc.get("welcome")
        if env_welcome:
            welcome_path: Path | None = Path(env_welcome).resolve()
        elif rc_welcome:
            welcome_path = Path(rc_welcome).resolve()
        else:
            welcome_path = None

        env_host = os.environ.get("BKK_HOST")
        host = env_host if env_host is not None else rc.get("host", "127.0.0.1")

        env_port = os.environ.get("BKK_PORT")
        port = int(env_port) if env_port is not None else int(rc.get("port", 8000))

        env_admin_team = os.environ.get("BKK_ADMIN_TEAM")
        admin_team = (
            env_admin_team
            if env_admin_team is not None
            else rc.get("admin_team", "bunkankun/bkk-admin")
        )

        env_editor_team = os.environ.get("BKK_EDITOR_TEAM")
        editor_team = (
            env_editor_team
            if env_editor_team is not None
            else rc.get("editor_team", "bunkankun/bkk-editor")
        )

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

        env_source_root = os.environ.get("BKK_SOURCE_ROOT")
        rc_source_root = rc.get("source_root")
        if env_source_root:
            source_root: Path | None = Path(env_source_root).resolve()
        elif rc_source_root:
            source_root = Path(rc_source_root).resolve()
        else:
            # Auto-detect: <bkk-package>/__init__.py → parents[2] is repo root.
            try:
                import bkk as _bkk_pkg
                candidate = Path(_bkk_pkg.__file__).resolve().parents[2]
                source_root = candidate if (candidate / ".git").exists() else None
            except Exception:
                source_root = None

        env_source_branch = os.environ.get("BKK_SOURCE_BRANCH")
        source_branch = (
            env_source_branch
            if env_source_branch is not None
            else rc.get("source_branch", "master")
        )

        env_max_search_hits = os.environ.get("BKK_MAX_SEARCH_HITS")
        if env_max_search_hits is not None:
            max_search_hits = int(env_max_search_hits)
        else:
            max_search_hits = int(rc.get("max_search_hits", 20000))

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
            core_upstream_repo=core_upstream_repo,
            core_pr_base=core_pr_base,
            annotations_root=annotations_root,
            annotations_index_path=annotations_index,
            annotation_dids=annotation_dids,
            annotation_admin_dids=annotation_admin_dids,
            host=host,
            port=port,
            admin_team=admin_team,
            editor_team=editor_team,
            reload=False,
            upstream_repo=upstream_repo,
            web_dist=web_dist,
            welcome_path=welcome_path,
            image_base_urls=dict(rc_image_base_urls),
            github_client_id=github_client_id,
            github_client_secret=github_client_secret,
            github_callback_url=github_callback_url,
            workspace_template_repo=workspace_template_repo,
            workspace_repo_name=workspace_repo_name,
            source_root=source_root,
            source_branch=source_branch,
            max_search_hits=max_search_hits,
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
        core_upstream_repo: str | None = None,
        core_pr_base: str | None = None,
        annotations_root: Path | str | None = None,
        annotations_index_path: Path | str | None = None,
        host: str | None = None,
        port: int | None = None,
        admin_team: str | None = None,
        editor_team: str | None = None,
        reload: bool | None = None,
        upstream_repo: str | None = None,
        web_dist: Path | str | None = None,
        welcome_path: Path | str | None = None,
        github_client_id: str | None = None,
        github_client_secret: str | None = None,
        github_callback_url: str | None = None,
        workspace_template_repo: str | None = None,
        workspace_repo_name: str | None = None,
        source_root: Path | str | None = None,
        source_branch: str | None = None,
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
        if core_upstream_repo is not None:
            updates["core_upstream_repo"] = core_upstream_repo
        if core_pr_base is not None:
            updates["core_pr_base"] = core_pr_base
        if annotations_root is not None:
            updates["annotations_root"] = Path(annotations_root).resolve()
        if annotations_index_path is not None:
            updates["annotations_index_path"] = Path(annotations_index_path).resolve()
        if host is not None:
            updates["host"] = host
        if port is not None:
            updates["port"] = port
        if admin_team is not None:
            updates["admin_team"] = admin_team
        if editor_team is not None:
            updates["editor_team"] = editor_team
        if reload is not None:
            updates["reload"] = reload
        if upstream_repo is not None:
            updates["upstream_repo"] = upstream_repo
        if web_dist is not None:
            updates["web_dist"] = Path(web_dist).resolve()
        if welcome_path is not None:
            updates["welcome_path"] = Path(welcome_path).resolve()
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
        if source_root is not None:
            updates["source_root"] = Path(source_root).resolve()
        if source_branch is not None:
            updates["source_branch"] = source_branch
        return replace(self, **updates)
