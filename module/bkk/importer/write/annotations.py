"""Write annotations to a bkk-annotations archive root.

Replaces the in-bundle ``.ann.yaml`` sidecar. Layout::

    <root>/<text-id>/<text-id>_<juan>.ann.jsonl

One JSON object per line; see ``docs/bkk-annotations/README.md`` for the
record shape and provenance conventions.
"""

from __future__ import annotations

import hashlib
import json
import uuid as _uuid
from pathlib import Path

from ..ir import Annotation


# Legacy-attribution placeholder DID for the TLS-derived seed corpus. Not a
# real atproto registration; it's the constant we tag pre-Bluesky annotations
# with so the harvester can recognise (and skip) them later.
LEGACY_TLS_DID = "did:plc:bkk-tls-legacy"


def _synth_cid(record: dict) -> str:
    """Deterministic CID stand-in for seed records.

    Hash of the record with the ``cid`` field cleared, so re-running the
    seed migration produces identical CIDs on identical inputs.
    """
    skeleton = dict(record)
    skeleton["provenance"] = dict(skeleton["provenance"])
    skeleton["provenance"]["cid"] = ""
    payload = json.dumps(skeleton, sort_keys=True, ensure_ascii=False)
    return "synth-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _record_id(ann: Annotation) -> str:
    """Use the payload's existing id where present (TLS provides one);
    otherwise synthesise a deterministic UUID5 from the anchor."""
    pid = ann.payload.get("id")
    if isinstance(pid, str) and pid:
        return pid
    seed = f"{ann.marker_id}|{ann.offset}|{ann.length}"
    return "uuid-" + str(_uuid.uuid5(_uuid.NAMESPACE_URL, seed))


def annotation_to_record(
    ann: Annotation, *, text_id: str, edition: str,
) -> dict:
    """Build the on-disk record for one Annotation. CID is filled in last."""
    payload = {k: v for k, v in ann.payload.items() if k != "id"}
    created_at: str | None = None
    md = ann.payload.get("metadata")
    if isinstance(md, dict):
        created = md.get("created")
        if isinstance(created, str):
            created_at = created

    anchor: dict = {
        "marker_id": ann.marker_id,
        "offset": ann.offset,
        "length": ann.length,
    }
    if ann.end_marker_id is not None:
        anchor["end_marker_id"] = ann.end_marker_id
    if ann.end_length is not None:
        anchor["end_length"] = ann.end_length

    record: dict = {
        "id": _record_id(ann),
        "text_id": text_id,
        "edition": edition,
        "anchor": anchor,
        "payload": payload,
        "provenance": {
            "did": LEGACY_TLS_DID,
            "cid": "",
            "created_at": created_at,
            "source_role": ann.source_role,
            "supersedes": None,
        },
        "curation_state": "accepted",
    }
    if ann.tls_seg_id is not None:
        record["provenance"]["tls"] = {
            "seg_id": ann.tls_seg_id,
            "pos": ann.tls_pos,
        }
    if ann.provenance:
        record["provenance"]["source_attribution"] = ann.provenance

    record["provenance"]["cid"] = _synth_cid(record)
    return record


_BUCKET_PRIORITY = {"front": 0, "body": 1, "back": 2}


def bucket_sort_key(record: dict) -> tuple:
    """Stable sort key for archive records: (bucket priority, offset, id)."""
    return (
        _BUCKET_PRIORITY.get(record.get("bucket"), 99),
        record.get("bucket_offset", 0),
        record.get("id", ""),
    )


def juan_archive_path(annotations_root: Path, text_id: str, juan_seq: int) -> Path:
    """Canonical archive JSONL path for ``(text_id, juan_seq)``."""
    return annotations_root / text_id / f"{text_id}_{juan_seq:03d}.ann.jsonl"


def write_records_jsonl(out_path: Path, records: list[dict], *, sort: bool = True) -> Path:
    """Write archive records to ``out_path`` as one JSON object per line."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if sort:
        records = sorted(records, key=bucket_sort_key)
    with out_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True))
            f.write("\n")
    return out_path


def write_juan_annotations(
    annotations: list[tuple[Annotation, int, str]],
    *,
    text_id: str,
    edition: str,
    juan_seq: int,
    annotations_root: Path,
) -> Path | None:
    """Write one juan's annotations to its JSONL file.

    ``annotations`` is the list of (Annotation, bucket_offset, bucket) tuples
    produced by the writer's bucket loop. Returns the written path, or None
    if the input list is empty.
    """
    if not annotations:
        return None
    out_path = juan_archive_path(annotations_root, text_id, juan_seq)

    records: list[dict] = []
    for ann, bucket_offset, bucket in annotations:
        record = annotation_to_record(ann, text_id=text_id, edition=edition)
        record["bucket"] = bucket
        record["bucket_offset"] = bucket_offset
        records.append(record)

    return write_records_jsonl(out_path, records, sort=True)
