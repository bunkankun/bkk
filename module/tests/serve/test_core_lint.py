"""GET /api/core/lint/syntactic-functions."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from bkk.index.core import build_core_index
from bkk.serve import create_app
from bkk.serve.config import ServeConfig


def _write_syn(root: Path, uuid: str, code: str, *, lint_accept: list[str] | None = None) -> None:
    record: dict[str, object] = {
        "schema_version": 2,
        "uuid": uuid,
        "type": "syntactic-function",
        "labels": {"display": code, "alternate": []},
        "code": code,
    }
    if lint_accept is not None:
        record["lint_accept"] = lint_accept
    path = root / "syntactic-functions" / uuid[0] / f"{uuid}.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")


@pytest.fixture
def lint_client(tmp_path: Path) -> TestClient:
    core_root = tmp_path / "bkk-core"
    _write_syn(core_root, "00000000-0000-0000-0000-000000000001", "NPab")  # clean
    _write_syn(core_root, "11111111-1111-1111-1111-111111111111", "vadN{{PRED}")  # error
    _write_syn(core_root, "22222222-2222-2222-2222-222222222222", "vt+prep N")  # warning
    _write_syn(
        core_root,
        "33333333-3333-3333-3333-333333333333",
        "vt+prep N",
        lint_accept=["whitespace"],
    )

    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    build_core_index(core_root, core_root / "_core.bkki")
    config = ServeConfig(
        corpus_root=corpus_root,
        index_path=corpus_root / "_corpus.bkkx",
        core_root=core_root,
        core_index_path=core_root / "_core.bkki",
    )
    return TestClient(create_app(config))


def test_lint_endpoint_returns_sorted_diagnostics(lint_client: TestClient) -> None:
    resp = lint_client.get("/core/lint/syntactic-functions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["record_count"] == 4
    assert body["error_count"] >= 1
    # All errors come first.
    severities = [it["diagnostic"]["severity"] for it in body["items"]]
    error_count = body["error_count"]
    assert severities[:error_count] == ["error"] * error_count
    # UUIDs match the file stems.
    for it in body["items"]:
        assert len(it["uuid"]) == 36
        assert it["path"].startswith("syntactic-functions/")
        assert it["collection"] == "syntactic-functions"


def test_lint_endpoint_honours_lint_accept(lint_client: TestClient) -> None:
    body = lint_client.get("/core/lint/syntactic-functions").json()
    by_uuid: dict[str, list[str]] = {}
    for it in body["items"]:
        by_uuid.setdefault(it["uuid"], []).append(it["diagnostic"]["code"])
    # Plain warning record still surfaces 'whitespace'.
    assert "whitespace" in by_uuid["22222222-2222-2222-2222-222222222222"]
    # The record with lint_accept: [whitespace] is silenced.
    assert "whitespace" not in by_uuid.get("33333333-3333-3333-3333-333333333333", [])


def test_lint_endpoint_503_without_core_root(tmp_path: Path) -> None:
    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    config = ServeConfig(
        corpus_root=corpus_root,
        index_path=corpus_root / "_corpus.bkkx",
    )
    client = TestClient(create_app(config))
    resp = client.get("/core/lint/syntactic-functions")
    assert resp.status_code == 503
