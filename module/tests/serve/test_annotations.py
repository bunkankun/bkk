"""Annotations endpoint: per-juan list pulled from the bkk-annotations archive."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bkk.index.annotations import build_annotation_index
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


@pytest.fixture
def indexed_annotated_client(annotated_corpus: tuple[Path, Path]) -> TestClient:
    corpus_root, archive_root = annotated_corpus
    index_path = build_annotation_index(archive_root)
    config = ServeConfig(
        corpus_root=corpus_root,
        index_path=corpus_root / "_corpus.bkkx",
        annotations_root=archive_root,
        annotations_index_path=index_path,
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


def test_annotation_index_builds_locations_and_skips_bad_records(annotated_corpus: tuple[Path, Path]):
    _corpus_root, archive_root = annotated_corpus
    path = _write_ann_jsonl(
        archive_root,
        "ANN0001",
        2,
        [
            {
                "id": "bad-rejected",
                "payload": {"sense": {"id": "uuid-s1"}},
                "curation_state": "rejected",
                "bucket": "body",
                "bucket_offset": 1,
            },
            {
                "id": "bad-no-sense",
                "payload": {"sense": {"def": "missing id"}},
                "curation_state": "accepted",
                "bucket": "body",
                "bucket_offset": 2,
            },
            {
                "id": "good-2",
                "payload": {
                    "concept": "ASCEND",
                    "form": {"orth": "乙"},
                    "sense": {"id": "uuid-s1", "def": "second use"},
                },
                "anchor": {"marker_id": "m2", "length": 1},
                "curation_state": "proposed",
                "bucket": "body",
                "bucket_offset": 3,
            },
        ],
    )
    with path.open("a", encoding="utf-8") as f:
        f.write("{not json}\n")

    index_path = build_annotation_index(archive_root)
    import sqlite3
    conn = sqlite3.connect(index_path)
    try:
        rows = conn.execute(
            "SELECT text_id, juan_seq, annotation_id, orth, sense_def "
            "FROM annotation_location WHERE sense_uuid = ? "
            "ORDER BY text_id, juan_seq, bucket_offset, annotation_id",
            ("s1",),
        ).fetchall()
    finally:
        conn.close()
    assert rows == [
        ("ANN0001", 1, "uuid-1", "己", "self; oneself"),
        ("ANN0001", 2, "good-2", "乙", "second use"),
    ]


def test_annotations_by_sense_falls_back_to_jsonl_scan(annotated_client: TestClient):
    r = annotated_client.get("/annotations/by-sense/uuid-s1")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    loc = body["locations"][0]
    assert loc["text_id"] == "ANN0001"
    assert loc["seq"] == 1
    assert loc["bucket"] == "body"
    assert loc["offset"] == 5
    assert loc["length"] == 1
    assert loc["orth"] == "己"
    assert loc["sense_def"] == "self; oneself"
    assert loc["translation_title"] == "Test (en)"
    assert loc["translation_text"] == "self"
    assert loc["resp"] == "T"
    assert loc["curation_state"] == "accepted"
    assert loc["text_title"] == "Annotated"
    assert loc["context_left"] == "甲乙丙丁戊"
    assert loc["context_match"] == "己"
    assert loc["context_right"] == "庚辛壬癸"


def test_annotations_by_sense_reads_index(indexed_annotated_client: TestClient):
    r = indexed_annotated_client.get("/annotations/by-sense/uuid-s1")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    loc = body["locations"][0]
    assert loc["id"] == "uuid-1"
    assert loc["concept"] == "ASCEND"
    assert loc["concept_id"] == "uuid-c1"
    assert loc["marker_id"] == "ANN0001_T_001-001a.1"


def test_annotations_by_sense_empty_without_root(corpus: Path):
    config = ServeConfig(corpus_root=corpus, index_path=corpus / "_corpus.bkkx")
    client = TestClient(create_app(config))
    r = client.get("/annotations/by-sense/uuid-s1")
    assert r.status_code == 200
    assert r.json() == {"sense_uuid": "uuid-s1", "total": 0, "locations": []}
