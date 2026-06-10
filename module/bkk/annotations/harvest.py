"""Harvest BKK records from Bluesky into the on-disk archive.

The harvester walks ``com.atproto.repo.listRecords`` for one or more DIDs
across the four BKK collections (annotation.note, comment.post,
translation.segment, plus the legacy flat annotation NSID), translates each
wire record into archive shape, and merges into per-juan JSONL files:

* annotations: ``<annotations_root>/<text_id>/<text_id>_NNN.ann.jsonl``
* comments:    ``<comments_root>/<text_id>/<text_id>_NNN.cmt.jsonl``
                (pure replies without an anchor: ``<text_id>/_replies.cmt.jsonl``)
* translations: ``<translations_root>/<text_id>/<text_id>_NNN.tr.jsonl``

Folding harvested translation segments back into the actual ``bkk-tr-*``
bundles is a separate task; for now they live alongside annotations and
comments as JSONL.

Merge is idempotent: existing lines whose ``provenance.cid`` matches an
incoming record are dropped before append. Seed records
(``did:plc:bkk-tls-legacy`` + ``synth-*`` CIDs) are never produced by the
harvester and are preserved untouched.

This module is one half of the "two-place rule"; the matching
archive→wire side lives in ``bkk.serve.routers.annotations_write``.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

import yaml

from bkk.index.merge import find_bundle
from bkk.importer.write.annotations import (
    juan_archive_path,
    write_records_jsonl,
)
from bkk.marker_assets import (
    VALID_BUCKETS,
    effective_markers_for_bucket,
    load_marker_asset,
)
from bkk.serve.atproto import (
    ANNOTATION_NSID,
    COMMENT_NSID,
    CURATION_NSID,
    LEGACY_ANNOTATION_NSID,
    TRANSLATION_NSID,
    list_records,
)
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


# ── Wire → archive (one converter per NSID) ──────────────────────────────


def _anchor_from_wire(anchor_in: Any) -> dict[str, Any] | None:
    """Convert the wire anchor object back to snake_case archive shape.

    Returns ``None`` when the required fields are missing or mistyped.
    """
    if not isinstance(anchor_in, dict):
        return None
    marker_id = anchor_in.get("markerId")
    offset = anchor_in.get("offset")
    length = anchor_in.get("length")
    if (
        not isinstance(marker_id, str)
        or not isinstance(offset, int)
        or not isinstance(length, int)
    ):
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
    return anchor


def _provenance(
    *,
    did: str,
    cid: str,
    wire: dict[str, Any],
    default_source_role: str,
    uri: str | None = None,
) -> dict[str, Any]:
    source_role = wire.get("sourceRole")
    if not isinstance(source_role, str):
        source_role = default_source_role
    created_at = wire.get("createdAt")
    supersedes = wire.get("supersedes")
    prov: dict[str, Any] = {
        "did": did,
        "cid": cid,
        "created_at": created_at if isinstance(created_at, str) else None,
        "source_role": source_role,
        "supersedes": supersedes if isinstance(supersedes, str) else None,
    }
    if uri is not None:
        prov["uri"] = uri
    return prov


def annotation_wire_to_archive(
    wire: dict[str, Any], *, did: str, cid: str,
    uri: str | None = None, nsid: str = ANNOTATION_NSID,
) -> dict[str, Any] | None:
    """Translate an annotation lexicon record (camelCase) to archive shape.

    Returns ``None`` if required fields are missing. ``nsid`` is parameterised
    so the legacy flat NSID flows through the same path.
    """
    text_id = wire.get("textId")
    edition = wire.get("edition")
    payload = wire.get("payload") or {}
    if not isinstance(text_id, str) or not isinstance(edition, str):
        return None
    anchor = _anchor_from_wire(wire.get("anchor"))
    if anchor is None:
        return None
    return {
        "id": cid,
        "text_id": text_id,
        "edition": edition,
        "anchor": anchor,
        "payload": payload if isinstance(payload, dict) else {},
        "provenance": _provenance(
            did=did, cid=cid, uri=uri, wire=wire,
            default_source_role=f"bsky:{nsid}",
        ),
        "curation_state": "proposed",
    }


# Backwards-compatible alias for the previous name; one-callsite reference
# in tests and external scripts.
wire_to_archive = annotation_wire_to_archive


def comment_wire_to_archive(
    wire: dict[str, Any], *, did: str, cid: str, uri: str | None = None,
) -> dict[str, Any] | None:
    """Translate a comment.post wire record to archive shape.

    Enforces the lexicon-level xor: exactly one of ``anchor`` / ``parent``
    must be present.
    """
    text_id = wire.get("textId")
    body = wire.get("body")
    lang = wire.get("lang")
    fmt = wire.get("format")
    if (
        not isinstance(text_id, str)
        or not isinstance(body, str)
        or not isinstance(lang, str)
        or not isinstance(fmt, str)
    ):
        return None

    anchor_wire = wire.get("anchor")
    parent_wire = wire.get("parent")
    anchor = _anchor_from_wire(anchor_wire) if anchor_wire is not None else None
    has_anchor = anchor is not None
    has_parent = isinstance(parent_wire, dict) and isinstance(parent_wire.get("uri"), str)
    if has_anchor == has_parent:
        # both or neither — invalid per the lexicon
        return None

    edition = wire.get("edition")
    record: dict[str, Any] = {
        "id": cid,
        "text_id": text_id,
        "body": body,
        "lang": lang,
        "format": fmt,
        "provenance": _provenance(
            did=did, cid=cid, uri=uri, wire=wire,
            default_source_role=f"bsky:{COMMENT_NSID}",
        ),
        "curation_state": "proposed",
    }
    if has_anchor:
        if not isinstance(edition, str):
            return None
        record["edition"] = edition
        record["anchor"] = anchor
    else:
        record["parent"] = {
            "uri": parent_wire["uri"],
            "cid": parent_wire.get("cid"),
        }
        root_wire = wire.get("root")
        if isinstance(root_wire, dict) and isinstance(root_wire.get("uri"), str):
            record["root"] = {
                "uri": root_wire["uri"],
                "cid": root_wire.get("cid"),
            }
    return record


def translation_wire_to_archive(
    wire: dict[str, Any], *, did: str, cid: str, uri: str | None = None,
) -> dict[str, Any] | None:
    """Translate a translation.segment wire record to archive shape."""
    text_id = wire.get("textId")
    edition = wire.get("edition")
    translation_id = wire.get("translationId")
    text = wire.get("text")
    lang = wire.get("lang")
    fmt = wire.get("format")
    if (
        not isinstance(text_id, str)
        or not isinstance(edition, str)
        or not isinstance(translation_id, str)
        or not isinstance(text, str)
        or not isinstance(lang, str)
        or not isinstance(fmt, str)
    ):
        return None
    anchor = _anchor_from_wire(wire.get("anchor"))
    if anchor is None:
        return None

    record: dict[str, Any] = {
        "id": cid,
        "text_id": text_id,
        "edition": edition,
        "anchor": anchor,
        "translation_id": translation_id,
        "text": text,
        "lang": lang,
        "format": fmt,
        "provenance": _provenance(
            did=did, cid=cid, uri=uri, wire=wire,
            default_source_role=f"bsky:{TRANSLATION_NSID}",
        ),
        "curation_state": "proposed",
    }
    title = wire.get("title")
    note = wire.get("note")
    if isinstance(title, str):
        record["title"] = title
    if isinstance(note, str):
        record["note"] = note
    return record


def curation_wire_to_archive(
    wire: dict[str, Any], *, did: str, rkey: str, cid: str,
) -> dict[str, Any] | None:
    """Translate a curation.judgment wire record to resolver-input shape.

    Curation records are never persisted as their own JSONL — the resolver
    consumes them and stamps the resolved ``(state, rating)`` onto the
    target record's archive line. Returns ``None`` when required fields
    are missing.
    """
    target = wire.get("target")
    if not isinstance(target, dict):
        return None
    target_uri = target.get("uri")
    target_cid = target.get("cid")
    state = wire.get("state")
    if not isinstance(target_uri, str) or not isinstance(state, str):
        return None
    rating = wire.get("rating")
    if not isinstance(rating, int) or rating < 0 or rating > 2:
        rating = 0
    created_at = wire.get("createdAt")
    supersedes = wire.get("supersedes")
    return {
        "target_uri": target_uri,
        "target_cid": target_cid if isinstance(target_cid, str) else None,
        "state": state,
        "rating": rating,
        "created_at": created_at if isinstance(created_at, str) else None,
        "supersedes": supersedes if isinstance(supersedes, str) else None,
        "provenance": {"did": did, "rkey": rkey, "cid": cid},
    }


# ── Listing / fetching ───────────────────────────────────────────────────


def fetch_did_records(
    did: str, *, collection: str = ANNOTATION_NSID, limit: int | None = None,
) -> list[tuple[dict, str, str | None]]:
    """Page through listRecords for one DID + collection.

    Returns ``(value, cid, uri)`` triples. ``uri`` is the ``at://<did>/<nsid>/<rkey>``
    handle as returned by listRecords; it may be ``None`` for legacy callers but
    is populated for every PDS response in practice.
    """
    service = resolve_pds(did)
    out: list[tuple[dict, str, str | None]] = []
    cursor: str | None = None
    page_size = 100
    while True:
        result = list_records(
            service=service, repo=did, collection=collection,
            limit=page_size, cursor=cursor,
        )
        for record in result.get("records") or []:
            if not isinstance(record, dict):
                continue
            value = record.get("value")
            cid = record.get("cid")
            uri = record.get("uri") if isinstance(record.get("uri"), str) else None
            if isinstance(value, dict) and isinstance(cid, str):
                out.append((value, cid, uri))
                if limit is not None and len(out) >= limit:
                    return out
        cursor = result.get("cursor")
        if not cursor:
            return out


# ── Archive paths for new record kinds ───────────────────────────────────


def comment_archive_path(
    comments_root: Path, text_id: str, juan_seq: int | None,
) -> Path:
    """JSONL path for one juan's anchored comments, or the replies file."""
    if juan_seq is None:
        return comments_root / text_id / "_replies.cmt.jsonl"
    return comments_root / text_id / f"{text_id}_{juan_seq:03d}.cmt.jsonl"


def translation_archive_path(
    translations_root: Path, text_id: str, juan_seq: int,
) -> Path:
    return translations_root / text_id / f"{text_id}_{juan_seq:03d}.tr.jsonl"


def _write_jsonl_merged(
    out_path: Path, incoming: list[dict[str, Any]], *, sort: bool,
    dry_run: bool,
) -> int:
    """Merge ``incoming`` into ``out_path``, deduping by ``provenance.cid``.

    Returns the number of pre-existing records that were replaced.
    """
    incoming_cids = {r["provenance"]["cid"] for r in incoming}
    superseded = {
        r["provenance"]["supersedes"]
        for r in incoming
        if r["provenance"].get("supersedes")
    }
    existing: list[dict[str, Any]] = []
    replaced = 0
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
    elif sort:
        write_records_jsonl(out_path, merged, sort=True)
    else:
        # Comments/translations have no bucket sort key; write in arrival order.
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            for r in merged:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True))
                f.write("\n")
    return replaced


# ── Main harvest entry point ─────────────────────────────────────────────


def harvest(
    dids: list[str],
    *,
    annotations_root: Path,
    corpus_root: Path,
    comments_root: Path | None = None,
    translations_root: Path | None = None,
    limit_per_did: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Pull records for each DID across all BKK collections and merge.

    Returns counts: ``{harvested, replaced, files_touched, skipped}``. The
    comment and translation roots default to siblings of ``annotations_root``
    when not supplied, which matches the conventional `.bkkrc` layout
    documented in workflow.md.
    """
    if comments_root is None:
        comments_root = annotations_root.parent / "bkk-comments"
    if translations_root is None:
        translations_root = annotations_root.parent / "bkk-translations"

    juan_cache: dict[tuple[str, str, int], dict | None] = {}
    incoming_annotations: dict[tuple[str, int], list[dict]] = defaultdict(list)
    incoming_comments: dict[tuple[str, int | None], list[dict]] = defaultdict(list)
    incoming_translations: dict[tuple[str, int], list[dict]] = defaultdict(list)

    skipped = 0
    harvested = 0

    def _resolve_juan(archive: dict) -> tuple[int | None, str | None]:
        """Return (juan_seq, edition) for an anchored archive record, or (None, None)."""
        anchor = archive.get("anchor") or {}
        marker_id = anchor.get("marker_id")
        if not isinstance(marker_id, str):
            return None, None
        parsed = parse_marker_id(marker_id)
        if parsed is None:
            return None, None
        return parsed[1], parsed[0]

    def _attach_bucket(archive: dict, juan_seq: int, edition_short: str) -> bool:
        text_id = archive["text_id"]
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
            return False
        anchor = archive["anchor"]
        pos = compute_bucket_position(juan_doc, anchor["marker_id"], anchor["offset"])
        if pos is None:
            log.warning(
                "marker_id %s not found in %s edition=%s seq=%d",
                anchor["marker_id"], text_id, edition_short, juan_seq,
            )
            return False
        archive["bucket"], archive["bucket_offset"] = pos
        return True

    # Each (collection, converter) pair is walked once per DID.
    collections: list[tuple[str, Callable[..., dict | None], str]] = [
        (ANNOTATION_NSID, annotation_wire_to_archive, "annotation"),
        (LEGACY_ANNOTATION_NSID, annotation_wire_to_archive, "annotation"),
        (COMMENT_NSID, comment_wire_to_archive, "comment"),
        (TRANSLATION_NSID, translation_wire_to_archive, "translation"),
    ]

    for did in dids:
        for collection, converter, kind in collections:
            log.info("harvesting %s from %s", collection, did)
            try:
                records = fetch_did_records(
                    did, collection=collection, limit=limit_per_did,
                )
            except Exception as exc:
                # PDS may not host every collection; treat as empty.
                log.warning("listRecords(%s) failed for %s: %s", collection, did, exc)
                continue

            for wire, cid, uri in records:
                # The legacy NSID flows through the annotation converter; we
                # tag its provenance with the *new* NSID so harvested records
                # land in a uniform source_role namespace.
                kwargs: dict[str, Any] = {"did": did, "cid": cid, "uri": uri}
                if converter is annotation_wire_to_archive:
                    kwargs["nsid"] = ANNOTATION_NSID
                archive = converter(wire, **kwargs)
                if archive is None:
                    skipped += 1
                    continue

                if kind == "annotation":
                    juan_seq, edition_short = _resolve_juan(archive)
                    if juan_seq is None or edition_short is None:
                        log.warning(
                            "cannot parse edition/juan from marker_id %s",
                            (archive.get("anchor") or {}).get("marker_id"),
                        )
                        skipped += 1
                        continue
                    if not _attach_bucket(archive, juan_seq, edition_short):
                        skipped += 1
                        continue
                    incoming_annotations[(archive["text_id"], juan_seq)].append(archive)
                    harvested += 1
                elif kind == "comment":
                    if "anchor" in archive:
                        juan_seq, edition_short = _resolve_juan(archive)
                        if juan_seq is None or edition_short is None:
                            log.warning(
                                "cannot parse edition/juan from comment anchor"
                            )
                            skipped += 1
                            continue
                        # Bucket info is informational for comments; attach
                        # when possible but don't fail the whole record if not.
                        _attach_bucket(archive, juan_seq, edition_short)
                        incoming_comments[(archive["text_id"], juan_seq)].append(archive)
                    else:
                        incoming_comments[(archive["text_id"], None)].append(archive)
                    harvested += 1
                elif kind == "translation":
                    juan_seq, edition_short = _resolve_juan(archive)
                    if juan_seq is None or edition_short is None:
                        log.warning(
                            "cannot parse edition/juan from translation anchor"
                        )
                        skipped += 1
                        continue
                    _attach_bucket(archive, juan_seq, edition_short)
                    incoming_translations[(archive["text_id"], juan_seq)].append(archive)
                    harvested += 1

    replaced = 0
    files_touched = 0

    for (text_id, juan_seq), incoming in incoming_annotations.items():
        out_path = juan_archive_path(annotations_root, text_id, juan_seq)
        replaced += _write_jsonl_merged(out_path, incoming, sort=True, dry_run=dry_run)
        files_touched += 1

    for (text_id, juan_seq), incoming in incoming_comments.items():
        out_path = comment_archive_path(comments_root, text_id, juan_seq)
        replaced += _write_jsonl_merged(out_path, incoming, sort=False, dry_run=dry_run)
        files_touched += 1

    for (text_id, juan_seq), incoming in incoming_translations.items():
        out_path = translation_archive_path(translations_root, text_id, juan_seq)
        replaced += _write_jsonl_merged(out_path, incoming, sort=False, dry_run=dry_run)
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
    "annotation_wire_to_archive",
    "comment_wire_to_archive",
    "translation_wire_to_archive",
    "curation_wire_to_archive",
    "wire_to_archive",
    "compute_bucket_position",
    "juan_seq_from_marker_id",
    "parse_marker_id",
    "comment_archive_path",
    "translation_archive_path",
]
