"""Validate and repair archive annotation anchors against the corpus.

Each archive record stores ``anchor.{marker_id, offset, length}`` plus a
cached ``bucket`` / ``bucket_offset`` resolved at write time. The legacy
TLS importer occasionally derives the wrong ``offset`` (counted against
the un-normalised source text, then carried into the BKK address space),
leaving the cached ``bucket_offset`` pointing a few chars away from the
graph the annotation describes.

This module:

* resolves each record's anchor against the master juan's body text,
* compares the body at the resolved position to the annotation's
  ``payload.form.orth`` graph (when present),
* for mismatches, searches a small window for ``orth`` and proposes a
  shifted offset if it finds a unique match.

The repair writes back to the archive JSONL; the source bundles are not
touched. Records that lack ``orth`` or have ambiguous nearby matches are
reported but never auto-changed.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

import yaml

from bkk.marker_assets import (
    effective_markers_for_bucket,
    load_marker_asset,
    VALID_BUCKETS,
)

log = logging.getLogger("bkk.annotations.validate")

DEFAULT_SEARCH_WINDOW = 8


@dataclass
class Finding:
    text_id: str
    juan_seq: int
    annotation_id: str
    marker_id: str
    status: str          # ok | mismatch_fixable | mismatch_ambiguous | mismatch_unfindable
                         # | no_orth | missing_marker | missing_juan | bad_record
    bucket: str | None = None
    bucket_offset: int | None = None
    anchor_offset: int | None = None
    orth: str | None = None
    found_at_offset: str | None = None
    proposed_bucket_offset: int | None = None
    delta: int | None = None
    detail: str | None = None


@dataclass
class _JuanCache:
    """Per-(text_id, juan_seq) cached body text + marker id → (bucket, offset)."""
    bodies: dict[str, str]              # bucket → text
    markers: dict[str, tuple[str, int]] # marker_id → (bucket, offset)


def _bundle_dir(corpus_root: Path, text_id: str) -> Path | None:
    candidates = list(corpus_root.glob(f"*/{text_id}"))
    return candidates[0] if candidates else None


def _load_juan_cache(corpus_root: Path, text_id: str, juan_seq: int) -> _JuanCache | None:
    """Load the master juan + marker asset and build a fast lookup index."""
    bundle = _bundle_dir(corpus_root, text_id)
    if bundle is None:
        return None
    juan_path = bundle / f"{text_id}_{juan_seq:03d}.yaml"
    if not juan_path.exists():
        return None
    try:
        juan_doc = yaml.safe_load(juan_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        log.warning("failed to load %s: %s", juan_path, exc)
        return None
    if not isinstance(juan_doc, dict):
        return None

    manifest_path = bundle / f"{text_id}.manifest.yaml"
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        try:
            loaded = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
        except (OSError, yaml.YAMLError) as exc:
            log.warning("failed to load %s: %s", manifest_path, exc)

    marker_asset = load_marker_asset(bundle, manifest, juan_seq)

    bodies: dict[str, str] = {}
    markers: dict[str, tuple[str, int]] = {}
    for bucket_name in VALID_BUCKETS:
        bucket = juan_doc.get(bucket_name)
        if not isinstance(bucket, dict):
            continue
        text = bucket.get("text")
        bodies[bucket_name] = text if isinstance(text, str) else ""
        for m in effective_markers_for_bucket(juan_doc, bucket_name, marker_asset):
            mid = m.get("id")
            off = m.get("offset")
            if isinstance(mid, str) and isinstance(off, int):
                markers.setdefault(mid, (bucket_name, off))

    return _JuanCache(bodies=bodies, markers=markers)


def _orth_from(record: dict) -> str | None:
    payload = record.get("payload") or {}
    form = payload.get("form") if isinstance(payload, dict) else None
    if not isinstance(form, dict):
        return None
    orth = form.get("orth")
    return orth if isinstance(orth, str) and orth else None


def _find_unique(haystack: str, needle: str, center: int, window: int) -> int | None:
    """Return absolute index of ``needle`` in haystack if exactly one match
    lies within ``[center - window, center + window]`` (inclusive of start).
    Otherwise return None.
    """
    if not needle or not haystack:
        return None
    lo = max(0, center - window)
    hi = min(len(haystack), center + window + len(needle))
    region = haystack[lo:hi]
    hits: list[int] = []
    pos = 0
    while True:
        i = region.find(needle, pos)
        if i < 0:
            break
        hits.append(lo + i)
        pos = i + 1
    if len(hits) != 1:
        return None
    return hits[0]


def _classify(
    record: dict, cache: _JuanCache, *, window: int,
) -> tuple[Finding, dict | None]:
    """Return (finding, proposed_record_or_None).

    ``proposed_record`` is a *new* dict with adjusted ``anchor.offset`` and
    ``bucket_offset`` when an unambiguous repair was found; None otherwise.
    """
    text_id = record.get("text_id") or ""
    ann_id = record.get("id") or ""
    anchor = record.get("anchor") or {}
    marker_id = anchor.get("marker_id") or ""
    anchor_offset = anchor.get("offset")
    bucket = record.get("bucket")
    bucket_offset = record.get("bucket_offset")

    base = Finding(
        text_id=text_id,
        juan_seq=record.get("_juan_seq", 0),
        annotation_id=ann_id,
        marker_id=marker_id,
        status="ok",
        bucket=bucket if isinstance(bucket, str) else None,
        bucket_offset=bucket_offset if isinstance(bucket_offset, int) else None,
        anchor_offset=anchor_offset if isinstance(anchor_offset, int) else None,
    )

    if not isinstance(marker_id, str) or not marker_id:
        base.status = "bad_record"
        base.detail = "missing marker_id"
        return base, None
    if not isinstance(anchor_offset, int) or not isinstance(bucket_offset, int):
        base.status = "bad_record"
        base.detail = "missing anchor.offset or bucket_offset"
        return base, None

    pos = cache.markers.get(marker_id)
    if pos is None:
        base.status = "missing_marker"
        base.detail = f"marker_id not found in juan: {marker_id}"
        return base, None

    bucket_name, marker_offset = pos
    body = cache.bodies.get(bucket_name, "")
    expected_bo = marker_offset + anchor_offset

    orth = _orth_from(record)
    base.orth = orth

    # Inconsistency between cached bucket_offset and resolved marker+offset.
    cached_drift = bucket_offset - expected_bo

    if not orth:
        # Without orth we can only check cache consistency; report a soft state.
        if cached_drift == 0:
            base.status = "no_orth"
            base.detail = "no orth to verify against body"
        else:
            base.status = "no_orth"
            base.detail = (
                f"no orth; cached bucket_offset drifts from marker+offset by {cached_drift}"
            )
        return base, None

    actual = body[bucket_offset:bucket_offset + len(orth)]
    base.found_at_offset = actual
    if actual == orth:
        base.status = "ok"
        return base, None

    # Mismatch: try to find a unique match near the resolved position.
    hit = _find_unique(body, orth, expected_bo, window)
    if hit is None:
        # widen once before giving up, to catch the off-by-N cases reliably
        hit = _find_unique(body, orth, expected_bo, max(window, 16))
    if hit is None:
        # Check if the graph occurs at all in the juan body
        if orth not in body:
            base.status = "mismatch_unfindable"
            base.detail = "orth not present in juan body"
        else:
            base.status = "mismatch_ambiguous"
            base.detail = "multiple or no candidate offsets in search window"
        return base, None

    delta = hit - bucket_offset
    base.status = "mismatch_fixable"
    base.proposed_bucket_offset = hit
    base.delta = delta
    base.detail = f"shift bucket_offset and anchor.offset by {delta}"

    new_record = dict(record)
    new_anchor = dict(anchor)
    new_anchor["offset"] = anchor_offset + delta
    new_record["anchor"] = new_anchor
    new_record["bucket_offset"] = hit
    return base, new_record


def iter_jsonl_files(
    annotations_root: Path, text_id_filter: str | None = None,
) -> Iterator[tuple[Path, str, int]]:
    """Yield (path, text_id, juan_seq) for archive JSONL files."""
    for path in sorted(annotations_root.glob("*/*.ann.jsonl")):
        text_id = path.parent.name
        if text_id_filter and text_id != text_id_filter:
            continue
        stem = path.name.removesuffix(".ann.jsonl")
        try:
            seq = int(stem.rsplit("_", 1)[-1])
        except ValueError:
            continue
        yield path, text_id, seq


def _read_records(path: Path) -> list[dict]:
    out: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                rec = json.loads(s)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def _write_records(path: Path, records: list[dict]) -> None:
    """Rewrite ``path`` preserving the existing on-disk sort (key order from writer)."""
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True))
            f.write("\n")


@dataclass
class RunSummary:
    files_scanned: int = 0
    files_changed: int = 0
    records_total: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    def bump(self, status: str) -> None:
        self.by_status[status] = self.by_status.get(status, 0) + 1


def _consensus_delta(findings: list[Finding]) -> int | None:
    """Return the single shared delta from ``mismatch_fixable`` findings, else None.

    Used per marker_id to resolve ``mismatch_ambiguous`` records where the
    orth appears at multiple positions but its sibling annotations under the
    same marker all agree on a uniform shift.
    """
    deltas = {f.delta for f in findings if f.status == "mismatch_fixable" and f.delta is not None}
    if len(deltas) == 1:
        return next(iter(deltas))
    return None


def _apply_consensus(
    findings: list[Finding],
    records_by_id: dict[str, dict],
    cache: _JuanCache,
) -> dict[str, dict]:
    """Promote ambiguous findings to fixable using a per-marker consensus delta.

    Returns a dict of ``annotation_id -> proposed_record`` for records that
    became fixable via consensus. Updates the corresponding Finding in place.
    """
    by_marker: dict[str, list[Finding]] = {}
    for f in findings:
        if f.marker_id:
            by_marker.setdefault(f.marker_id, []).append(f)

    promoted: dict[str, dict] = {}
    for group in by_marker.values():
        delta = _consensus_delta(group)
        if delta is None:
            continue
        bucket_name = next((f.bucket for f in group if f.bucket), None)
        if not bucket_name:
            continue
        body = cache.bodies.get(bucket_name, "")
        for f in group:
            if f.status != "mismatch_ambiguous":
                continue
            if f.bucket_offset is None or f.anchor_offset is None or not f.orth:
                continue
            new_bo = f.bucket_offset + delta
            if new_bo < 0 or new_bo + len(f.orth) > len(body):
                continue
            if body[new_bo:new_bo + len(f.orth)] != f.orth:
                continue
            rec = records_by_id.get(f.annotation_id)
            if rec is None:
                continue
            new_rec = dict(rec)
            new_anchor = dict(rec.get("anchor") or {})
            new_anchor["offset"] = f.anchor_offset + delta
            new_rec["anchor"] = new_anchor
            new_rec["bucket_offset"] = new_bo
            promoted[f.annotation_id] = new_rec
            f.status = "mismatch_fixable"
            f.proposed_bucket_offset = new_bo
            f.delta = delta
            f.detail = f"resolved by per-marker consensus delta {delta:+d}"
    return promoted


def run(
    annotations_root: Path,
    corpus_root: Path,
    *,
    text_id_filter: str | None = None,
    write: bool = False,
    window: int = DEFAULT_SEARCH_WINDOW,
    collect_findings: bool = True,
    consensus: bool = True,
    progress: Callable[[str], None] | None = None,
) -> RunSummary:
    """Walk the archive, classify each record, optionally repair fixable ones.

    With ``write=False`` (default) the archive is not modified — useful for
    plain validation. With ``write=True``, files containing at least one
    ``mismatch_fixable`` record are rewritten with the corrected offsets.

    ``consensus=True`` enables a second pass per juan: ``mismatch_ambiguous``
    records inherit the shift from their marker's unambiguous siblings when
    that shift produces a unique orth match.

    ``progress`` is called once per JSONL file with a short status line. The
    CLI wires this to a stderr printer so long runs aren't silent.
    """
    summary = RunSummary()
    juan_cache: dict[tuple[str, int], _JuanCache | None] = {}

    files = list(iter_jsonl_files(annotations_root, text_id_filter))
    total_files = len(files)

    for i, (path, text_id, juan_seq) in enumerate(files, 1):
        summary.files_scanned += 1
        key = (text_id, juan_seq)
        if key not in juan_cache:
            juan_cache[key] = _load_juan_cache(corpus_root, text_id, juan_seq)
        cache = juan_cache[key]

        records = _read_records(path)
        summary.records_total += len(records)

        if cache is None:
            if collect_findings:
                summary.findings.append(Finding(
                    text_id=text_id, juan_seq=juan_seq,
                    annotation_id="", marker_id="",
                    status="missing_juan",
                    detail=f"juan not found under corpus for {text_id}_{juan_seq:03d}",
                ))
            summary.bump("missing_juan")
            continue

        per_juan_findings: list[Finding] = []
        proposals: dict[str, dict] = {}
        records_by_id: dict[str, dict] = {}
        for rec in records:
            rec["_juan_seq"] = juan_seq
            finding, proposed = _classify(rec, cache, window=window)
            finding.juan_seq = juan_seq
            per_juan_findings.append(finding)
            ann_id = rec.get("id") or ""
            if isinstance(ann_id, str) and ann_id:
                records_by_id[ann_id] = rec
                if proposed is not None:
                    proposals[ann_id] = proposed

        if consensus:
            proposals.update(_apply_consensus(per_juan_findings, records_by_id, cache))

        for f in per_juan_findings:
            summary.bump(f.status)
            if collect_findings:
                summary.findings.append(f)

        new_records: list[dict] = []
        file_changed = False
        for rec in records:
            ann_id = rec.get("id") or ""
            if write and isinstance(ann_id, str) and ann_id in proposals:
                out = dict(proposals[ann_id])
                file_changed = True
            else:
                out = dict(rec)
            out.pop("_juan_seq", None)
            new_records.append(out)

        if write and file_changed:
            _write_records(path, new_records)
            summary.files_changed += 1

        if progress is not None:
            tag = "rewrote" if (write and file_changed) else "scanned"
            fix = sum(1 for f in per_juan_findings if f.status == "mismatch_fixable")
            amb = sum(1 for f in per_juan_findings if f.status == "mismatch_ambiguous")
            unf = sum(1 for f in per_juan_findings if f.status == "mismatch_unfindable")
            progress(
                f"[{i}/{total_files}] {tag} {text_id}_{juan_seq:03d}: "
                f"{len(records)} records, {fix} fixable, {amb} ambiguous, {unf} unfindable"
            )

    return summary


def format_text_summary(summary: RunSummary, *, max_findings: int = 25) -> str:
    """Human-readable digest of a RunSummary for the CLI."""
    lines = [
        f"scanned {summary.files_scanned} file(s), "
        f"{summary.records_total} record(s); "
        f"changed {summary.files_changed} file(s)",
        "by status:",
    ]
    for status in sorted(summary.by_status):
        lines.append(f"  {status}: {summary.by_status[status]}")
    interesting = [
        f for f in summary.findings
        if f.status not in ("ok", "no_orth")
    ]
    if interesting:
        lines.append(f"first {min(max_findings, len(interesting))} of "
                     f"{len(interesting)} non-ok finding(s):")
        for f in interesting[:max_findings]:
            extra = []
            if f.orth:
                extra.append(f"orth={f.orth!r}")
            if f.found_at_offset is not None and f.found_at_offset != f.orth:
                extra.append(f"found={f.found_at_offset!r}")
            if f.delta is not None:
                extra.append(f"delta={f.delta:+d}")
            if f.detail:
                extra.append(f.detail)
            head = (
                f"  [{f.status}] {f.text_id}_{f.juan_seq:03d} "
                f"{f.marker_id}+{f.anchor_offset} (bo={f.bucket_offset}) "
                f"id={f.annotation_id}"
            )
            lines.append(head)
            if extra:
                lines.append("    " + " | ".join(extra))
    return "\n".join(lines)


__all__ = [
    "Finding",
    "RunSummary",
    "run",
    "iter_jsonl_files",
    "format_text_summary",
    "DEFAULT_SEARCH_WINDOW",
]
