"""Endpoints under /admin: session + GitHub-team gating + background jobs."""

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


def _workspace(login: str = "alice") -> dict:
    return {
        "repo": f"{login}/BKK-Workspace",
        "html_url": f"https://github.com/{login}/BKK-Workspace",
        "branch": login,
        "private": True,
    }


def _client(
    corpus: Path,
    *,
    is_admin: bool = True,
    login_as: str | None = "alice",
    annotations_root: Path | None = None,
    core_root: Path | None = None,
    core_index_path: Path | None = None,
) -> TestClient:
    config = ServeConfig(
        corpus_root=corpus,
        index_path=corpus / "_corpus.bkkx",
        annotations_root=annotations_root,
        core_root=core_root,
        core_index_path=core_index_path,
    )
    client = TestClient(create_app(config))
    if login_as is not None:
        state = client.app.state.bkk
        session = state.sessions.create(
            login=login_as,
            name=login_as.capitalize(),
            avatar_url=None,
            html_url=f"https://github.com/{login_as}",
            access_token="test-token",
            workspace=_workspace(login_as),
            is_admin=is_admin,
        )
        client.cookies.set("bkk_session", session.id)
    return client


# ---------- auth ----------


def test_admin_requires_session(admin_corpus):
    client = _client(admin_corpus, login_as=None)
    r = client.post("/admin/validate/ADM0001")
    assert r.status_code == 401
    assert r.json()["detail"] == "Login required"


def test_admin_forbids_non_admin_session(admin_corpus):
    client = _client(admin_corpus, is_admin=False)
    r = client.post("/admin/validate/ADM0001")
    assert r.status_code == 403
    assert r.json()["detail"] == "Admin team membership required"


def test_admin_allows_admin_session(admin_corpus):
    client = _client(admin_corpus)
    r = client.post("/admin/validate/ADM0001")
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


def test_annotation_index_produces_artifact(admin_corpus, tmp_path: Path):
    archive = tmp_path / "bkk-annotations"
    text_dir = archive / "ADM0001"
    text_dir.mkdir(parents=True)
    (text_dir / "ADM0001_001.ann.jsonl").write_text(
        '{"payload":{"sense":{"id":"sense-1"}},"bucket":"body","bucket_offset":1}\n',
        encoding="utf-8",
    )
    client = _client(admin_corpus, annotations_root=archive)
    r = client.post("/admin/annotations")
    assert r.status_code == 202
    job_id = r.json()["id"]
    body = client.get(f"/admin/jobs/{job_id}").json()
    assert body["status"] == "success"
    assert body["kind"] == "annotation_index"
    assert Path(body["result"]["annotations_index_path"]).exists()


def test_annotation_index_requires_root(admin_corpus):
    client = _client(admin_corpus)
    r = client.post("/admin/annotations")
    assert r.status_code == 400
    assert r.json()["error"] == "annotations_root_missing"


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


def test_core_sync_requires_core_root(admin_corpus):
    client = _client(admin_corpus)
    r = client.post("/admin/core/sync")
    assert r.status_code == 400
    assert r.json()["error"] == "core_root_missing"


def test_core_sync_runs_git_and_rebuilds_index(admin_corpus, tmp_path: Path, monkeypatch):
    import subprocess

    core_root = tmp_path / "bkk-core"
    (core_root / "concepts" / "0").mkdir(parents=True)
    (core_root / "concepts" / "0" / "abc.md").write_text(
        "---\nuuid: 00000000-0000-0000-0000-00000000abcd\ntype: concept\nconcept: X\n---\nbody\n",
        encoding="utf-8",
    )

    client = _client(
        admin_corpus,
        core_root=core_root,
        core_index_path=core_root / "_core.bkki",
    )

    runs: list[list[str]] = []

    class FakeCompleted:
        def __init__(self, args, returncode=0, stdout="", stderr=""):
            self.args = args
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(args, **kwargs):
        runs.append(args)
        if args[3] == "rev-parse":
            return FakeCompleted(args, stdout="deadbeef\n")
        return FakeCompleted(args)

    monkeypatch.setattr(subprocess, "run", fake_run)

    r = client.post("/admin/core/sync")
    assert r.status_code == 202
    job_id = r.json()["id"]
    body = client.get(f"/admin/jobs/{job_id}").json()
    assert body["status"] == "success", body
    assert body["kind"] == "core_sync"
    assert body["result"]["pulled_sha"] == "deadbeef"
    assert Path(body["result"]["core_index_path"]).exists()
    assert [r[3] for r in runs] == ["fetch", "merge", "rev-parse"]


def test_core_sync_reports_non_ff_merge_failure(admin_corpus, tmp_path: Path, monkeypatch):
    import subprocess

    core_root = tmp_path / "bkk-core"
    core_root.mkdir()

    client = _client(
        admin_corpus,
        core_root=core_root,
        core_index_path=core_root / "_core.bkki",
    )

    class FakeCompleted:
        def __init__(self, args, returncode=0, stdout="", stderr=""):
            self.args = args
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(args, **kwargs):
        if args[3] == "merge":
            return FakeCompleted(args, returncode=1, stderr="not fast-forward")
        return FakeCompleted(args)

    monkeypatch.setattr(subprocess, "run", fake_run)

    r = client.post("/admin/core/sync")
    job_id = r.json()["id"]
    body = client.get(f"/admin/jobs/{job_id}").json()
    assert body["status"] == "error"
    assert "not fast-forward" in body["error"]


def test_jobs_unknown_id(admin_corpus):
    client = _client(admin_corpus)
    r = client.get("/admin/jobs/no-such-job")
    assert r.status_code == 400
    assert r.json()["error"] == "job_not_found"


def test_jobs_endpoint_requires_session(admin_corpus):
    client = _client(admin_corpus, login_as=None)
    r = client.get("/admin/jobs/anything")
    assert r.status_code == 401


# ---------- /admin/info ----------


def test_admin_info_returns_health_snapshot(admin_corpus):
    client = _client(admin_corpus)
    r = client.get("/admin/info")
    assert r.status_code == 200
    body = r.json()
    assert body["server_version"] == "0.1.0"
    assert body["corpus"]["bundle_count"] == 1
    assert body["corpus"]["exists"] is True
    assert "index" in body
    assert "catalog" in body
    assert "config" in body


def test_admin_info_requires_admin(admin_corpus):
    anon = _client(admin_corpus, login_as=None)
    assert anon.get("/admin/info").status_code == 401

    non_admin = _client(admin_corpus, is_admin=False)
    assert non_admin.get("/admin/info").status_code == 403
