"""Endpoints under /admin: bearer-token auth + background jobs."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bkk.serve import create_app
from bkk.serve.config import ServeConfig

from .conftest import write_bundle


@pytest.fixture
def admin_corpus(tmp_path: Path) -> Path:
    write_bundle(
        tmp_path,
        "ADM0001",
        "甲乙丙丁戊己庚辛壬癸",
        title="天干",
    )
    return tmp_path


def _client(corpus: Path, *, admin_token: str | None = None) -> TestClient:
    config = ServeConfig(
        corpus_root=corpus,
        index_path=corpus / "_corpus.bkkx",
        admin_token=admin_token,
    )
    return TestClient(create_app(config))


# ---------- auth ----------


def test_admin_open_when_no_token(admin_corpus):
    client = _client(admin_corpus)
    r = client.post("/admin/validate/ADM0001")
    assert r.status_code == 202


def test_admin_unauthorized_without_header(admin_corpus):
    client = _client(admin_corpus, admin_token="secret")
    r = client.post("/admin/validate/ADM0001")
    assert r.status_code == 401
    assert r.json()["error"] == "admin_unauthorized"
    assert r.headers.get("www-authenticate") == "Bearer"


def test_admin_unauthorized_wrong_token(admin_corpus):
    client = _client(admin_corpus, admin_token="secret")
    r = client.post(
        "/admin/validate/ADM0001",
        headers={"Authorization": "Bearer nope"},
    )
    assert r.status_code == 401


def test_admin_authorized_with_correct_token(admin_corpus):
    client = _client(admin_corpus, admin_token="secret")
    r = client.post(
        "/admin/validate/ADM0001",
        headers={"Authorization": "Bearer secret"},
    )
    assert r.status_code == 202


# ---------- jobs lifecycle ----------


def test_validate_produces_success_job(admin_corpus):
    client = _client(admin_corpus)
    r = client.post("/admin/validate/ADM0001")
    assert r.status_code == 202
    job_id = r.json()["id"]
    # TestClient runs BackgroundTasks before returning, so the job is done.
    poll = client.get(f"/admin/jobs/{job_id}")
    assert poll.status_code == 200
    body = poll.json()
    assert body["status"] == "success"
    assert body["kind"] == "validate"
    assert body["target"] == "ADM0001"
    assert body["started_at"] is not None
    assert body["finished_at"] is not None
    # Validator emits a JSON report; render_json was loaded back to a dict.
    assert isinstance(body["result"], dict)


def test_index_one_produces_artifact(admin_corpus):
    client = _client(admin_corpus)
    r = client.post("/admin/index/ADM0001")
    assert r.status_code == 202
    job_id = r.json()["id"]
    body = client.get(f"/admin/jobs/{job_id}").json()
    assert body["status"] == "success"
    assert body["kind"] == "index"
    assert Path(body["result"]["index_path"]).exists()


def test_merge_corpus_produces_artifact(admin_corpus):
    client = _client(admin_corpus)
    r = client.post("/admin/index")
    assert r.status_code == 202
    job_id = r.json()["id"]
    body = client.get(f"/admin/jobs/{job_id}").json()
    assert body["status"] == "success"
    assert body["kind"] == "merge"
    assert Path(body["result"]["index_path"]).exists()


def test_admin_index_unknown_textid(admin_corpus):
    client = _client(admin_corpus)
    r = client.post("/admin/index/MISSING")
    assert r.status_code == 404
    assert r.json()["error"] == "bundle_not_found"


def test_admin_validate_unknown_textid(admin_corpus):
    client = _client(admin_corpus)
    r = client.post("/admin/validate/MISSING")
    assert r.status_code == 404
    assert r.json()["error"] == "bundle_not_found"


def test_jobs_unknown_id(admin_corpus):
    client = _client(admin_corpus)
    r = client.get("/admin/jobs/no-such-job")
    assert r.status_code == 400
    assert r.json()["error"] == "job_not_found"


def test_jobs_endpoint_also_auth_protected(admin_corpus):
    client = _client(admin_corpus, admin_token="secret")
    r = client.get("/admin/jobs/anything")
    assert r.status_code == 401
