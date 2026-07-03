"""ServeConfig: BKK_UPSTREAM_REPO env + --upstream-repo CLI both populate the field
and the value is echoed by GET /."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bkk.serve import create_app
from bkk.serve.cli import build_parser
from bkk.serve.config import ServeConfig


def _make_client(corpus: Path, **overrides) -> TestClient:
    config = ServeConfig(
        corpus_root=corpus,
        index_path=corpus / "_corpus.bkkx",
        **overrides,
    )
    return TestClient(create_app(config))


def test_root_reports_no_upstream_repo_by_default(client: TestClient):
    body = client.get("/").json()
    assert body["service"] == "bkk-serve"
    assert body["upstream_repo"] is None
    assert body["bluesky_enabled"] is False
    assert body["catalog_path"].endswith("_catalog.bkkc")


def test_root_echoes_upstream_repo_when_set(corpus: Path):
    client = _make_client(corpus, upstream_repo="my-org/my-repo")
    body = client.get("/").json()
    assert body["upstream_repo"] == "my-org/my-repo"


def test_from_env_reads_upstream_repo(corpus: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BKK_UPSTREAM_REPO", "env-org/env-repo")
    monkeypatch.delenv("BKK_INDEX_PATH", raising=False)
    monkeypatch.delenv("BKK_CATALOG_PATH", raising=False)
    monkeypatch.delenv("BKK_WEB_DIST", raising=False)

    config = ServeConfig.from_env(corpus_root=corpus)
    assert config.upstream_repo == "env-org/env-repo"


def test_from_env_unset_yields_none(corpus: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BKK_UPSTREAM_REPO", raising=False)
    monkeypatch.delenv("BKK_BLUESKY_ENABLE", raising=False)
    monkeypatch.delenv("BKK_INDEX_PATH", raising=False)
    monkeypatch.delenv("BKK_CATALOG_PATH", raising=False)
    monkeypatch.delenv("BKK_WEB_DIST", raising=False)

    config = ServeConfig.from_env(corpus_root=corpus)
    assert config.upstream_repo is None
    assert config.bluesky_enabled is False


def test_from_env_reads_parallels_root(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    rc_root = tmp_path / "rc-parallels"
    env_root = tmp_path / "env-parallels"
    monkeypatch.delenv("BKK_PARALLELS_ROOT", raising=False)

    config = ServeConfig.from_env(
        corpus_root=corpus, rc={"parallels_root": rc_root},
    )
    assert config.parallels_root == rc_root.resolve()

    monkeypatch.setenv("BKK_PARALLELS_ROOT", str(env_root))
    config = ServeConfig.from_env(
        corpus_root=corpus, rc={"parallels_root": rc_root},
    )
    assert config.parallels_root == env_root.resolve()


def test_from_env_enables_bluesky_only_on_exact_true(
    corpus: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("BKK_BLUESKY_ENABLE", "true")
    monkeypatch.delenv("BKK_INDEX_PATH", raising=False)
    monkeypatch.delenv("BKK_CATALOG_PATH", raising=False)
    monkeypatch.delenv("BKK_WEB_DIST", raising=False)
    monkeypatch.delenv("BKK_UPSTREAM_REPO", raising=False)

    config = ServeConfig.from_env(corpus_root=corpus)
    assert config.bluesky_enabled is False

    monkeypatch.setenv("BKK_BLUESKY_ENABLE", "True")
    config = ServeConfig.from_env(corpus_root=corpus)
    assert config.bluesky_enabled is True


def test_cli_flag_overrides_env(corpus: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BKK_UPSTREAM_REPO", "env-org/env-repo")
    monkeypatch.delenv("BKK_INDEX_PATH", raising=False)
    monkeypatch.delenv("BKK_CATALOG_PATH", raising=False)
    monkeypatch.delenv("BKK_WEB_DIST", raising=False)

    args = build_parser().parse_args(["--upstream-repo", "cli-org/cli-repo"])
    base = ServeConfig.from_env(corpus_root=corpus)
    config = base.merge_cli(upstream_repo=args.upstream_repo)

    assert config.upstream_repo == "cli-org/cli-repo"


def test_cli_flag_alone_sets_value(corpus: Path):
    base = ServeConfig(
        corpus_root=corpus,
        index_path=corpus / "_corpus.bkkx",
    )
    config = base.merge_cli(upstream_repo="flag-only/repo")
    assert config.upstream_repo == "flag-only/repo"

    client = TestClient(create_app(config))
    assert client.get("/").json()["upstream_repo"] == "flag-only/repo"


def test_bundle_github_defaults_env_and_cli(corpus: Path, monkeypatch: pytest.MonkeyPatch):
    defaults = ServeConfig(corpus_root=corpus, index_path=corpus / "_corpus.bkkx")
    assert defaults.bundle_github_org == "bkkbooks"
    assert defaults.bundle_github_branch == "auto"

    monkeypatch.setenv("BKK_BUNDLE_GITHUB_ORG", "env-books")
    monkeypatch.setenv("BKK_BUNDLE_GITHUB_BRANCH", "main")
    from_env = ServeConfig.from_env(corpus_root=corpus)
    assert from_env.bundle_github_org == "env-books"
    assert from_env.bundle_github_branch == "main"

    args = build_parser().parse_args([
        "--bundle-github-org", "cli-books",
        "--bundle-github-branch", "stable",
    ])
    merged = from_env.merge_cli(
        bundle_github_org=args.bundle_github_org,
        bundle_github_branch=args.bundle_github_branch,
    )
    assert merged.bundle_github_org == "cli-books"
    assert merged.bundle_github_branch == "stable"


def test_bluesky_session_endpoint_disabled_by_default(client: TestClient):
    r = client.get("/annotations/bluesky/session")
    assert r.status_code == 403
    assert "BKK_BLUESKY_ENABLE=True" in r.json()["detail"]


def test_bluesky_session_endpoint_enabled_when_configured(corpus: Path):
    client = _make_client(corpus, bluesky_enabled=True)
    r = client.get("/annotations/bluesky/session")
    assert r.status_code == 401


def test_web_dist_env_and_cli(corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    web_a = tmp_path / "envdist"
    web_a.mkdir()
    web_b = tmp_path / "clidist"
    web_b.mkdir()

    monkeypatch.setenv("BKK_WEB_DIST", str(web_a))
    monkeypatch.delenv("BKK_INDEX_PATH", raising=False)
    monkeypatch.delenv("BKK_CATALOG_PATH", raising=False)
    monkeypatch.delenv("BKK_UPSTREAM_REPO", raising=False)

    base = ServeConfig.from_env(corpus_root=corpus)
    assert base.web_dist == web_a.resolve()

    config = base.merge_cli(web_dist=web_b)
    assert config.web_dist == web_b.resolve()


def test_catalog_path_env_and_cli(corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    env_catalog = tmp_path / "env.bkkc"
    cli_catalog = tmp_path / "cli.bkkc"

    monkeypatch.setenv("BKK_CATALOG_PATH", str(env_catalog))
    monkeypatch.delenv("BKK_INDEX_PATH", raising=False)
    monkeypatch.delenv("BKK_WEB_DIST", raising=False)
    monkeypatch.delenv("BKK_UPSTREAM_REPO", raising=False)

    base = ServeConfig.from_env(corpus_root=corpus)
    assert base.catalog_path == env_catalog.resolve()

    args = build_parser().parse_args(["--catalog", str(cli_catalog)])
    config = base.merge_cli(catalog_path=args.catalog_path)
    assert config.catalog_path == cli_catalog.resolve()
