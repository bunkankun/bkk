"""Annotations endpoint: per-juan list pulled from sibling *.ann.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from bkk.serve import create_app
from bkk.serve.config import ServeConfig

from .conftest import write_bundle


def _write_ann(bundle_dir: Path, textid: str, seq: int, annotations: list[dict]) -> Path:
    path = bundle_dir / f"{textid}_{seq:03d}.ann.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "text_id": textid,
                "juan": f"{seq:03d}",
                "edition": "T",
                "annotations": annotations,
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def annotated_corpus(tmp_path: Path) -> Path:
    bundle = write_bundle(
        tmp_path,
        "ANN0001",
        "甲乙丙丁戊己庚辛壬癸",
        title="Annotated",
        identifiers={"krp": "ANN0001", "slug": ["annotated"]},
    )
    _write_ann(
        bundle,
        "ANN0001",
        1,
        [
            {
                "id": "uuid-1",
                "concept": "ASCEND",
                "concept_id": "uuid-c1",
                "seg_id": "ANN0001_T_001-001a.1",
                "pos": 0,
                "offset": 5,
                "length": 1,
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
            {
                "id": "uuid-2",
                "concept": "EARTH",
                "offset": 0,
                "form": {"orig": "甲", "orth": "甲", "pron": "jiǎ"},
                "sense": {"def": "first"},
            },
        ],
    )

    write_bundle(
        tmp_path,
        "PLAIN001",
        "ABCDEFGHIJ",
        title="No annotations",
    )
    return tmp_path


@pytest.fixture
def annotated_client(annotated_corpus: Path) -> TestClient:
    config = ServeConfig(
        corpus_root=annotated_corpus,
        index_path=annotated_corpus / "_corpus.bkkx",
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
    assert second["form"] == {"orig": "己", "orth": "己", "pron": "jǐ"}
    assert second["sense"]["def"] == "self; oneself"
    assert second["translation"]["text"] == "self"
    assert second["metadata"]["resp"] == "T"


def test_annotations_drops_absent_fields(annotated_client: TestClient):
    r = annotated_client.get("/bundles/ANN0001/juan/1/annotations")
    body = r.json()
    first = body[0]
    # The minimal entry has no length/translation/metadata — those keys must
    # be omitted (response_model_exclude_none=True), not emitted as null.
    assert "length" not in first
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
