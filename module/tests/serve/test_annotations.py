"""Annotations endpoint: per-juan list pulled from the bkk-annotations archive."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bkk.serve import create_app
from bkk.serve.config import ServeConfig

from .conftest import write_bundle


def _write_ann_jsonl(
    archive_root: Path, textid: str, seq: int, records: list[dict],
) -> Path:
    text_dir = archive_root / textid
    text_dir.mkdir(parents=True, exist_ok=True)
    path = text_dir / f"{textid}_{seq:03d}.ann.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True))
            f.write("\n")
    return path


@pytest.fixture
def annotated_corpus(tmp_path: Path) -> tuple[Path, Path]:
    corpus_root = tmp_path / "corpus"
    archive_root = tmp_path / "bkk-annotations"
    corpus_root.mkdir()

    write_bundle(
        corpus_root,
        "ANN0001",
        "甲乙丙丁戊己庚辛壬癸",
        title="Annotated",
        identifiers={"krp": "ANN0001", "slug": ["annotated"]},
    )
    _write_ann_jsonl(
        archive_root,
        "ANN0001",
        1,
        [
            {
                "id": "uuid-1",
                "text_id": "ANN0001",
                "edition": "tls",
                "anchor": {
                    "marker_id": "ANN0001_T_001-001a.1",
                    "offset": 0,
                    "length": 1,
                },
                "payload": {
                    "concept": "ASCEND",
                    "concept_id": "uuid-c1",
                    "form": {"orig": "己", "orth": "己", "pron": "jǐ"},
                    "sense": {
                        "id": "uuid-s1",
                        "pos": "N",
                        "syn_func": "Nab",
                        "sem_feat": "self",
                        "def": "self; oneself",
                    },
                    "translation": {
                        "text": "self",
                        "title": "Test (en)",
                        "src": "Tester",
                    },
                    "metadata": {"resp": "T", "created": "2026-01-01"},
                },
                "provenance": {
                    "did": "did:plc:bkk-tls-legacy",
                    "cid": "synth-aaa",
                    "source_role": "tls:ann",
                    "supersedes": None,
                },
                "curation_state": "accepted",
                "bucket": "body",
                "bucket_offset": 5,
            },
            {
                "id": "uuid-2",
                "text_id": "ANN0001",
                "edition": "tls",
                "anchor": {
                    "marker_id": "ANN0001_T_001-001a.1",
                    "offset": 0,
                    "length": 0,
                },
                "payload": {
                    "concept": "EARTH",
                    "form": {"orig": "甲", "orth": "甲", "pron": "jiǎ"},
                    "sense": {"def": "first"},
                },
                "provenance": {
                    "did": "did:plc:bkk-tls-legacy",
                    "cid": "synth-bbb",
                    "source_role": "tls:ann",
                    "supersedes": None,
                },
                "curation_state": "accepted",
                "bucket": "body",
                "bucket_offset": 0,
            },
        ],
    )

    write_bundle(
        corpus_root,
        "PLAIN001",
        "ABCDEFGHIJ",
        title="No annotations",
    )
    return corpus_root, archive_root


@pytest.fixture
def annotated_client(annotated_corpus: tuple[Path, Path]) -> TestClient:
    corpus_root, archive_root = annotated_corpus
    config = ServeConfig(
        corpus_root=corpus_root,
        index_path=corpus_root / "_corpus.bkkx",
        annotations_root=archive_root,
    )
    return TestClient(create_app(config))


def test_annotations_list_returned_sorted_by_offset(annotated_client: TestClient):
    r = annotated_client.get("/bundles/ANN0001/juan/1/annotations")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) == 2
    assert [a["offset"] for a in body] == [0, 5]

    second = body[1]
    assert second["id"] == "uuid-1"
    assert second["concept"] == "ASCEND"
    assert second["length"] == 1
    assert second["marker_id"] == "ANN0001_T_001-001a.1"
    assert second["form"] == {"orig": "己", "orth": "己", "pron": "jǐ"}
    assert second["sense"]["def"] == "self; oneself"
    assert second["translation"]["text"] == "self"
    assert second["metadata"]["resp"] == "T"


def test_annotations_drops_absent_fields(annotated_client: TestClient):
    r = annotated_client.get("/bundles/ANN0001/juan/1/annotations")
    body = r.json()
    first = body[0]
    assert "translation" not in first
    assert "metadata" not in first
    assert first["concept"] == "EARTH"


def test_annotations_empty_list_when_no_ann_file(annotated_client: TestClient):
    r = annotated_client.get("/bundles/PLAIN001/juan/1/annotations")
    assert r.status_code == 200
    assert r.json() == []


def test_annotations_unknown_bundle_returns_404(annotated_client: TestClient):
    r = annotated_client.get("/bundles/NO_SUCH/juan/1/annotations")
    assert r.status_code == 404
    assert r.json()["error"] == "bundle_not_found"


def test_annotations_via_texts_alias_matches(annotated_client: TestClient):
    direct = annotated_client.get("/bundles/ANN0001/juan/1/annotations").json()
    aliased = annotated_client.get("/texts/annotated/juan/1/annotations").json()
    assert direct == aliased


def test_annotations_via_texts_alias_unknown_identifier(annotated_client: TestClient):
    r = annotated_client.get("/texts/no_such_id/juan/1/annotations")
    assert r.status_code == 400
    assert r.json()["error"] == "identifier_not_found"
