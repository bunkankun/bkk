"""Periodic per-DID ``listRecords`` poller for the live contributions feed.

We originally tried Bluesky's Jetstream firehose, but the Bluesky relay does
not propagate our custom NSIDs — it only carries collections whose lexicons
are resolvable (or explicitly allowlisted), and we have not yet published
``org.bunkankun.*``'s lexicons. Until that lands, this poller walks the DID
roster configured under ``[annotations].dids`` in ``.bkkrc`` (the same list
``bkk annotations harvest`` uses) and calls ``com.atproto.repo.listRecords``
on each one for each NSID we know about.

When the lexicons are published and records start appearing on the firehose,
this file should be swapped back to the jetstream subscriber (see the git
history immediately before this rewrite).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any

from .atproto import (
    ANNOTATION_NSID,
    COMMENT_NSID,
    LEGACY_ANNOTATION_NSID,
    TRANSLATION_NSID,
    list_records,
)


log = logging.getLogger("bkk.serve.contributions_feed")

BUFFER_MAX = 500
POLL_INTERVAL_S = 30.0
PAGE_LIMIT = 100  # max records per DID per cycle (atproto listRecords cap)


# (NSID, kind) — kind is the discriminator surfaced to the frontend so the
# Chat tab can render each record type appropriately. The legacy flat NSID
# rides along as kind=annotation.
COLLECTIONS: tuple[tuple[str, str], ...] = (
    (ANNOTATION_NSID, "annotation"),
    (LEGACY_ANNOTATION_NSID, "annotation"),
    (COMMENT_NSID, "comment"),
    (TRANSLATION_NSID, "translation"),
)


def _created_at_to_us(s: Any) -> int:
    """Parse a record's ``createdAt`` string to microseconds since epoch.

    Returns 0 when unparseable; callers can sort by it without crashing.
    """
    if not isinstance(s, str):
        return 0
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return int(dt.timestamp() * 1_000_000)


def _common_entry(
    record: dict[str, Any], *, did: str, kind: str,
) -> dict[str, Any] | None:
    """Extract the fields every kind shares; ``None`` when the record is malformed."""
    uri = record.get("uri")
    cid = record.get("cid")
    value = record.get("value") or {}
    if not isinstance(uri, str) or not isinstance(cid, str) or not isinstance(value, dict):
        return None
    text_id = value.get("textId")
    if not isinstance(text_id, str):
        return None
    created_at = value.get("createdAt")
    return {
        "kind": kind,
        "did": did,
        "cid": cid,
        "uri": uri,
        "text_id": text_id,
        "created_at": created_at if isinstance(created_at, str) else None,
        "time_us": _created_at_to_us(created_at),
        "value": value,
    }


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


def _entry_from_record(
    record: dict[str, Any], *, did: str, kind: str,
) -> dict[str, Any] | None:
    """Translate a ``listRecords`` record dict to our flat contribution shape."""
    common = _common_entry(record, did=did, kind=kind)
    if common is None:
        return None
    value = common.pop("value")
    anchor = _anchor_fields(value)

    if kind == "annotation":
        edition = value.get("edition")
        if not isinstance(edition, str) or not anchor:
            return None
        payload = value.get("payload")
        source_role = value.get("sourceRole")
        return {
            **common,
            "edition": edition,
            **anchor,
            "payload": payload if isinstance(payload, dict) else {},
            "source_role": source_role if isinstance(source_role, str) else None,
        }

    if kind == "comment":
        body = value.get("body")
        lang = value.get("lang")
        if not isinstance(body, str) or not isinstance(lang, str):
            return None
        edition = value.get("edition")
        parent = value.get("parent")
        entry: dict[str, Any] = {
            **common,
            "body": body,
            "lang": lang,
        }
        if anchor:
            entry.update(anchor)
        if isinstance(edition, str):
            entry["edition"] = edition
        if isinstance(parent, dict) and isinstance(parent.get("uri"), str):
            entry["parent"] = {
                "uri": parent["uri"],
                "cid": parent.get("cid"),
            }
        return entry

    if kind == "translation":
        text = value.get("text")
        lang = value.get("lang")
        edition = value.get("edition")
        translation_id = value.get("translationId")
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


def _poll_did_sync(did: str) -> list[dict[str, Any]]:
    """Blocking helper: pull one page of records for ``did`` from every collection.

    Runs inside ``asyncio.to_thread`` from the poll loop. PDS resolution is
    deferred to here so a slow plc.directory call doesn't stall the loop.
    """
    from bkk.annotations.pds import resolve_pds

    service = resolve_pds(did)
    entries: list[dict[str, Any]] = []
    for nsid, kind in COLLECTIONS:
        try:
            result = list_records(
                service=service, repo=did, collection=nsid,
                limit=PAGE_LIMIT, cursor=None,
            )
        except Exception as exc:
            log.debug("listRecords(%s) failed for %s: %s", nsid, did, exc)
            continue

        records = result.get("records") if isinstance(result, dict) else None
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            entry = _entry_from_record(record, did=did, kind=kind)
            if entry is not None:
                entries.append(entry)
    return entries


class ContributionFeed:
    """In-memory ring buffer of recent contributions, populated by polling.

    ``_by_uri`` is keyed by atproto URI for cheap O(1) dedupe and eviction in
    insertion order. ``snapshot`` returns items sorted by ``time_us`` desc so
    the UI shows newest first.
    """

    def __init__(
        self,
        dids: list[str] | None = None,
        *,
        max_entries: int = BUFFER_MAX,
        poll_interval: float = POLL_INTERVAL_S,
    ) -> None:
        self._dids = list(dids or [])
        self._by_uri: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._max = max_entries
        self._poll_interval = poll_interval
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def snapshot(self, limit: int) -> tuple[list[dict[str, Any]], bool]:
        items = sorted(self._by_uri.values(), key=lambda r: r["time_us"], reverse=True)
        truncated = len(self._by_uri) >= self._max
        return items[:limit], truncated

    async def _ingest(self, entries: list[dict[str, Any]]) -> int:
        new = 0
        async with self._lock:
            for entry in entries:
                uri = entry["uri"]
                if uri in self._by_uri:
                    continue
                self._by_uri[uri] = entry
                new += 1
                while len(self._by_uri) > self._max:
                    self._by_uri.popitem(last=False)
        return new

    async def _poll_once(self) -> None:
        for did in self._dids:
            if self._stop.is_set():
                return
            try:
                entries = await asyncio.to_thread(_poll_did_sync, did)
            except Exception as exc:
                log.warning("poll cycle error for %s: %s", did, exc)
                continue
            if entries:
                new = await self._ingest(entries)
                if new:
                    log.info(
                        "contributions: +%d from %s (buffer=%d)",
                        new, did, len(self._by_uri),
                    )

    async def run(self) -> None:
        """Main loop: poll every ``poll_interval`` seconds until cancelled."""
        if not self._dids:
            log.info(
                "contributions: no DIDs configured "
                "(set [annotations].dids in .bkkrc); feed will stay empty"
            )
            await self._stop.wait()
            return

        log.info(
            "contributions: polling %d DID(s) every %.0fs across %d collection(s)",
            len(self._dids), self._poll_interval, len(COLLECTIONS),
        )
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("contributions poll cycle crashed: %s", exc)
            elapsed = time.monotonic() - started
            wait = max(0.0, self._poll_interval - elapsed)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass


__all__ = ["ContributionFeed"]
