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

from bkk.index.merge import find_bundle
from bkk.importer.write.annotations import (
    bucket_sort_key,
    juan_archive_path,
    write_records_jsonl,
)
from bkk.marker_assets import (
    VALID_BUCKETS,
    effective_markers_for_bucket,
    load_marker_asset,
)
from bkk.serve.atproto import ANNOTATION_NSID, list_records
from bkk.serve.routers.annotations import read_raw_records

from .pds import resolve_pds


log = logging.getLogger("bkk.annotations.harvest")


# Marker-id shape: <text-id>_<edition>_<juan:03d>-<rest>
_MARKER_RE = re.compile(r"_(?P<edition>[^_]+)_(?P<seq>\d{3})-")


def parse_marker_id(marker_id: str) -> tuple[str, int] | None:
    """Return ``(edition_short, juan_seq)`` parsed from ``marker_id``."""
    m = _MARKER_RE.search(marker_id)
    if m is None:
        return None
    return m.group("edition"), int(m.group("seq"))


def juan_seq_from_marker_id(marker_id: str) -> int | None:
    parsed = parse_marker_id(marker_id)
    return parsed[1] if parsed else None


def _load_yaml(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        log.warning("failed to load %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def _juan_dir_and_filename(
    bundle: Path, manifest: dict, seq: int,
) -> tuple[Path, str] | None:
    parts = (manifest.get("assets") or {}).get("parts") or []
    entry = next(
        (p for p in parts if isinstance(p, dict) and p.get("seq") == seq),
        None,
    )
    if entry is None:
        return None
    filename = entry.get("filename")
    if not isinstance(filename, str):
        return None
    return bundle, filename


def _load_juan_with_markers(
    corpus_root: Path, text_id: str, edition_short: str, juan_seq: int,
) -> dict | None:
    """Load ``juan_seq`` for ``edition_short``, hydrated with its marker asset.

    Prefers the edition layer at ``editions/<edition>/`` so anchors against
    edition-specific seg ids resolve. Falls back to the master bundle when no
    edition manifest is present (e.g. for ``edition: bkk``).
    """
    bundle = find_bundle(corpus_root, text_id)
    if bundle is None:
        return None

    edition_dir = bundle / "editions" / edition_short
    edition_manifest = _load_yaml(
        edition_dir / f"{text_id}-{edition_short}.manifest.yaml",
    )
    if edition_manifest is not None:
        located = _juan_dir_and_filename(edition_dir, edition_manifest, juan_seq)
        if located is not None:
            base, filename = located
            juan = _load_yaml(base / filename)
            if juan is not None:
                return _hydrate(juan, edition_dir, edition_manifest, juan_seq)

    master_manifest = _load_yaml(bundle / f"{text_id}.manifest.yaml") or {}
    juan = _load_yaml(bundle / f"{text_id}_{juan_seq:03d}.yaml")
    if juan is None:
        return None
    return _hydrate(juan, bundle, master_manifest, juan_seq)


def _hydrate(
    juan: dict, manifest_dir: Path, manifest: dict, juan_seq: int,
) -> dict:
    """Attach effective markers (inline + asset) to each bucket on ``juan``."""
    asset = load_marker_asset(manifest_dir, manifest, juan_seq)
    for bucket_name in VALID_BUCKETS:
        bucket = juan.get(bucket_name)
        if not isinstance(bucket, dict):
            continue
        bucket["markers"] = effective_markers_for_bucket(juan, bucket_name, asset)
    return juan


def compute_bucket_position(
    juan_doc: dict, marker_id: str, anchor_offset: int,
) -> tuple[str, int] | None:
    """Return ``(bucket, bucket_offset)`` for ``marker_id`` in ``juan_doc``."""
    for bucket in VALID_BUCKETS:
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
    juan_cache: dict[tuple[str, str, int], dict | None] = {}
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
            parsed = parse_marker_id(marker_id)
            if parsed is None:
                log.warning(
                    "cannot parse edition/juan from marker_id %s", marker_id,
                )
                skipped += 1
                continue
            edition_short, juan_seq = parsed

            cache_key = (text_id, edition_short, juan_seq)
            if cache_key not in juan_cache:
                juan_cache[cache_key] = _load_juan_with_markers(
                    corpus_root, text_id, edition_short, juan_seq,
                )
            juan_doc = juan_cache[cache_key]
            if juan_doc is None:
                log.warning(
                    "juan not found in corpus: %s edition=%s seq=%d",
                    text_id, edition_short, juan_seq,
                )
                skipped += 1
                continue

            pos = compute_bucket_position(juan_doc, marker_id, archive["anchor"]["offset"])
            if pos is None:
                log.warning(
                    "marker_id %s not found in %s edition=%s seq=%d",
                    marker_id, text_id, edition_short, juan_seq,
                )
                skipped += 1
                continue
            archive["bucket"], archive["bucket_offset"] = pos
            incoming_by_juan[(text_id, juan_seq)].append(archive)
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
    "parse_marker_id",
]
