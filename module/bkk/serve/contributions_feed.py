"""Jetstream subscriber for the live BKK contributions feed.

Connects to Bluesky's Jetstream firehose
(``wss://jetstream2.us-east.bsky.network/subscribe``) filtered to our four
NSIDs and streams every ``commit`` event into an in-memory ring buffer that
the Chat tab reads via ``GET /api/contributions``.

This was previously a per-DID poller because the relay didn't propagate our
custom NSIDs. That's no longer true (the authority DID is published and DNS
``_lexicon`` TXT records resolve per group), so we get every record from
every DID in real time without a roster.

If ``dids`` is set in the constructor (from ``.bkkrc [annotations].dids``)
it's passed as a ``wantedDids`` filter to scope the firehose; otherwise the
feed is firehose-wide.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlencode

import websockets
from websockets.exceptions import ConnectionClosed

from .atproto import (
    ANNOTATION_NSID,
    COMMENT_NSID,
    LEGACY_ANNOTATION_NSID,
    TRANSLATION_NSID,
)


log = logging.getLogger("bkk.serve.contributions_feed")

BUFFER_MAX = 500
JETSTREAM_URL = "wss://jetstream2.us-east.bsky.network/subscribe"
BACKFILL_WINDOW_S = 24 * 3600  # initial cursor goes 24h back so the chat isn't empty on boot
RECONNECT_MIN_S = 1.0
RECONNECT_MAX_S = 60.0


# (NSID, kind) pairs. ``kind`` is the discriminator the SPA branches on.
# Legacy flat NSID rides along as kind=annotation.
COLLECTIONS: tuple[tuple[str, str], ...] = (
    (ANNOTATION_NSID, "annotation"),
    (LEGACY_ANNOTATION_NSID, "annotation"),
    (COMMENT_NSID, "comment"),
    (TRANSLATION_NSID, "translation"),
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
    ) -> None:
        self._dids = list(dids or [])
        self._max = max_entries
        self._url = url
        self._backfill_window_s = backfill_window_s
        self._by_uri: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        # Cursor advances as we process events; survives reconnects so we
        # don't lose anything during a transient disconnect.
        self._cursor_us: int | None = None

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self, limit: int) -> tuple[list[dict[str, Any]], bool]:
        items = sorted(self._by_uri.values(), key=lambda r: r["time_us"], reverse=True)
        truncated = len(self._by_uri) >= self._max
        return items[:limit], truncated

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
            await self._insert(entry)

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
        """Main loop: connect to Jetstream, consume, reconnect on drop."""
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
