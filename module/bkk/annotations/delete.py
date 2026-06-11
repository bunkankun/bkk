"""Hard-delete a contribution: bsky deleteRecord + archive JSONL rewrite.

Used by:

* ``bkk annotations delete`` CLI (operator removing duplicates / stale records).
* The Jetstream contributions feed, which propagates remote deletes to disk
  so that an author who deletes from another client doesn't leave a phantom
  record in the archive.

Hard delete ordering is **bsky first, archive second** so that a failed bsky
delete can't be hidden by a successful archive rewrite (the next harvest
would resurrect the on-disk copy from the still-living bsky record).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from bkk.importer.write.annotations import write_records_jsonl
from bkk.serve.routers.annotations import read_raw_records


KIND_ANNOTATION = "annotation"
KIND_COMMENT = "comment"
KIND_TRANSLATION = "translation"

_LAYOUT: tuple[tuple[str, str], ...] = (
    (KIND_ANNOTATION, ".ann.jsonl"),
    (KIND_COMMENT, ".cmt.jsonl"),
    (KIND_TRANSLATION, ".tr.jsonl"),
)


@dataclass
class ArchiveHit:
    """A record located in the on-disk archive."""

    path: Path
    record: dict[str, Any]
    kind: str  # one of KIND_*


def _matches(
    record: dict[str, Any],
    *,
    uri: str | None,
    cid: str | None,
    record_id: str | None,
) -> bool:
    prov = record.get("provenance")
    if not isinstance(prov, dict):
        prov = {}
    if uri is not None and prov.get("uri") == uri:
        return True
    if cid is not None and prov.get("cid") == cid:
        return True
    if record_id is not None and record.get("id") == record_id:
        return True
    return False


def _iter_archive_files(root: Path, *, suffix: str) -> Iterable[Path]:
    if not root.is_dir():
        return
    for text_dir in sorted(root.iterdir()):
        if not text_dir.is_dir():
            continue
        for path in sorted(text_dir.glob(f"*{suffix}")):
            yield path


def _roots_by_kind(
    annotations_root: Path | None,
    comments_root: Path | None,
    translations_root: Path | None,
) -> dict[str, Path]:
    return {
        k: v for k, v in (
            (KIND_ANNOTATION, annotations_root),
            (KIND_COMMENT, comments_root),
            (KIND_TRANSLATION, translations_root),
        ) if v is not None
    }


def locate(
    *,
    uri: str | None = None,
    cid: str | None = None,
    record_id: str | None = None,
    annotations_root: Path | None = None,
    comments_root: Path | None = None,
    translations_root: Path | None = None,
) -> ArchiveHit | None:
    """Scan archive roots for a record matching any provided identifier."""
    if uri is None and cid is None and record_id is None:
        raise ValueError("must provide at least one of uri/cid/record_id")
    roots = _roots_by_kind(annotations_root, comments_root, translations_root)
    for kind, suffix in _LAYOUT:
        root = roots.get(kind)
        if root is None:
            continue
        for path in _iter_archive_files(root, suffix=suffix):
            for record in read_raw_records(path):
                if _matches(record, uri=uri, cid=cid, record_id=record_id):
                    return ArchiveHit(path=path, record=record, kind=kind)
    return None


def archive_remove(hit: ArchiveHit) -> bool:
    """Rewrite ``hit.path`` omitting the matching record.

    Returns True when the file content changed.
    """
    target_uri = (hit.record.get("provenance") or {}).get("uri")
    target_cid = (hit.record.get("provenance") or {}).get("cid")
    target_id = hit.record.get("id")
    records = list(read_raw_records(hit.path))
    survivors = [
        r for r in records
        if not _matches(r, uri=target_uri, cid=target_cid, record_id=target_id)
    ]
    if len(survivors) == len(records):
        return False
    write_records_jsonl(hit.path, survivors, sort=(hit.kind == KIND_ANNOTATION))
    return True


def find_rejected(
    *,
    annotations_root: Path | None = None,
    comments_root: Path | None = None,
    translations_root: Path | None = None,
) -> list[ArchiveHit]:
    """Return all archive rows with ``curation_state == "rejected"``.

    Used by the bulk ``bkk annotations delete --rejected`` flow. The
    on-disk ``curation_state`` is stamped by ``PATCH /annotations/curation-state``
    (and by the harvester's resolver pass), so this faithfully reflects
    what the UI / curation feed currently treats as rejected — no bsky
    round-trip needed.
    """
    hits: list[ArchiveHit] = []
    roots = _roots_by_kind(annotations_root, comments_root, translations_root)
    for kind, suffix in _LAYOUT:
        root = roots.get(kind)
        if root is None:
            continue
        for path in _iter_archive_files(root, suffix=suffix):
            for record in read_raw_records(path):
                if record.get("curation_state") == "rejected":
                    hits.append(ArchiveHit(path=path, record=record, kind=kind))
    return hits


def archive_remove_by_uri(
    uri: str,
    *,
    annotations_root: Path | None = None,
    comments_root: Path | None = None,
    translations_root: Path | None = None,
) -> ArchiveHit | None:
    """Locate and remove a record by ``provenance.uri``. Returns the hit or None.

    Convenience for the Jetstream delete-propagation path.
    """
    hit = locate(
        uri=uri,
        annotations_root=annotations_root,
        comments_root=comments_root,
        translations_root=translations_root,
    )
    if hit is None:
        return None
    return hit if archive_remove(hit) else None


def is_bsky_native(record: dict[str, Any]) -> bool:
    """Heuristic: record originates on bsky (has at-URI and non-synth CID)."""
    prov = record.get("provenance")
    if not isinstance(prov, dict):
        return False
    uri = prov.get("uri")
    cid = prov.get("cid")
    if not isinstance(uri, str) or not uri.startswith("at://"):
        return False
    if isinstance(cid, str) and cid.startswith("synth-"):
        return False
    return True


def parse_at_uri(uri: str) -> tuple[str, str, str]:
    """Split ``at://did/collection/rkey`` into its three components."""
    if not uri.startswith("at://"):
        raise ValueError(f"not an at-uri: {uri}")
    rest = uri[len("at://"):]
    parts = rest.split("/", 2)
    if len(parts) != 3 or not all(parts):
        raise ValueError(f"malformed at-uri: {uri}")
    return parts[0], parts[1], parts[2]


__all__ = [
    "ArchiveHit",
    "KIND_ANNOTATION",
    "KIND_COMMENT",
    "KIND_TRANSLATION",
    "locate",
    "archive_remove",
    "archive_remove_by_uri",
    "find_rejected",
    "is_bsky_native",
    "parse_at_uri",
]
