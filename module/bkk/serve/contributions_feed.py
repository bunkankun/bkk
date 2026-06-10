"""Jetstream subscriber for the live BKK contributions feed.

Two ingestion paths run in parallel:

1. **Startup seed** — for every DID in ``[annotations].dids``, one-shot
   ``com.atproto.repo.listRecords`` per NSID gives the buffer real
   historical depth (up to ``SEED_LIMIT`` records per DID per kind).
   Without this, the chat would only ever show whatever Jetstream still
   has in its (short) retention window.

2. **Live subscriber** — Jetstream firehose
   (``wss://jetstream2.us-east.bsky.network/subscribe``) filtered to our
   four NSIDs streams every ``commit`` event into the same buffer. The
   roster, if set, doubles as a ``wantedDids`` filter; otherwise the feed
   is firehose-wide so we pick up records from any DID in real time.

This was previously a per-DID poller because the relay didn't propagate
our custom NSIDs. That's no longer true (the authority DID is published
and DNS ``_lexicon`` TXT records resolve per group).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import websockets
from websockets.exceptions import ConnectionClosed

from .atproto import (
    ANNOTATION_NSID,
    COMMENT_NSID,
    CURATION_NSID,
    LEGACY_ANNOTATION_NSID,
    TRANSLATION_NSID,
    get_profiles,
    list_records,
)
from .curation import CurationResolver, Judgment


log = logging.getLogger("bkk.serve.contributions_feed")

BUFFER_MAX = 500
JETSTREAM_URL = "wss://jetstream2.us-east.bsky.network/subscribe"
BACKFILL_WINDOW_S = 24 * 3600  # Jetstream cursor for live-side gap recovery
SEED_LIMIT = 100  # per (DID, NSID) on startup
RECONNECT_MIN_S = 1.0
RECONNECT_MAX_S = 60.0


# (NSID, kind) pairs. ``kind`` is the discriminator the SPA branches on.
# Legacy flat NSID rides along as kind=annotation.
COLLECTIONS: tuple[tuple[str, str], ...] = (
    (ANNOTATION_NSID, "annotation"),
    (LEGACY_ANNOTATION_NSID, "annotation"),
    (COMMENT_NSID, "comment"),
    (TRANSLATION_NSID, "translation"),
    (CURATION_NSID, "curation"),
)
_KIND_BY_NSID: dict[str, str] = {nsid: kind for nsid, kind in COLLECTIONS}


def _anchor_fields(value: dict[str, Any]) -> dict[str, Any]:
    """Pull the anchor sub-object into snake_case fields. Empty dict if absent."""
    anchor = value.get("anchor")
    if not isinstance(anchor, dict):
        return {}
    marker_id = anchor.get("markerId")
    offset = anchor.get("offset")
    length = anchor.get("length")
    if (
        not isinstance(marker_id, str)
        or not isinstance(offset, int)
        or not isinstance(length, int)
    ):
        return {}
    out: dict[str, Any] = {
        "marker_id": marker_id,
        "offset": offset,
        "length": length,
    }
    end_marker = anchor.get("endMarkerId")
    end_length = anchor.get("endLength")
    if isinstance(end_marker, str):
        out["end_marker_id"] = end_marker
    if isinstance(end_length, int):
        out["end_length"] = end_length
    return out


def _entry_from_commit(
    *, did: str, time_us: int, collection: str, rkey: str, cid: str,
    record: dict[str, Any],
) -> dict[str, Any] | None:
    """Translate a Jetstream commit event to our flat contribution shape."""
    kind = _KIND_BY_NSID.get(collection)
    if kind is None:
        return None
    text_id = record.get("textId")
    if not isinstance(text_id, str):
        return None
    created_at = record.get("createdAt")
    common: dict[str, Any] = {
        "kind": kind,
        "did": did,
        "cid": cid,
        "uri": f"at://{did}/{collection}/{rkey}",
        "text_id": text_id,
        "created_at": created_at if isinstance(created_at, str) else None,
        "time_us": time_us,
    }
    anchor = _anchor_fields(record)

    if kind == "annotation":
        edition = record.get("edition")
        if not isinstance(edition, str) or not anchor:
            return None
        payload = record.get("payload")
        source_role = record.get("sourceRole")
        return {
            **common,
            "edition": edition,
            **anchor,
            "payload": payload if isinstance(payload, dict) else {},
            "source_role": source_role if isinstance(source_role, str) else None,
        }

    if kind == "comment":
        body = record.get("body")
        lang = record.get("lang")
        if not isinstance(body, str) or not isinstance(lang, str):
            return None
        edition = record.get("edition")
        parent = record.get("parent")
        entry: dict[str, Any] = {**common, "body": body, "lang": lang}
        if anchor:
            entry.update(anchor)
        if isinstance(edition, str):
            entry["edition"] = edition
        if isinstance(parent, dict) and isinstance(parent.get("uri"), str):
            entry["parent"] = {"uri": parent["uri"], "cid": parent.get("cid")}
        return entry

    if kind == "translation":
        text = record.get("text")
        lang = record.get("lang")
        edition = record.get("edition")
        translation_id = record.get("translationId")
        if (
            not isinstance(text, str)
            or not isinstance(lang, str)
            or not isinstance(edition, str)
            or not isinstance(translation_id, str)
            or not anchor
        ):
            return None
        return {
            **common,
            "edition": edition,
            **anchor,
            "translation_id": translation_id,
            "text": text,
            "lang": lang,
        }

    return None


def _judgment_from_record(
    *, did: str, rkey: str, cid: str, record: dict[str, Any],
) -> Judgment | None:
    """Parse a curation.judgment wire record into a Judgment.

    Returns ``None`` when required fields are missing or mistyped.
    """
    target = record.get("target")
    if not isinstance(target, dict):
        return None
    target_uri = target.get("uri")
    if not isinstance(target_uri, str):
        return None
    state = record.get("state")
    if not isinstance(state, str):
        return None
    rating = record.get("rating")
    if not isinstance(rating, int) or rating < 0 or rating > 2:
        rating = 0
    target_cid = target.get("cid")
    return Judgment(
        did=did,
        rkey=rkey,
        cid=cid,
        target_uri=target_uri,
        target_cid=target_cid if isinstance(target_cid, str) else None,
        state=state,
        rating=rating,
        created_at_us=_created_at_us(record.get("createdAt")),
    )


def _created_at_us(s: Any) -> int:
    """Parse a record's ``createdAt`` string to microseconds since epoch.

    Returns 0 when unparseable; sortable without crashing.
    """
    if not isinstance(s, str):
        return 0
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return int(dt.timestamp() * 1_000_000)


def _entry_from_listrecord(rec: dict[str, Any], *, did: str) -> dict[str, Any] | None:
    """Translate a ``listRecords`` record dict to the same shape as commits.

    The wire shape from listRecords is ``{uri, cid, value}``; we parse the
    collection + rkey out of the URI and use the record's own ``createdAt``
    as ``time_us`` since there's no relay envelope to lift it from.
    """
    uri = rec.get("uri")
    cid = rec.get("cid")
    value = rec.get("value")
    if not isinstance(uri, str) or not isinstance(cid, str) or not isinstance(value, dict):
        return None
    # at://<did>/<collection>/<rkey>
    try:
        _, _, _did, collection, rkey = uri.split("/", 4)
    except ValueError:
        return None
    return _entry_from_commit(
        did=did, time_us=_created_at_us(value.get("createdAt")),
        collection=collection, rkey=rkey, cid=cid, record=value,
    )


def _build_url(*, dids: list[str], cursor_us: int | None) -> str:
    params: list[tuple[str, str]] = []
    for nsid, _ in COLLECTIONS:
        params.append(("wantedCollections", nsid))
    for did in dids:
        params.append(("wantedDids", did))
    if cursor_us is not None:
        params.append(("cursor", str(cursor_us)))
    return f"{JETSTREAM_URL}?{urlencode(params)}"


class ContributionFeed:
    """In-memory ring buffer of recent contributions, populated by Jetstream.

    ``_by_uri`` is keyed by atproto URI for O(1) dedupe and eviction in
    insertion order. ``snapshot`` returns items sorted by ``time_us`` desc
    so the UI shows newest first.
    """

    def __init__(
        self,
        dids: list[str] | None = None,
        *,
        max_entries: int = BUFFER_MAX,
        url: str = JETSTREAM_URL,
        backfill_window_s: float = BACKFILL_WINDOW_S,
        resolver: CurationResolver | None = None,
    ) -> None:
        self._dids = list(dids or [])
        self._max = max_entries
        self._url = url
        self._backfill_window_s = backfill_window_s
        self._by_uri: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._profile_cache: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        # Cursor advances as we process events; survives reconnects so we
        # don't lose anything during a transient disconnect.
        self._cursor_us: int | None = None
        # Resolver consumes curation.judgment records; everything we insert
        # into the buffer gets its current resolved (state, rating) stamped.
        self.resolver: CurationResolver = resolver or CurationResolver()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self, limit: int) -> tuple[list[dict[str, Any]], bool]:
        items = sorted(self._by_uri.values(), key=lambda r: r["time_us"], reverse=True)
        truncated = len(self._by_uri) >= self._max
        return items[:limit], truncated

    def find(self, uri: str) -> dict[str, Any] | None:
        return self._by_uri.get(uri)

    def ensure_profiles(self, dids: list[str]) -> None:
        """Fetch and cache Bluesky profiles for any DIDs not yet resolved. Best-effort."""
        missing = [d for d in dids if d not in self._profile_cache]
        if not missing:
            return
        fetched = get_profiles(missing)
        self._profile_cache.update(fetched)

    def get_cached_profile(self, did: str) -> dict[str, Any] | None:
        return self._profile_cache.get(did)

    def set_curation(self, uri: str, state: str, rating: int) -> bool:
        entry = self._by_uri.get(uri)
        if entry is None:
            return False
        entry["curation_state"] = state
        entry["rating"] = rating
        return True

    async def _insert(self, entry: dict[str, Any]) -> None:
        async with self._lock:
            uri = entry["uri"]
            # On update, refresh in place; on create, append.
            if uri in self._by_uri:
                self._by_uri[uri] = entry
                self._by_uri.move_to_end(uri)
            else:
                self._by_uri[uri] = entry
                while len(self._by_uri) > self._max:
                    self._by_uri.popitem(last=False)

    async def _delete(self, uri: str) -> None:
        async with self._lock:
            self._by_uri.pop(uri, None)

    async def _handle_commit(self, msg: dict[str, Any]) -> None:
        commit = msg.get("commit")
        if not isinstance(commit, dict):
            return
        did = msg.get("did")
        time_us = msg.get("time_us")
        collection = commit.get("collection")
        rkey = commit.get("rkey")
        op = commit.get("operation")
        if not (
            isinstance(did, str)
            and isinstance(time_us, int)
            and isinstance(collection, str)
            and isinstance(rkey, str)
        ):
            return
        self._cursor_us = time_us
        if collection == CURATION_NSID:
            await self._handle_curation_commit(
                op=op, did=did, rkey=rkey, commit=commit,
            )
            return
        if op == "delete":
            await self._delete(f"at://{did}/{collection}/{rkey}")
            return
        if op not in ("create", "update"):
            return
        cid = commit.get("cid")
        record = commit.get("record")
        if not isinstance(cid, str) or not isinstance(record, dict):
            return
        entry = _entry_from_commit(
            did=did, time_us=time_us, collection=collection, rkey=rkey,
            cid=cid, record=record,
        )
        if entry is not None:
            self._stamp_curation(entry)
            await self._insert(entry)

    async def _handle_curation_commit(
        self, *, op: str | None, did: str, rkey: str, commit: dict[str, Any],
    ) -> None:
        if op == "delete":
            affected = self.resolver.remove(did=did, rkey=rkey)
            if affected is not None:
                await self._refresh_curation(affected)
            return
        if op not in ("create", "update"):
            return
        cid = commit.get("cid")
        record = commit.get("record")
        if not isinstance(cid, str) or not isinstance(record, dict):
            return
        j = _judgment_from_record(did=did, rkey=rkey, cid=cid, record=record)
        if j is None:
            return
        if self.resolver.apply(j):
            await self._refresh_curation(j.target_uri)

    def _stamp_curation(self, entry: dict[str, Any]) -> None:
        state, rating = self.resolver.get(entry["uri"])
        entry["curation_state"] = state
        entry["rating"] = rating

    async def _refresh_curation(self, uri: str) -> None:
        async with self._lock:
            entry = self._by_uri.get(uri)
            if entry is None:
                return
            state, rating = self.resolver.get(uri)
            entry["curation_state"] = state
            entry["rating"] = rating

    async def _seed_did(self, did: str) -> int:
        """One-shot listRecords seed across all collections for ``did``.

        Resolves the PDS in a thread to avoid blocking the loop on a slow
        plc.directory call. Returns the count of entries successfully
        inserted (post-dedupe).
        """
        from bkk.annotations.pds import resolve_pds

        try:
            service = await asyncio.to_thread(resolve_pds, did)
        except Exception as exc:
            log.warning("seed: resolve_pds(%s) failed: %s", did, exc)
            return 0
        new = 0
        for nsid, _ in COLLECTIONS:
            try:
                result = await asyncio.to_thread(
                    list_records, service=service, repo=did,
                    collection=nsid, limit=SEED_LIMIT, cursor=None,
                )
            except Exception as exc:
                log.debug("seed: listRecords(%s) failed for %s: %s", nsid, did, exc)
                continue
            records = result.get("records") if isinstance(result, dict) else None
            if not isinstance(records, list):
                continue
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                if nsid == CURATION_NSID:
                    if self._apply_curation_listrecord(rec, did=did):
                        new += 1
                    continue
                entry = _entry_from_listrecord(rec, did=did)
                if entry is None:
                    continue
                self._stamp_curation(entry)
                before = len(self._by_uri)
                await self._insert(entry)
                if len(self._by_uri) > before:
                    new += 1
        return new

    def _apply_curation_listrecord(
        self, rec: dict[str, Any], *, did: str,
    ) -> bool:
        """Apply a seed-fetched curation record to the resolver. Returns True
        when the resolved value for the target changed."""
        uri = rec.get("uri")
        cid = rec.get("cid")
        value = rec.get("value")
        if not isinstance(uri, str) or not isinstance(cid, str) or not isinstance(value, dict):
            return False
        try:
            _, _, _did, _coll, rkey = uri.split("/", 4)
        except ValueError:
            return False
        j = _judgment_from_record(did=did, rkey=rkey, cid=cid, record=value)
        if j is None:
            return False
        return self.resolver.apply(j)

    async def _seed_all(self) -> None:
        if not self._dids:
            log.info(
                "contributions: no seed DIDs configured "
                "([annotations].dids in .bkkrc); skipping historical seed"
            )
            return
        log.info("contributions: seeding from %d DID(s)…", len(self._dids))
        total = 0
        for did in self._dids:
            if self._stop.is_set():
                return
            total += await self._seed_did(did)
        log.info("contributions: seed complete (+%d entries, buffer=%d)", total, len(self._by_uri))

    async def _consume(self, ws: Any) -> None:
        async for raw in ws:
            if self._stop.is_set():
                return
            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if msg.get("kind") != "commit":
                continue
            try:
                await self._handle_commit(msg)
            except Exception:
                log.exception("error handling commit event")

    async def run(self) -> None:
        """Main loop: seed historical records, then consume Jetstream forever."""
        await self._seed_all()
        if self._cursor_us is None and self._backfill_window_s > 0:
            self._cursor_us = int((time.time() - self._backfill_window_s) * 1_000_000)

        backoff = RECONNECT_MIN_S
        while not self._stop.is_set():
            url = _build_url(dids=self._dids, cursor_us=self._cursor_us)
            try:
                log.info("contributions: connecting to Jetstream (cursor=%s)", self._cursor_us)
                async with websockets.connect(url, max_size=2**20) as ws:
                    backoff = RECONNECT_MIN_S
                    log.info(
                        "contributions: subscribed to %d collection(s)%s",
                        len(COLLECTIONS),
                        f", {len(self._dids)} DID filter(s)" if self._dids else "",
                    )
                    await self._consume(ws)
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as exc:
                log.warning("contributions: connection closed: %s", exc)
            except Exception as exc:
                log.warning("contributions: connect/consume failed: %s", exc)

            if self._stop.is_set():
                return
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, RECONNECT_MAX_S)


__all__ = ["ContributionFeed"]
