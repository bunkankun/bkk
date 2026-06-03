"""Harvest annotation records from Bluesky into the archive.

The harvester walks ``com.atproto.repo.listRecords`` for one or more DIDs,
translates each lexicon record to BKK archive shape, and merges it into the
per-juan JSONL files under ``<annotations_root>/<text_id>/<text_id>_NNN.ann.jsonl``.

Merge is idempotent: existing lines whose ``provenance.cid`` matches an
incoming record are dropped before append. Seed records
(``did:plc:bkk-tls-legacy`` + ``synth-*`` CIDs) are never produced by the
harvester and are preserved untouched.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from bkk.importer.write.annotations import (
    bucket_sort_key,
    juan_archive_path,
    write_records_jsonl,
)
from bkk.serve.atproto import ANNOTATION_NSID, list_records
from bkk.serve.routers.annotations import read_raw_records

from .pds import resolve_pds


log = logging.getLogger("bkk.annotations.harvest")


# Marker-id shape: <text-id>_<edition>_<juan:03d>-<rest>
_JUAN_RE = re.compile(r"_[^_]+_(\d{3})-")


def juan_seq_from_marker_id(marker_id: str) -> int | None:
    m = _JUAN_RE.search(marker_id)
    return int(m.group(1)) if m else None


def _load_juan_yaml(corpus_root: Path, text_id: str, juan_seq: int) -> dict | None:
    # Bundle dirs live under <corpus_root>/<category>/<text_id>/.
    candidates = list(corpus_root.glob(f"*/{text_id}/{text_id}_{juan_seq:03d}.yaml"))
    if not candidates:
        return None
    try:
        return yaml.safe_load(candidates[0].read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        log.warning("failed to load %s: %s", candidates[0], exc)
        return None


def compute_bucket_position(
    juan_doc: dict, marker_id: str, anchor_offset: int,
) -> tuple[str, int] | None:
    """Return ``(bucket, bucket_offset)`` for ``marker_id`` in ``juan_doc``."""
    for bucket in ("front", "body", "back"):
        section = juan_doc.get(bucket)
        if not isinstance(section, dict):
            continue
        markers = section.get("markers")
        if not isinstance(markers, list):
            continue
        for m in markers:
            if isinstance(m, dict) and m.get("id") == marker_id:
                off = m.get("offset")
                if isinstance(off, int):
                    return bucket, off + anchor_offset
    return None


def wire_to_archive(
    wire: dict[str, Any], *, did: str, cid: str,
) -> dict[str, Any] | None:
    """Translate a lexicon record (camelCase) to archive shape (snake_case).

    Returns ``None`` if required fields are missing.
    """
    text_id = wire.get("textId")
    edition = wire.get("edition")
    anchor_in = wire.get("anchor") or {}
    payload = wire.get("payload") or {}
    if not isinstance(text_id, str) or not isinstance(edition, str):
        return None
    if not isinstance(anchor_in, dict):
        return None
    marker_id = anchor_in.get("markerId")
    offset = anchor_in.get("offset")
    length = anchor_in.get("length")
    if not isinstance(marker_id, str) or not isinstance(offset, int) or not isinstance(length, int):
        return None

    anchor: dict[str, Any] = {
        "marker_id": marker_id,
        "offset": offset,
        "length": length,
    }
    end_marker_id = anchor_in.get("endMarkerId")
    end_length = anchor_in.get("endLength")
    if isinstance(end_marker_id, str):
        anchor["end_marker_id"] = end_marker_id
    if isinstance(end_length, int):
        anchor["end_length"] = end_length

    source_role = wire.get("sourceRole")
    if not isinstance(source_role, str):
        source_role = f"bsky:{ANNOTATION_NSID}"

    supersedes = wire.get("supersedes")
    created_at = wire.get("createdAt")

    record: dict[str, Any] = {
        "id": cid,
        "text_id": text_id,
        "edition": edition,
        "anchor": anchor,
        "payload": payload if isinstance(payload, dict) else {},
        "provenance": {
            "did": did,
            "cid": cid,
            "created_at": created_at if isinstance(created_at, str) else None,
            "source_role": source_role,
            "supersedes": supersedes if isinstance(supersedes, str) else None,
        },
        "curation_state": "proposed",
    }
    return record


def fetch_did_records(did: str, *, limit: int | None = None) -> list[tuple[dict, str]]:
    """Page through listRecords for one DID. Returns (value, cid) pairs."""
    service = resolve_pds(did)
    out: list[tuple[dict, str]] = []
    cursor: str | None = None
    page_size = 100
    while True:
        result = list_records(
            service=service, repo=did, collection=ANNOTATION_NSID,
            limit=page_size, cursor=cursor,
        )
        for record in result.get("records") or []:
            if not isinstance(record, dict):
                continue
            value = record.get("value")
            cid = record.get("cid")
            if isinstance(value, dict) and isinstance(cid, str):
                out.append((value, cid))
                if limit is not None and len(out) >= limit:
                    return out
        cursor = result.get("cursor")
        if not cursor:
            return out


def harvest(
    dids: list[str],
    *,
    annotations_root: Path,
    corpus_root: Path,
    limit_per_did: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Pull records for each DID and merge into the archive.

    Returns ``{harvested, replaced, files_touched, skipped}``.
    """
    incoming_by_juan: dict[tuple[str, int], list[dict]] = defaultdict(list)
    juan_cache: dict[tuple[str, int], dict | None] = {}
    skipped = 0
    harvested = 0

    for did in dids:
        log.info("harvesting records for %s", did)
        for wire, cid in fetch_did_records(did, limit=limit_per_did):
            archive = wire_to_archive(wire, did=did, cid=cid)
            if archive is None:
                skipped += 1
                continue
            text_id = archive["text_id"]
            marker_id = archive["anchor"]["marker_id"]
            juan_seq = juan_seq_from_marker_id(marker_id)
            if juan_seq is None:
                log.warning("cannot derive juan_seq from marker_id %s", marker_id)
                skipped += 1
                continue

            key = (text_id, juan_seq)
            if key not in juan_cache:
                juan_cache[key] = _load_juan_yaml(corpus_root, text_id, juan_seq)
            juan_doc = juan_cache[key]
            if juan_doc is None:
                log.warning("juan not found in corpus: %s seq=%d", text_id, juan_seq)
                skipped += 1
                continue

            pos = compute_bucket_position(juan_doc, marker_id, archive["anchor"]["offset"])
            if pos is None:
                log.warning(
                    "marker_id %s not found in %s seq=%d", marker_id, text_id, juan_seq,
                )
                skipped += 1
                continue
            archive["bucket"], archive["bucket_offset"] = pos
            incoming_by_juan[key].append(archive)
            harvested += 1

    replaced = 0
    files_touched = 0
    for (text_id, juan_seq), incoming in incoming_by_juan.items():
        out_path = juan_archive_path(annotations_root, text_id, juan_seq)
        incoming_cids = {r["provenance"]["cid"] for r in incoming}
        superseded = {
            r["provenance"]["supersedes"]
            for r in incoming
            if r["provenance"].get("supersedes")
        }
        existing: list[dict] = []
        if out_path.exists():
            for raw in read_raw_records(out_path):
                cid = (raw.get("provenance") or {}).get("cid")
                if cid in incoming_cids or cid in superseded:
                    replaced += 1
                    continue
                existing.append(raw)
        merged = existing + incoming
        if dry_run:
            log.info("dry-run: would write %d records to %s", len(merged), out_path)
        else:
            write_records_jsonl(out_path, merged, sort=True)
        files_touched += 1

    return {
        "harvested": harvested,
        "replaced": replaced,
        "files_touched": files_touched,
        "skipped": skipped,
    }


__all__ = [
    "harvest",
    "fetch_did_records",
    "wire_to_archive",
    "compute_bucket_position",
    "juan_seq_from_marker_id",
]
