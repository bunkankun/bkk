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


def test_root_echoes_upstream_repo_when_set(corpus: Path):
    client = _make_client(corpus, upstream_repo="my-org/my-repo")
    body = client.get("/").json()
    assert body["upstream_repo"] == "my-org/my-repo"


def test_from_env_reads_upstream_repo(corpus: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BKK_UPSTREAM_REPO", "env-org/env-repo")
    monkeypatch.delenv("BKK_INDEX_PATH", raising=False)
    monkeypatch.delenv("BKK_WEB_DIST", raising=False)

    config = ServeConfig.from_env(corpus_root=corpus)
    assert config.upstream_repo == "env-org/env-repo"


def test_from_env_unset_yields_none(corpus: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("BKK_UPSTREAM_REPO", raising=False)
    monkeypatch.delenv("BKK_INDEX_PATH", raising=False)
    monkeypatch.delenv("BKK_WEB_DIST", raising=False)

    config = ServeConfig.from_env(corpus_root=corpus)
    assert config.upstream_repo is None


def test_cli_flag_overrides_env(corpus: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BKK_UPSTREAM_REPO", "env-org/env-repo")
    monkeypatch.delenv("BKK_INDEX_PATH", raising=False)
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


def test_web_dist_env_and_cli(corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    web_a = tmp_path / "envdist"
    web_a.mkdir()
    web_b = tmp_path / "clidist"
    web_b.mkdir()

    monkeypatch.setenv("BKK_WEB_DIST", str(web_a))
    monkeypatch.delenv("BKK_INDEX_PATH", raising=False)
    monkeypatch.delenv("BKK_UPSTREAM_REPO", raising=False)

    base = ServeConfig.from_env(corpus_root=corpus)
    assert base.web_dist == web_a.resolve()

    config = base.merge_cli(web_dist=web_b)
    assert config.web_dist == web_b.resolve()
