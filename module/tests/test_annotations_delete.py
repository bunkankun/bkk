"""Unit tests for ``bkk.annotations.delete``."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from bkk.annotations import cli as ad_cli
from bkk.annotations import delete as ad


DID = "did:plc:author"
URI = f"at://{DID}/org.bunkankun.annotation.note/aaa"
CID = "bafy-real-cid"


def _ann_record(*, uri: str, cid: str, marker_id: str = "KR1h0004_tls_001-1a.1") -> dict:
    return {
        "id": cid,
        "text_id": "KR1h0004",
        "edition": "tls",
        "anchor": {"marker_id": marker_id, "offset": 0, "length": 1},
        "payload": {"concept": "test"},
        "provenance": {
            "did": DID,
            "cid": cid,
            "uri": uri,
            "created_at": "2026-01-01T00:00:00Z",
            "source_role": "bsky:org.bunkankun.annotation.note",
            "supersedes": None,
        },
        "curation_state": "proposed",
        "rating": 0,
        "bucket": "body",
        "bucket_offset": 0,
    }


def _write_ann_file(root: Path, text_id: str, juan_seq: int, records: list[dict]) -> Path:
    path = root / text_id / f"{text_id}_{juan_seq:03d}.ann.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True))
            f.write("\n")
    return path


def test_locate_by_uri(tmp_path):
    r1 = _ann_record(uri=URI, cid=CID)
    r2 = _ann_record(uri=URI + "-other", cid=CID + "-other")
    _write_ann_file(tmp_path, "KR1h0004", 1, [r1, r2])

    hit = ad.locate(uri=URI, annotations_root=tmp_path)
    assert hit is not None
    assert hit.kind == ad.KIND_ANNOTATION
    assert hit.record["provenance"]["cid"] == CID


def test_locate_by_cid(tmp_path):
    r1 = _ann_record(uri=URI, cid=CID)
    _write_ann_file(tmp_path, "KR1h0004", 1, [r1])
    hit = ad.locate(cid=CID, annotations_root=tmp_path)
    assert hit is not None and hit.record["id"] == CID


def test_locate_by_id_finds_legacy(tmp_path):
    """Legacy records use ``id = "uuid-..."`` with no provenance.uri."""
    legacy = _ann_record(uri="", cid="synth-abc")
    legacy["id"] = "uuid-legacy-123"
    legacy["provenance"].pop("uri")
    _write_ann_file(tmp_path, "KR1h0004", 1, [legacy])
    hit = ad.locate(record_id="uuid-legacy-123", annotations_root=tmp_path)
    assert hit is not None and hit.record["provenance"]["cid"] == "synth-abc"


def test_locate_returns_none_on_miss(tmp_path):
    _write_ann_file(tmp_path, "KR1h0004", 1, [_ann_record(uri=URI, cid=CID)])
    assert ad.locate(uri="at://nope/x/y", annotations_root=tmp_path) is None


def test_archive_remove_drops_only_target(tmp_path):
    r1 = _ann_record(uri=URI, cid=CID)
    r2 = _ann_record(
        uri=f"at://{DID}/org.bunkankun.annotation.note/bbb",
        cid="bafy-keep",
        marker_id="KR1h0004_tls_001-1a.2",
    )
    path = _write_ann_file(tmp_path, "KR1h0004", 1, [r1, r2])
    hit = ad.locate(uri=URI, annotations_root=tmp_path)
    assert hit is not None
    assert ad.archive_remove(hit) is True

    with path.open(encoding="utf-8") as f:
        survivors = [json.loads(line) for line in f if line.strip()]
    assert len(survivors) == 1
    assert survivors[0]["provenance"]["cid"] == "bafy-keep"


def test_archive_remove_by_uri_convenience(tmp_path):
    """The Jetstream-facing convenience: locate + remove in one call."""
    r1 = _ann_record(uri=URI, cid=CID)
    path = _write_ann_file(tmp_path, "KR1h0004", 1, [r1])
    hit = ad.archive_remove_by_uri(URI, annotations_root=tmp_path)
    assert hit is not None
    assert hit.path == path
    assert path.read_text(encoding="utf-8") == ""

    # Re-running is a no-op now (already gone).
    assert ad.archive_remove_by_uri(URI, annotations_root=tmp_path) is None


def test_is_bsky_native():
    bsky = _ann_record(uri=URI, cid=CID)
    assert ad.is_bsky_native(bsky)

    synth = _ann_record(uri="at://did:plc:bkk-tls-legacy/x/y", cid="synth-abc")
    assert not ad.is_bsky_native(synth)

    no_uri = _ann_record(uri="", cid="synth-abc")
    no_uri["provenance"].pop("uri")
    assert not ad.is_bsky_native(no_uri)


def test_parse_at_uri():
    did, coll, rkey = ad.parse_at_uri(URI)
    assert did == DID
    assert coll == "org.bunkankun.annotation.note"
    assert rkey == "aaa"


def test_parse_at_uri_rejects_garbage():
    with pytest.raises(ValueError):
        ad.parse_at_uri("not-an-at-uri")
    with pytest.raises(ValueError):
        ad.parse_at_uri("at://just-did")


def test_locate_walks_comment_archive(tmp_path):
    """Comments live under a different root and suffix."""
    comments_root = tmp_path / "comments"
    record = {
        "id": "bafy-comment",
        "text_id": "KR1h0004",
        "provenance": {"did": DID, "cid": "bafy-comment",
                       "uri": f"at://{DID}/org.bunkankun.comment.post/zzz"},
    }
    path = comments_root / "KR1h0004" / "KR1h0004_001.cmt.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False))
        f.write("\n")

    hit = ad.locate(cid="bafy-comment", comments_root=comments_root)
    assert hit is not None and hit.kind == ad.KIND_COMMENT


def test_locate_requires_an_identifier():
    with pytest.raises(ValueError):
        ad.locate(annotations_root=Path("/tmp"))


def test_find_rejected_scans_all_roots(tmp_path):
    ann_root = tmp_path / "ann"
    cmt_root = tmp_path / "cmt"

    keep = _ann_record(uri=URI, cid=CID)
    drop = _ann_record(uri=URI + "-x", cid=CID + "-x",
                       marker_id="KR1h0004_tls_001-1a.2")
    drop["curation_state"] = "rejected"
    _write_ann_file(ann_root, "KR1h0004", 1, [keep, drop])

    cmt_drop = {
        "id": "bafy-cmt",
        "text_id": "KR1h0004",
        "curation_state": "rejected",
        "provenance": {"did": DID, "cid": "bafy-cmt",
                       "uri": f"at://{DID}/org.bunkankun.comment.post/c1"},
    }
    cmt_path = cmt_root / "KR1h0004" / "KR1h0004_001.cmt.jsonl"
    cmt_path.parent.mkdir(parents=True, exist_ok=True)
    with cmt_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps(cmt_drop, ensure_ascii=False) + "\n")

    hits = ad.find_rejected(
        annotations_root=ann_root,
        comments_root=cmt_root,
    )
    by_kind = sorted((h.kind, h.record["provenance"]["cid"]) for h in hits)
    assert by_kind == [
        (ad.KIND_ANNOTATION, CID + "-x"),
        (ad.KIND_COMMENT, "bafy-cmt"),
    ]


def test_find_rejected_empty_when_none_rejected(tmp_path):
    _write_ann_file(tmp_path, "KR1h0004", 1, [_ann_record(uri=URI, cid=CID)])
    assert ad.find_rejected(annotations_root=tmp_path) == []


def test_cli_delete_rejected_dry_run_lists_all(tmp_path, monkeypatch):
    """``bkk annotations delete --rejected --dry-run`` previews every hit."""
    keep = _ann_record(uri=URI, cid=CID)
    bsky_drop = _ann_record(
        uri=URI + "-x", cid=CID + "-x", marker_id="KR1h0004_tls_001-1a.2",
    )
    bsky_drop["curation_state"] = "rejected"
    legacy_drop = _ann_record(uri="", cid="synth-abc",
                              marker_id="KR1h0004_tls_001-1a.3")
    legacy_drop["id"] = "uuid-legacy"
    legacy_drop["provenance"]["did"] = "did:plc:bkk-tls-legacy"
    legacy_drop["provenance"].pop("uri")
    legacy_drop["curation_state"] = "rejected"
    _write_ann_file(tmp_path, "KR1h0004", 1, [keep, bsky_drop, legacy_drop])

    monkeypatch.setattr(ad_cli, "load_rc", lambda: {})

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ad_cli.run([
            "delete", "--rejected", "--dry-run",
            "--annotations-root", str(tmp_path),
        ])

    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["status"] == "ok"
    assert payload["rejected_count"] == 2

    # Verify each hit has the right action set; archive untouched (dry-run).
    by_cid = {r["cid"]: r for r in payload["results"]}
    bsky_actions = {a["action"] for a in by_cid[CID + "-x"]["actions"]}
    assert "would_delete_remote" in bsky_actions
    assert "would_remove_from" in bsky_actions

    legacy_actions = {a["action"] for a in by_cid["synth-abc"]["actions"]}
    assert "skipped_remote" in legacy_actions  # no at-uri on legacy
    assert "would_remove_from" in legacy_actions


def test_cli_delete_rejected_nothing_to_do(tmp_path, monkeypatch):
    _write_ann_file(tmp_path, "KR1h0004", 1, [_ann_record(uri=URI, cid=CID)])
    monkeypatch.setattr(ad_cli, "load_rc", lambda: {})

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ad_cli.run([
            "delete", "--rejected", "--dry-run",
            "--annotations-root", str(tmp_path),
        ])
    assert rc == 0
    assert json.loads(buf.getvalue()) == {
        "status": "nothing_to_do", "rejected_count": 0,
    }


def test_cli_delete_rejected_archive_only_rewrites_jsonl(tmp_path, monkeypatch):
    """With --archive-only, no bsky session is needed and the JSONL is rewritten."""
    keep = _ann_record(uri=URI, cid=CID)
    drop = _ann_record(uri=URI + "-x", cid=CID + "-x",
                       marker_id="KR1h0004_tls_001-1a.2")
    drop["curation_state"] = "rejected"
    path = _write_ann_file(tmp_path, "KR1h0004", 1, [keep, drop])

    monkeypatch.setattr(ad_cli, "load_rc", lambda: {})

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = ad_cli.run([
            "delete", "--rejected", "--archive-only",
            "--annotations-root", str(tmp_path),
        ])
    assert rc == 0
    payload = json.loads(buf.getvalue())
    assert payload["status"] == "ok"
    assert payload["rejected_count"] == 1

    with path.open(encoding="utf-8") as f:
        survivors = [json.loads(line) for line in f if line.strip()]
    assert len(survivors) == 1
    assert survivors[0]["provenance"]["cid"] == CID
