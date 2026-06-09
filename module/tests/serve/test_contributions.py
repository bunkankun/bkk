"""Contributions feed: read-time enrichment + curation-state PATCH."""

from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from bkk.serve import create_app
from bkk.serve.config import ServeConfig
from bkk.serve.contributions_feed import ContributionFeed
from bkk.serve.routers.auth import SESSION_COOKIE


TEXT_ID = "CON0001"
EDITION_SHORT = "bkk"
JUAN_SEQ = 1
MARKER_ID = f"{TEXT_ID}_{EDITION_SHORT}_001-1a"
MARKER_OFFSET = 3
DID = "did:plc:contrib-test"


def _write_bundle_with_marker(corpus_root: Path) -> None:
    """Write a synthetic bundle whose body contains a single addressable marker."""
    bundle_dir = corpus_root / TEXT_ID
    bundle_dir.mkdir(parents=True)
    body_text = "甲乙丙丁戊己庚辛"
    (bundle_dir / f"{TEXT_ID}_001.yaml").write_text(
        yaml.safe_dump(
            {
                "canonical_identifier": f"bkk:test/{TEXT_ID}/v1/juan/1",
                "seq": 1,
                "body": {
                    "text": body_text,
                    "hash": "sha256:0",
                    "markers": [
                        {"id": MARKER_ID, "type": "pb", "offset": MARKER_OFFSET},
                    ],
                },
                "hash": "sha256:0",
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (bundle_dir / f"{TEXT_ID}.manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "canonical_identifier": f"bkk:test/{TEXT_ID}/v1",
                "editions": [{"short": EDITION_SHORT, "label": "bkk"}],
                "assets": {
                    "parts": [
                        {"seq": 1, "filename": f"{TEXT_ID}_001.yaml", "hash": "sha256:0"},
                    ],
                },
                "table_of_contents": [
                    {
                        "ref": {
                            "seq": 1,
                            "marker_id": MARKER_ID,
                            "span": ["body", 0, len(body_text)],
                        },
                        "label": "Tian Gan",
                    }
                ],
                "metadata": {
                    "title": "Heavenly Stems",
                    "edition": {"short": EDITION_SHORT},
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def _ann_entry(*, cid: str, anchor_offset: int = 2) -> dict:
    """Live-buffer shape for an annotation contribution."""
    return {
        "kind": "annotation",
        "did": DID,
        "cid": cid,
        "uri": f"at://{DID}/org.bunkankun.annotation.note/{cid}",
        "text_id": TEXT_ID,
        "created_at": "2026-06-01T00:00:00Z",
        "time_us": 1_700_000_000_000_000,
        "edition": EDITION_SHORT,
        "marker_id": MARKER_ID,
        "offset": anchor_offset,
        "length": 1,
        "payload": {"form": {"orth": "甲"}},
        "source_role": "manual",
    }


@pytest.fixture
def env(tmp_path: Path):
    """Corpus + annotations_root + client + attached feed; lifespan disabled."""
    monkey = pytest.MonkeyPatch()
    monkey.setenv("BKK_DISABLE_CONTRIBUTIONS_POLL", "1")

    corpus_root = tmp_path / "corpus"
    corpus_root.mkdir()
    annotations_root = tmp_path / "bkk-annotations"
    annotations_root.mkdir()
    _write_bundle_with_marker(corpus_root)

    config = ServeConfig(
        corpus_root=corpus_root,
        index_path=corpus_root / "_corpus.bkkx",
        annotations_root=annotations_root,
    )
    app = create_app(config)
    feed = ContributionFeed(dids=[])
    app.state.bkk.contributions = feed
    client = TestClient(app)
    try:
        yield app, client, feed, annotations_root
    finally:
        client.close()
        monkey.undo()


def _put_entry(feed: ContributionFeed, entry: dict) -> None:
    """Synchronously seed the feed buffer (skipping its asyncio lock)."""
    feed._by_uri[entry["uri"]] = entry  # type: ignore[attr-defined]


def test_contributions_enriches_title_juan_bucket_master_offset(env):
    _, client, feed, _ = env
    entry = _ann_entry(cid="cid-aaa", anchor_offset=2)
    _put_entry(feed, entry)

    r = client.get("/contributions?limit=10")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["title"] == "Heavenly Stems"
    assert item["juan_seq"] == JUAN_SEQ
    assert item["bucket"] == "body"
    assert item["master_offset"] == MARKER_OFFSET + 2


def test_contributions_unknown_textid_leaves_enriched_fields_null(env):
    _, client, feed, _ = env
    entry = _ann_entry(cid="cid-bbb")
    entry["text_id"] = "DOES_NOT_EXIST"
    _put_entry(feed, entry)

    items = client.get("/contributions").json()["items"]
    item = next(i for i in items if i["cid"] == "cid-bbb")
    assert item["title"] is None
    # juan_seq is still parsed from marker_id even when the bundle is unknown.
    assert item["juan_seq"] == JUAN_SEQ
    assert item["bucket"] is None
    assert item["master_offset"] is None


def test_contributions_unparseable_marker_id(env):
    _, client, feed, _ = env
    entry = _ann_entry(cid="cid-ccc")
    entry["marker_id"] = "not-a-real-marker"
    _put_entry(feed, entry)

    item = next(
        i for i in client.get("/contributions").json()["items"] if i["cid"] == "cid-ccc"
    )
    assert item["juan_seq"] is None
    assert item["bucket"] is None
    assert item["master_offset"] is None


def test_patch_curation_state_requires_login(env):
    _, client, feed, _ = env
    _put_entry(feed, _ann_entry(cid="cid-anon"))

    r = client.patch(
        "/annotations/curation-state",
        json={"uri": _ann_entry(cid="cid-anon")["uri"], "state": "accepted"},
    )
    assert r.status_code == 401


def _login_as(client: TestClient, app, *, is_editor: bool) -> str:
    session = app.state.bkk.sessions.create(
        login="tester",
        name=None,
        avatar_url=None,
        html_url=None,
        access_token="ghp-test",
        workspace={},
        is_editor=is_editor,
    )
    client.cookies.set(SESSION_COOKIE, session.id)
    return session.id


def test_patch_curation_state_requires_editor_role(env):
    app, client, feed, _ = env
    _put_entry(feed, _ann_entry(cid="cid-non-editor"))
    _login_as(client, app, is_editor=False)

    r = client.patch(
        "/annotations/curation-state",
        json={
            "uri": _ann_entry(cid="cid-non-editor")["uri"],
            "state": "accepted",
        },
    )
    assert r.status_code == 403


def test_patch_curation_state_rejects_unknown_state(env):
    app, client, feed, _ = env
    _put_entry(feed, _ann_entry(cid="cid-bad-state"))
    _login_as(client, app, is_editor=True)

    r = client.patch(
        "/annotations/curation-state",
        json={
            "uri": _ann_entry(cid="cid-bad-state")["uri"],
            "state": "garbage",
        },
    )
    assert r.status_code == 422


def test_patch_curation_state_404_when_not_in_buffer(env):
    app, client, _, _ = env
    _login_as(client, app, is_editor=True)

    r = client.patch(
        "/annotations/curation-state",
        json={
            "uri": (
                f"at://{DID}/org.bunkankun.annotation.note/missing"
            ),
            "state": "accepted",
        },
    )
    assert r.status_code == 404


def test_patch_curation_state_round_trip(env):
    app, client, feed, annotations_root = env
    entry = _ann_entry(cid="cid-happy")
    _put_entry(feed, entry)

    # Seed the on-disk archive with the matching cid in provenance.
    archive_dir = annotations_root / TEXT_ID
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{TEXT_ID}_{JUAN_SEQ:03d}.ann.jsonl"
    record = {
        "id": "rec-1",
        "text_id": TEXT_ID,
        "edition": EDITION_SHORT,
        "anchor": {"marker_id": MARKER_ID, "offset": 2, "length": 1},
        "payload": {"form": {"orth": "甲"}},
        "provenance": {"did": DID, "cid": "cid-happy", "source_role": "manual"},
        "curation_state": "proposed",
        "bucket": "body",
        "bucket_offset": MARKER_OFFSET + 2,
    }
    archive_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    _login_as(client, app, is_editor=True)
    r = client.patch(
        "/annotations/curation-state",
        json={"uri": entry["uri"], "state": "rejected"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["curation_state"] == "rejected"
    assert body["text_id"] == TEXT_ID
    assert body["juan_seq"] == JUAN_SEQ

    # File on disk reflects the new state.
    on_disk = [
        json.loads(line)
        for line in archive_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert on_disk[0]["curation_state"] == "rejected"

    # Buffer entry also reflects the new state so the next snapshot doesn't lag.
    refreshed = client.get("/contributions").json()["items"][0]
    assert refreshed["curation_state"] == "rejected"
