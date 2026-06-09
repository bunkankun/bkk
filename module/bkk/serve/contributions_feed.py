"""Periodic per-DID ``listRecords`` poller for the live contributions feed.

We originally tried Bluesky's Jetstream firehose, but the Bluesky relay does
not propagate our custom NSID ``org.bunkankun.annotation`` — it only carries
collections whose lexicons are resolvable (or explicitly allowlisted), and we
have not yet published ``org.bunkankun.annotation``'s lexicon. Until that
lands, this poller walks the DID roster configured under
``[annotations].dids`` in ``.bkkrc`` (the same list ``bkk annotations
harvest`` uses) and calls ``com.atproto.repo.listRecords`` on each one.

When the lexicon is published and records start appearing on the firehose,
the file ``bkk/serve/contributions_feed.py`` should be swapped back to the
jetstream subscriber (see the git history immediately before this rewrite).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from datetime import datetime
from typing import Any

from .atproto import ANNOTATION_NSID, list_records


log = logging.getLogger("bkk.serve.contributions_feed")

BUFFER_MAX = 500
POLL_INTERVAL_S = 30.0
PAGE_LIMIT = 100  # max records per DID per cycle (atproto listRecords cap)


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


def _entry_from_record(record: dict[str, Any], *, did: str) -> dict[str, Any] | None:
    """Translate a ``listRecords`` record dict to our flat contribution shape.

    Returns ``None`` when required fields are missing — we skip silently.
    """
    uri = record.get("uri")
    cid = record.get("cid")
    value = record.get("value") or {}
    if not isinstance(uri, str) or not isinstance(cid, str) or not isinstance(value, dict):
        return None

    anchor = value.get("anchor") or {}
    text_id = value.get("textId")
    edition = value.get("edition")
    marker_id = anchor.get("markerId") if isinstance(anchor, dict) else None
    offset = anchor.get("offset") if isinstance(anchor, dict) else None
    length = anchor.get("length") if isinstance(anchor, dict) else None
    if (
        not isinstance(text_id, str)
        or not isinstance(edition, str)
        or not isinstance(marker_id, str)
        or not isinstance(offset, int)
        or not isinstance(length, int)
    ):
        return None

    end_marker = anchor.get("endMarkerId")
    end_length = anchor.get("endLength")
    payload = value.get("payload")
    created_at = value.get("createdAt")
    source_role = value.get("sourceRole")

    return {
        "did": did,
        "cid": cid,
        "uri": uri,
        "text_id": text_id,
        "edition": edition,
        "marker_id": marker_id,
        "offset": offset,
        "length": length,
        "end_marker_id": end_marker if isinstance(end_marker, str) else None,
        "end_length": end_length if isinstance(end_length, int) else None,
        "payload": payload if isinstance(payload, dict) else {},
        "created_at": created_at if isinstance(created_at, str) else None,
        "time_us": _created_at_to_us(created_at),
        "source_role": source_role if isinstance(source_role, str) else None,
    }


def _poll_did_sync(did: str) -> list[dict[str, Any]]:
    """Blocking helper: pull one page of records for ``did`` and translate.

    Runs inside ``asyncio.to_thread`` from the poll loop. PDS resolution is
    deferred to here so a slow plc.directory call doesn't stall the loop.
    """
    from bkk.annotations.pds import resolve_pds

    service = resolve_pds(did)
    try:
        result = list_records(
            service=service, repo=did, collection=ANNOTATION_NSID,
            limit=PAGE_LIMIT, cursor=None,
        )
    except Exception as exc:
        log.warning("listRecords failed for %s: %s", did, exc)
        return []

    records = result.get("records") if isinstance(result, dict) else None
    if not isinstance(records, list):
        return []
    entries: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        entry = _entry_from_record(record, did=did)
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
            "contributions: polling %d DID(s) every %.0fs",
            len(self._dids), self._poll_interval,
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
