"""Contributions feed: read-time enrichment + curation-state PATCH."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from bkk.serve import create_app
from bkk.serve.config import ServeConfig
from bkk.serve.contributions_feed import ContributionFeed
from bkk.serve.curation import CurationResolver
from bkk.serve.routers.auth import SESSION_COOKIE
from bkk.serve.state import BlueskySession


TEXT_ID = "CON0001"
EDITION_SHORT = "bkk"
JUAN_SEQ = 1
MARKER_ID = f"{TEXT_ID}_{EDITION_SHORT}_001-1a"
MARKER_OFFSET = 3
DID = "did:plc:contrib-test"
EDITOR_DID = "did:plc:editor-test"
ADMIN_DID = "did:plc:admin-test"


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
        # Mirrors the wire record cached by _entry_from_commit.
        "_wire": {
            "textId": TEXT_ID,
            "edition": EDITION_SHORT,
            "anchor": {
                "markerId": MARKER_ID,
                "offset": anchor_offset,
                "length": 1,
            },
            "payload": {"form": {"orth": "甲"}},
            "sourceRole": "manual",
            "createdAt": "2026-06-01T00:00:00Z",
        },
        "_collection": "org.bunkankun.annotation.note",
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
        annotation_dids=(EDITOR_DID, ADMIN_DID, DID),
        annotation_admin_dids=(ADMIN_DID,),
        bluesky_enabled=True,
    )
    app = create_app(config)
    resolver = CurationResolver(
        editor_dids=config.annotation_dids,
        admin_dids=config.annotation_admin_dids,
    )
    feed = ContributionFeed(dids=[], resolver=resolver)
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


def _attach_bluesky(app, session_id: str, did: str) -> None:
    app.state.bkk.sessions.attach_bluesky(
        session_id,
        BlueskySession(
            did=did,
            handle="editor.test",
            access_jwt="jwt-access",
            refresh_jwt="jwt-refresh",
            service_endpoint="https://bsky.social",
        ),
    )


def _patch_create_record(monkeypatch: pytest.MonkeyPatch, *, did: str, rkey: str = "rkey-1", cid: str = "cid-jdg-1"):
    """Stub bkk.serve.routers.contributions.create_record; return calls list."""
    calls: list[dict] = []

    def _stub(*, service, access_jwt, refresh_jwt, repo, collection, record):
        calls.append({
            "service": service,
            "repo": repo,
            "collection": collection,
            "record": record,
        })
        return (
            {"uri": f"at://{did}/{collection}/{rkey}", "cid": cid},
            None,
        )

    monkeypatch.setattr(
        "bkk.serve.routers.contributions.create_record", _stub,
    )
    return calls


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


def _seed_archive(annotations_root: Path, *, cid: str) -> Path:
    archive_dir = annotations_root / TEXT_ID
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{TEXT_ID}_{JUAN_SEQ:03d}.ann.jsonl"
    record = {
        "id": "rec-1",
        "text_id": TEXT_ID,
        "edition": EDITION_SHORT,
        "anchor": {"marker_id": MARKER_ID, "offset": 2, "length": 1},
        "payload": {"form": {"orth": "甲"}},
        "provenance": {"did": DID, "cid": cid, "source_role": "manual"},
        "curation_state": "proposed",
        "bucket": "body",
        "bucket_offset": MARKER_OFFSET + 2,
    }
    archive_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return archive_path


def test_patch_curation_state_round_trip(env, monkeypatch):
    app, client, feed, annotations_root = env
    entry = _ann_entry(cid="cid-happy")
    _put_entry(feed, entry)
    archive_path = _seed_archive(annotations_root, cid="cid-happy")

    session_id = _login_as(client, app, is_editor=True)
    _attach_bluesky(app, session_id, EDITOR_DID)
    calls = _patch_create_record(monkeypatch, did=EDITOR_DID)

    r = client.patch(
        "/annotations/curation-state",
        json={"uri": entry["uri"], "state": "rejected"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["curation_state"] == "rejected"
    assert body["rating"] == 0
    assert body["text_id"] == TEXT_ID
    assert body["juan_seq"] == JUAN_SEQ
    assert body["curation_uri"].startswith(f"at://{EDITOR_DID}/")

    # Posted via Bluesky.
    assert len(calls) == 1
    assert calls[0]["collection"] == "org.bunkankun.curation.judgment"
    assert calls[0]["repo"] == EDITOR_DID
    assert calls[0]["record"]["target"]["uri"] == entry["uri"]
    assert calls[0]["record"]["state"] == "rejected"
    assert calls[0]["record"]["rating"] == 0

    # File on disk reflects the new state.
    on_disk = [
        json.loads(line)
        for line in archive_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert on_disk[0]["curation_state"] == "rejected"
    assert on_disk[0]["rating"] == 0

    # Buffer entry also reflects the new state so the next snapshot doesn't lag.
    refreshed = client.get("/contributions").json()["items"][0]
    assert refreshed["curation_state"] == "rejected"
    assert refreshed["rating"] == 0


def test_patch_materializes_jsonl_row_when_record_not_yet_on_disk(env, monkeypatch):
    """Rejecting a live-feed record that hasn't been harvested writes a fresh JSONL row.

    Without this, the on-disk archive never sees the rejection — the curation
    update lives only in the in-memory feed + bsky judgment record, and
    ``bkk annotations delete --rejected`` finds nothing to delete.
    """
    app, client, feed, annotations_root = env
    entry = _ann_entry(cid="cid-fresh")
    _put_entry(feed, entry)
    # No _seed_archive: the record has never been harvested to JSONL.

    session_id = _login_as(client, app, is_editor=True)
    _attach_bluesky(app, session_id, EDITOR_DID)
    _patch_create_record(monkeypatch, did=EDITOR_DID)

    r = client.patch(
        "/annotations/curation-state",
        json={"uri": entry["uri"], "state": "rejected"},
    )
    assert r.status_code == 200, r.text

    archive_path = (
        annotations_root / TEXT_ID / f"{TEXT_ID}_{JUAN_SEQ:03d}.ann.jsonl"
    )
    assert archive_path.exists(), "PATCH should materialize the JSONL file"
    on_disk = [
        json.loads(line)
        for line in archive_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(on_disk) == 1
    row = on_disk[0]
    assert row["curation_state"] == "rejected"
    assert row["provenance"]["cid"] == "cid-fresh"
    assert row["provenance"]["uri"] == entry["uri"]
    assert row["provenance"]["did"] == DID
    assert row["anchor"]["marker_id"] == MARKER_ID
    assert row["bucket"] == "body"
    assert row["bucket_offset"] == MARKER_OFFSET + 2


def test_patch_skips_materialize_without_wire(env, monkeypatch):
    """Legacy buffer entries (no ``_wire``) don't crash the PATCH path."""
    app, client, feed, annotations_root = env
    entry = _ann_entry(cid="cid-no-wire")
    entry.pop("_wire", None)
    entry.pop("_collection", None)
    _put_entry(feed, entry)

    session_id = _login_as(client, app, is_editor=True)
    _attach_bluesky(app, session_id, EDITOR_DID)
    _patch_create_record(monkeypatch, did=EDITOR_DID)

    r = client.patch(
        "/annotations/curation-state",
        json={"uri": entry["uri"], "state": "rejected"},
    )
    assert r.status_code == 200
    archive_path = (
        annotations_root / TEXT_ID / f"{TEXT_ID}_{JUAN_SEQ:03d}.ann.jsonl"
    )
    assert not archive_path.exists()


def test_patch_posts_curation_record(env, monkeypatch):
    """PATCH posts a record with the expected lexicon shape."""
    app, client, feed, _ = env
    entry = _ann_entry(cid="cid-post")
    _put_entry(feed, entry)

    session_id = _login_as(client, app, is_editor=True)
    _attach_bluesky(app, session_id, EDITOR_DID)
    calls = _patch_create_record(monkeypatch, did=EDITOR_DID)

    r = client.patch(
        "/annotations/curation-state",
        json={"uri": entry["uri"], "state": "accepted", "rating": 2},
    )
    assert r.status_code == 200, r.text

    rec = calls[0]["record"]
    assert rec["$type"] == "org.bunkankun.curation.judgment"
    assert rec["target"] == {"uri": entry["uri"], "cid": "cid-post"}
    assert rec["state"] == "accepted"
    assert rec["rating"] == 2
    assert "createdAt" in rec


def test_patch_fills_missing_field_from_resolver(env, monkeypatch):
    """When only `rating` is sent, the posted record preserves the existing state."""
    app, client, feed, _ = env
    entry = _ann_entry(cid="cid-fill")
    _put_entry(feed, entry)

    # Pre-seed resolver: a prior editor has already accepted this record.
    from bkk.serve.curation import Judgment
    feed.resolver.apply(Judgment(
        did=EDITOR_DID,
        rkey="rk-prior",
        cid="cid-prior-jdg",
        target_uri=entry["uri"],
        target_cid="cid-fill",
        state="accepted",
        rating=0,
        created_at_us=1_700_000_000_000_000,
    ))

    session_id = _login_as(client, app, is_editor=True)
    _attach_bluesky(app, session_id, EDITOR_DID)
    calls = _patch_create_record(monkeypatch, did=EDITOR_DID, rkey="rk-new")

    r = client.patch(
        "/annotations/curation-state",
        json={"uri": entry["uri"], "rating": 1},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # State carried forward from the resolver; rating updated.
    assert body["curation_state"] == "accepted"
    assert body["rating"] == 1
    # Posted record is a full snapshot.
    assert calls[0]["record"]["state"] == "accepted"
    assert calls[0]["record"]["rating"] == 1


def test_patch_rejects_self_accept(env, monkeypatch):
    """Authors may not set `state=accepted` on their own record."""
    app, client, feed, _ = env
    entry = _ann_entry(cid="cid-self")  # entry["did"] is DID
    _put_entry(feed, entry)

    session_id = _login_as(client, app, is_editor=True)
    # Editor's Bluesky DID matches the contribution's author DID.
    _attach_bluesky(app, session_id, DID)
    calls = _patch_create_record(monkeypatch, did=DID)

    r = client.patch(
        "/annotations/curation-state",
        json={"uri": entry["uri"], "state": "accepted"},
    )
    assert r.status_code == 403
    assert not calls  # nothing was posted


def test_patch_allows_self_reject(env, monkeypatch):
    """Authors may retract their own record by setting state=rejected."""
    app, client, feed, _ = env
    entry = _ann_entry(cid="cid-self-reject")
    _put_entry(feed, entry)

    session_id = _login_as(client, app, is_editor=True)
    _attach_bluesky(app, session_id, DID)
    calls = _patch_create_record(monkeypatch, did=DID)

    r = client.patch(
        "/annotations/curation-state",
        json={"uri": entry["uri"], "state": "rejected"},
    )
    assert r.status_code == 200, r.text
    assert calls[0]["record"]["state"] == "rejected"


def test_patch_allows_self_withdraw_to_proposed(env, monkeypatch):
    """Authors may move their own record back to ``proposed`` (un-reject)."""
    app, client, feed, _ = env
    entry = _ann_entry(cid="cid-self-withdraw")
    _put_entry(feed, entry)

    session_id = _login_as(client, app, is_editor=True)
    _attach_bluesky(app, session_id, DID)
    calls = _patch_create_record(monkeypatch, did=DID)

    r = client.patch(
        "/annotations/curation-state",
        json={"uri": entry["uri"], "state": "proposed"},
    )
    assert r.status_code == 200, r.text
    assert calls[0]["record"]["state"] == "proposed"


def test_patch_allows_self_rating_change(env, monkeypatch):
    """Authors may set `rating` on their own records."""
    app, client, feed, _ = env
    entry = _ann_entry(cid="cid-self-rating")
    _put_entry(feed, entry)

    session_id = _login_as(client, app, is_editor=True)
    _attach_bluesky(app, session_id, DID)
    calls = _patch_create_record(monkeypatch, did=DID)

    r = client.patch(
        "/annotations/curation-state",
        json={"uri": entry["uri"], "rating": 2},
    )
    assert r.status_code == 200, r.text
    assert calls[0]["record"]["rating"] == 2


def test_admin_can_patch_own_state(env, monkeypatch):
    """A DID listed in admin_dids bypasses the self-curation restriction."""
    app, client, feed, _ = env
    # Entry authored by ADMIN_DID — the admin is both author and curator.
    entry = _ann_entry(cid="cid-admin-self")
    entry["did"] = ADMIN_DID
    entry["uri"] = f"at://{ADMIN_DID}/org.bunkankun.annotation.note/cid-admin-self"
    _put_entry(feed, entry)

    session_id = _login_as(client, app, is_editor=True)
    _attach_bluesky(app, session_id, ADMIN_DID)
    calls = _patch_create_record(monkeypatch, did=ADMIN_DID)

    r = client.patch(
        "/annotations/curation-state",
        json={"uri": entry["uri"], "state": "accepted"},
    )
    assert r.status_code == 200, r.text
    assert calls[0]["record"]["state"] == "accepted"
