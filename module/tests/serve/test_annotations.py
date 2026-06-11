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


# ── Provenance exposure + archive DELETE ──────────────────────────────────


from bkk.serve.routers.auth import SESSION_COOKIE
from bkk.serve.state import BlueskySession


_AUTHOR_DID = "did:plc:author-x"


def _login(client: TestClient, *, is_editor: bool = False, is_admin: bool = False) -> str:
    app = client.app  # type: ignore[attr-defined]
    session = app.state.bkk.sessions.create(
        login="tester",
        name=None,
        avatar_url=None,
        html_url=None,
        access_token="ghp-test",
        workspace={},
        is_editor=is_editor,
        is_admin=is_admin,
    )
    client.cookies.set(SESSION_COOKIE, session.id)
    return session.id


def _attach_bluesky(client: TestClient, session_id: str, did: str) -> None:
    app = client.app  # type: ignore[attr-defined]
    app.state.bkk.sessions.attach_bluesky(
        session_id,
        BlueskySession(
            did=did,
            handle="tester.bsky.social",
            access_jwt="jwt-access",
            refresh_jwt="jwt-refresh",
            service_endpoint="https://bsky.social",
        ),
    )


def test_annotations_expose_did_for_legacy_records(annotated_client: TestClient):
    """Legacy records expose ``did`` but no ``uri``."""
    body = annotated_client.get("/bundles/ANN0001/juan/1/annotations").json()
    legacy = next(a for a in body if a["id"] == "uuid-1")
    assert legacy["did"] == "did:plc:bkk-tls-legacy"
    assert "uri" not in legacy  # synth records have no at-URI
    # curation_state=accepted is non-default → exposed
    assert legacy["curation_state"] == "accepted"


def test_annotations_suppress_proposed_curation_state(tmp_path: Path):
    """``proposed`` is the default; don't ship it on every record."""
    corpus_root = tmp_path / "corpus"
    archive_root = tmp_path / "archive"
    corpus_root.mkdir()
    write_bundle(corpus_root, "PROP0001", "abc", title="t")
    _write_ann_jsonl(archive_root, "PROP0001", 1, [{
        "id": "uuid-p",
        "anchor": {"marker_id": "PROP0001_001-1a", "offset": 0, "length": 1},
        "payload": {"concept": "X"},
        "provenance": {"did": _AUTHOR_DID, "cid": "synth-p"},
        "curation_state": "proposed",
        "bucket": "body",
        "bucket_offset": 0,
    }])
    config = ServeConfig(
        corpus_root=corpus_root,
        index_path=corpus_root / "_corpus.bkkx",
        annotations_root=archive_root,
    )
    client = TestClient(create_app(config))
    body = client.get("/bundles/PROP0001/juan/1/annotations").json()
    assert "curation_state" not in body[0]


def test_annotations_expose_uri_for_bsky_native(tmp_path: Path):
    corpus_root = tmp_path / "corpus"
    archive_root = tmp_path / "archive"
    corpus_root.mkdir()
    write_bundle(corpus_root, "BSKY0001", "abc", title="t")
    bsky_uri = f"at://{_AUTHOR_DID}/org.bunkankun.annotation.note/abc"
    _write_ann_jsonl(archive_root, "BSKY0001", 1, [{
        "id": "bafy-cid",
        "anchor": {"marker_id": "BSKY0001_001-1a", "offset": 0, "length": 1},
        "payload": {"concept": "X"},
        "provenance": {"did": _AUTHOR_DID, "cid": "bafy-cid", "uri": bsky_uri},
        "curation_state": "accepted",
        "bucket": "body",
        "bucket_offset": 0,
    }])
    config = ServeConfig(
        corpus_root=corpus_root,
        index_path=corpus_root / "_corpus.bkkx",
        annotations_root=archive_root,
    )
    client = TestClient(create_app(config))
    body = client.get("/bundles/BSKY0001/juan/1/annotations").json()
    assert body[0]["did"] == _AUTHOR_DID
    assert body[0]["uri"] == bsky_uri


def test_delete_archive_annotation_requires_login(annotated_client: TestClient):
    r = annotated_client.delete("/bundles/ANN0001/juan/1/annotations/uuid-1")
    assert r.status_code == 401


def test_delete_archive_annotation_editor_allowed(annotated_client: TestClient):
    """An editor can delete a legacy/synth annotation."""
    _login(annotated_client, is_editor=True)
    r = annotated_client.delete("/bundles/ANN0001/juan/1/annotations/uuid-1")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "text_id": "ANN0001", "juan_seq": 1, "id": "uuid-1", "deleted": True,
    }
    # The other annotation survives; uuid-1 is gone.
    remaining = annotated_client.get("/bundles/ANN0001/juan/1/annotations").json()
    assert [a["id"] for a in remaining] == ["uuid-2"]


def test_delete_archive_annotation_non_editor_blocked(annotated_client: TestClient):
    """Non-editor, non-owner gets 403."""
    sid = _login(annotated_client, is_editor=False)
    _attach_bluesky(annotated_client, sid, "did:plc:somebody-else")
    r = annotated_client.delete("/bundles/ANN0001/juan/1/annotations/uuid-1")
    assert r.status_code == 403


def test_delete_archive_annotation_owner_allowed(tmp_path: Path):
    """A non-editor whose Bluesky DID matches the record's author can delete it."""
    corpus_root = tmp_path / "corpus"
    archive_root = tmp_path / "archive"
    corpus_root.mkdir()
    write_bundle(corpus_root, "OWN0001", "abc", title="t")
    _write_ann_jsonl(archive_root, "OWN0001", 1, [{
        "id": "uuid-mine",
        "anchor": {"marker_id": "OWN0001_001-1a", "offset": 0, "length": 1},
        "payload": {"concept": "X"},
        "provenance": {"did": _AUTHOR_DID, "cid": "synth-mine"},
        "curation_state": "accepted",
        "bucket": "body",
        "bucket_offset": 0,
    }])
    config = ServeConfig(
        corpus_root=corpus_root,
        index_path=corpus_root / "_corpus.bkkx",
        annotations_root=archive_root,
    )
    client = TestClient(create_app(config))
    sid = _login(client, is_editor=False)
    _attach_bluesky(client, sid, _AUTHOR_DID)
    r = client.delete("/bundles/OWN0001/juan/1/annotations/uuid-mine")
    assert r.status_code == 200, r.text


def test_delete_archive_annotation_rejects_bsky_native(tmp_path: Path):
    """Bsky-native records must go through the curation PATCH or the CLI."""
    corpus_root = tmp_path / "corpus"
    archive_root = tmp_path / "archive"
    corpus_root.mkdir()
    write_bundle(corpus_root, "BSKY0002", "abc", title="t")
    _write_ann_jsonl(archive_root, "BSKY0002", 1, [{
        "id": "bafy-bsky",
        "anchor": {"marker_id": "BSKY0002_001-1a", "offset": 0, "length": 1},
        "payload": {"concept": "X"},
        "provenance": {
            "did": _AUTHOR_DID,
            "cid": "bafy-bsky",
            "uri": f"at://{_AUTHOR_DID}/org.bunkankun.annotation.note/r",
        },
        "curation_state": "accepted",
        "bucket": "body",
        "bucket_offset": 0,
    }])
    config = ServeConfig(
        corpus_root=corpus_root,
        index_path=corpus_root / "_corpus.bkkx",
        annotations_root=archive_root,
    )
    client = TestClient(create_app(config))
    _login(client, is_editor=True)
    r = client.delete("/bundles/BSKY0002/juan/1/annotations/bafy-bsky")
    assert r.status_code == 400
    assert "curation-state" in r.json()["detail"]


def test_delete_archive_annotation_missing(annotated_client: TestClient):
    _login(annotated_client, is_editor=True)
    r = annotated_client.delete("/bundles/ANN0001/juan/1/annotations/uuid-nope")
    assert r.status_code == 404
