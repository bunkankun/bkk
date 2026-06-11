"""Jetstream delete → on-disk archive propagation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from bkk.serve.contributions_feed import ContributionFeed


DID = "did:plc:author"
ANN_RKEY = "abc"
ANN_URI = f"at://{DID}/org.bunkankun.annotation.note/{ANN_RKEY}"
ANN_CID = "bafy-ann"


def _ann_record(uri: str, cid: str) -> dict:
    return {
        "id": cid,
        "text_id": "KR1h0004",
        "edition": "tls",
        "anchor": {"marker_id": "KR1h0004_tls_001-1a.1", "offset": 0, "length": 1},
        "payload": {"concept": "x"},
        "provenance": {
            "did": DID,
            "cid": cid,
            "uri": uri,
            "created_at": "2026-01-01T00:00:00Z",
            "source_role": "bsky:org.bunkankun.annotation.note",
            "supersedes": None,
        },
        "bucket": "body",
        "bucket_offset": 0,
    }


def _delete_commit(*, collection: str, rkey: str, did: str = DID) -> dict:
    return {
        "kind": "commit",
        "did": did,
        "time_us": 1_700_000_000_000_000,
        "commit": {
            "collection": collection,
            "rkey": rkey,
            "operation": "delete",
        },
    }


def _write_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_jetstream_delete_removes_archive_row(tmp_path):
    annotations_root = tmp_path / "bkk-annotations"
    ann_path = annotations_root / "KR1h0004" / "KR1h0004_001.ann.jsonl"
    _write_jsonl(ann_path, _ann_record(ANN_URI, ANN_CID))

    feed = ContributionFeed(
        dids=[],
        annotations_root=annotations_root,
        archive_on_delete=True,
    )
    msg = _delete_commit(collection="org.bunkankun.annotation.note", rkey=ANN_RKEY)
    asyncio.run(feed._handle_commit(msg))

    assert ann_path.read_text(encoding="utf-8") == ""


def test_jetstream_delete_noop_when_flag_off(tmp_path):
    annotations_root = tmp_path / "bkk-annotations"
    ann_path = annotations_root / "KR1h0004" / "KR1h0004_001.ann.jsonl"
    _write_jsonl(ann_path, _ann_record(ANN_URI, ANN_CID))
    original = ann_path.read_text(encoding="utf-8")

    feed = ContributionFeed(
        dids=[],
        annotations_root=annotations_root,
        archive_on_delete=False,
    )
    msg = _delete_commit(collection="org.bunkankun.annotation.note", rkey=ANN_RKEY)
    asyncio.run(feed._handle_commit(msg))

    assert ann_path.read_text(encoding="utf-8") == original


def test_jetstream_delete_skips_missing_root(tmp_path):
    """Comment delete with no comments_root set: no-op, no crash."""
    feed = ContributionFeed(
        dids=[],
        annotations_root=tmp_path / "bkk-annotations",
        archive_on_delete=True,
    )
    msg = _delete_commit(collection="org.bunkankun.comment.post", rkey="zzz")
    asyncio.run(feed._handle_commit(msg))  # no crash


def test_jetstream_delete_evicts_buffer_alongside_archive(tmp_path):
    """The legacy in-memory eviction still happens when archive propagation runs."""
    annotations_root = tmp_path / "bkk-annotations"
    ann_path = annotations_root / "KR1h0004" / "KR1h0004_001.ann.jsonl"
    _write_jsonl(ann_path, _ann_record(ANN_URI, ANN_CID))

    feed = ContributionFeed(
        dids=[],
        annotations_root=annotations_root,
        archive_on_delete=True,
    )
    feed._by_uri[ANN_URI] = {"uri": ANN_URI, "time_us": 1}  # type: ignore[attr-defined]
    msg = _delete_commit(collection="org.bunkankun.annotation.note", rkey=ANN_RKEY)
    asyncio.run(feed._handle_commit(msg))

    assert ANN_URI not in feed._by_uri  # type: ignore[attr-defined]
    assert ann_path.read_text(encoding="utf-8") == ""


def test_jetstream_delete_propagates_to_comments_root(tmp_path):
    comments_root = tmp_path / "bkk-comments"
    rkey = "ccc"
    uri = f"at://{DID}/org.bunkankun.comment.post/{rkey}"
    cmt_path = comments_root / "KR1h0004" / "KR1h0004_001.cmt.jsonl"
    cmt_path.parent.mkdir(parents=True, exist_ok=True)
    cmt_path.write_text(
        json.dumps(
            {
                "id": "bafy-cmt",
                "text_id": "KR1h0004",
                "provenance": {"did": DID, "cid": "bafy-cmt", "uri": uri},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    feed = ContributionFeed(
        dids=[],
        comments_root=comments_root,
        archive_on_delete=True,
    )
    msg = _delete_commit(collection="org.bunkankun.comment.post", rkey=rkey)
    asyncio.run(feed._handle_commit(msg))

    assert cmt_path.read_text(encoding="utf-8") == ""


def test_jetstream_delete_curation_collection_does_not_touch_archive(tmp_path):
    """Curation deletes go through the resolver, not the archive."""
    annotations_root = tmp_path / "bkk-annotations"
    ann_path = annotations_root / "KR1h0004" / "KR1h0004_001.ann.jsonl"
    _write_jsonl(ann_path, _ann_record(ANN_URI, ANN_CID))
    original = ann_path.read_text(encoding="utf-8")

    feed = ContributionFeed(
        dids=[],
        annotations_root=annotations_root,
        archive_on_delete=True,
    )
    msg = _delete_commit(collection="org.bunkankun.curation.judgment", rkey="qqq")
    asyncio.run(feed._handle_commit(msg))

    assert ann_path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "collection",
    [
        "org.bunkankun.annotation.note",
        "org.bunkankun.annotation",  # legacy flat NSID
    ],
)
def test_jetstream_delete_handles_legacy_annotation_nsid(tmp_path, collection):
    annotations_root = tmp_path / "bkk-annotations"
    rkey = "xyz"
    uri = f"at://{DID}/{collection}/{rkey}"
    cid = f"bafy-{collection}"
    ann_path = annotations_root / "KR1h0004" / "KR1h0004_001.ann.jsonl"
    _write_jsonl(ann_path, _ann_record(uri, cid))

    feed = ContributionFeed(
        dids=[],
        annotations_root=annotations_root,
        archive_on_delete=True,
    )
    msg = _delete_commit(collection=collection, rkey=rkey)
    asyncio.run(feed._handle_commit(msg))

    assert ann_path.read_text(encoding="utf-8") == ""
