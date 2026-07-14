"""Report-backed voice problem endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from bkk.serve import create_app
from bkk.serve.config import ServeConfig
from bkk.voice.problems import write_voice_problems_report


def _client(corpus: Path, report: Path | None) -> TestClient:
    corpus.mkdir(exist_ok=True)
    return TestClient(create_app(ServeConfig(
        corpus_root=corpus,
        index_path=corpus / "_corpus.bkkx",
        voice_problems_report_path=report,
    )))


def _write_report(path: Path) -> None:
    write_voice_problems_report([
        {
            "id": 1,
            "textid": "KR0a0001",
            "title": "One",
            "edition": None,
            "seq": 1,
            "bucket": "body",
            "offset": 3,
            "length": 0,
            "marker_id": "KR0a0001_bkk_001-bkkvprob1",
            "source": "parens",
            "code": "unmatched-open",
            "message": "unmatched '(' at offset 3",
        },
        {
            "id": 2,
            "textid": "KR0a0002",
            "title": "Two",
            "edition": None,
            "seq": 1,
            "bucket": "body",
            "offset": 5,
            "length": 0,
            "marker_id": "KR0a0002_bkk_001-bkkvprob1",
            "source": "parens",
            "code": "stray-close",
            "message": "unexpected ')' at offset 5 with no matching '('",
        },
    ], path)


def test_voice_problems_503_when_report_unconfigured(tmp_path: Path) -> None:
    client = _client(tmp_path / "corpus", None)
    r = client.get("/api/voice/problems")
    assert r.status_code == 503
    assert "report not configured" in r.json()["detail"]


def test_voice_problems_503_when_report_missing(tmp_path: Path) -> None:
    client = _client(tmp_path / "corpus", tmp_path / "missing.jsonl")
    r = client.get("/api/voice/problems")
    assert r.status_code == 503
    assert "report missing" in r.json()["detail"]


def test_voice_problems_reads_configured_report(tmp_path: Path) -> None:
    report = tmp_path / "voice-problems.jsonl"
    _write_report(report)
    client = _client(tmp_path / "corpus", report)

    body = client.get("/api/voice/problems?textid=KR0a0001").json()

    assert body["total"] == 1
    assert body["returned"] == 1
    assert body["items"][0]["marker_id"] == "KR0a0001_bkk_001-bkkvprob1"
