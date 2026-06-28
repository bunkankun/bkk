"""Endpoints under /admin/duplications: list, detail, action."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from bkk.index import merge_bundles
from bkk.index.cli import run as cli_run
from bkk.index.duplications import read_duplications_report
from bkk.serve import create_app
from bkk.serve.config import ServeConfig


def _write_bundle(root: Path, textid: str, body_text: str) -> Path:
    bundle_dir = root / textid
    bundle_dir.mkdir(parents=True)
    juan = {
        "canonical_identifier": f"bkk:test/{textid}/v1/juan/1",
        "seq": 1,
        "body": {"text": body_text, "hash": "sha256:0", "markers": []},
        "hash": "sha256:0",
    }
    (bundle_dir / f"{textid}_001.yaml").write_text(
        yaml.safe_dump(juan, allow_unicode=True), encoding="utf-8",
    )
    (bundle_dir / f"{textid}.manifest.yaml").write_text(
        yaml.safe_dump({
            "canonical_identifier": f"bkk:test/{textid}/v1",
            "editions": [{"short": "X", "label": "x"}],
            "assets": {
                "parts": [
                    {"seq": 1, "filename": f"{textid}_001.yaml", "hash": "sha256:0"},
                ],
            },
            "table_of_contents": [
                {
                    "ref": {
                        "seq": 1,
                        "marker_id": f"{textid}_001-body",
                        "span": ["body", 0, len(body_text)],
                    },
                    "label": f"{textid} body",
                },
            ],
            "metadata": {"title": textid, "edition": {"short": "X"}},
        }, allow_unicode=True),
        encoding="utf-8",
    )
    return bundle_dir


def _long_block(seed: str, length: int) -> str:
    chars: list[str] = []
    i = 0
    while len(chars) < length:
        chars.append(seed)
        chars.append(f"{i:04d}-")
        i += 1
    return "".join(chars)[:length]


@pytest.fixture
def dup_corpus(tmp_path: Path) -> tuple[Path, Path]:
    """Two bundles whose juan share a 400-char block; return (corpus, report)."""
    shared = _long_block("D", 400)
    _write_bundle(tmp_path, "KR0a0001", f"aaa{shared}bbb")
    _write_bundle(tmp_path, "KR0a0002", f"ccc{shared}ddd")
    merge_out = tmp_path / "_corpus.bkkx"
    merge_bundles(tmp_path, merge_out)
    report = tmp_path / "dups.tsv"
    rc = cli_run([
        "duplications", str(merge_out),
        "--out", str(report),
        "--min-length", "200", "--min-pair-chars", "100", "--quiet",
    ])
    assert rc == 0
    return tmp_path, report


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
    report: Path | None,
    is_admin: bool = True,
    login_as: str | None = "alice",
) -> TestClient:
    config = ServeConfig(
        corpus_root=corpus,
        index_path=corpus / "_corpus.bkkx",
        duplications_report_path=report,
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


def test_requires_session(dup_corpus):
    corpus, report = dup_corpus
    client = _client(corpus, report=report, login_as=None)
    assert client.get("/admin/duplications").status_code == 401


def test_forbids_non_admin(dup_corpus):
    corpus, report = dup_corpus
    client = _client(corpus, report=report, is_admin=False)
    assert client.get("/admin/duplications").status_code == 403


# ---------- 503 when unconfigured ----------


def test_503_when_report_unconfigured(dup_corpus):
    corpus, _ = dup_corpus
    client = _client(corpus, report=None)
    r = client.get("/admin/duplications")
    assert r.status_code == 503
    assert "report not configured" in r.json()["detail"]


def test_503_when_report_missing(tmp_path: Path, dup_corpus):
    corpus, _ = dup_corpus
    missing = tmp_path / "no-such.tsv"
    client = _client(corpus, report=missing)
    r = client.get("/admin/duplications")
    assert r.status_code == 503
    assert "report missing" in r.json()["detail"]


# ---------- list ----------


def test_list_returns_summary_rows(dup_corpus):
    corpus, report = dup_corpus
    client = _client(corpus, report=report)
    r = client.get("/admin/duplications")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["returned"] == 1
    row = body["rows"][0]
    assert row["id"] == 1
    assert {row["textid_a"], row["textid_b"]} == {"KR0a0001", "KR0a0002"}
    # Summary strips spans_a / spans_b.
    assert "spans_a" not in row
    assert "longest_a" not in row
    assert row["action"] is None


def test_list_filter_pending_vs_done(dup_corpus):
    corpus, report = dup_corpus
    client = _client(corpus, report=report)
    assert client.get("/admin/duplications?filter=pending").json()["total"] == 1
    assert client.get("/admin/duplications?filter=done").json()["total"] == 0


def test_list_pagination(dup_corpus):
    corpus, report = dup_corpus
    client = _client(corpus, report=report)
    body = client.get("/admin/duplications?limit=1&offset=1").json()
    assert body["total"] == 1
    assert body["returned"] == 0


# ---------- detail ----------


def test_detail_includes_head_tail_around_longest(dup_corpus):
    corpus, report = dup_corpus
    client = _client(corpus, report=report)
    r = client.get("/admin/duplications/1?window=20")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["row"]["id"] == 1
    assert "longest_a" in body["row"]
    assert "spans_a" in body["row"]
    a = body["sides"]["a"]
    b = body["sides"]["b"]
    for side in (a, b):
        assert side["bucket"] == "body"
        assert "head" in side and "tail" in side
        assert isinstance(side["head"]["text"], str)
        assert isinstance(side["tail"]["text"], str)
        # window=20 means each snippet is at most 40 chars wide.
        assert len(side["head"]["text"]) <= 40
        assert len(side["tail"]["text"]) <= 40


def test_detail_404_when_row_id_out_of_range(dup_corpus):
    corpus, report = dup_corpus
    client = _client(corpus, report=report)
    r = client.get("/admin/duplications/99")
    assert r.status_code == 404


# ---------- action ----------


def test_action_requires_confirm(dup_corpus):
    corpus, report = dup_corpus
    client = _client(corpus, report=report)
    r = client.post(
        "/admin/duplications/1/action",
        json={"action": "keep"},
    )
    assert r.status_code == 400


def test_action_rejects_unknown_action(dup_corpus):
    corpus, report = dup_corpus
    client = _client(corpus, report=report)
    r = client.post(
        "/admin/duplications/1/action",
        json={"action": "nuke_everything", "confirm": True},
    )
    assert r.status_code == 400


def test_action_rejects_intra_action_on_inter_row(dup_corpus):
    corpus, report = dup_corpus
    client = _client(corpus, report=report)
    # Row 1 is inter-juan (two different textids); delete_span is intra-only.
    r = client.post(
        "/admin/duplications/1/action",
        json={"action": "delete_span", "confirm": True},
    )
    assert r.status_code == 400
    assert "not valid for inter-juan" in r.json()["detail"]


def test_action_records_decision_in_tsv(dup_corpus):
    corpus, report = dup_corpus
    client = _client(corpus, report=report)
    r = client.post(
        "/admin/duplications/1/action",
        json={"action": "keep", "confirm": True},
    )
    assert r.status_code == 202
    job_id = r.json()["id"]
    body = client.get(f"/admin/jobs/{job_id}").json()
    assert body["status"] == "success", body
    assert body["kind"] == "duplications_action"
    assert body["result"]["action"] == "keep"
    assert body["result"]["deletion_executed"] is False

    rows = read_duplications_report(report)
    assert rows[0]["action"] == "keep"
    assert rows[0]["action_actor"] == "alice"
    assert rows[0]["action_at"]  # ISO timestamp set
