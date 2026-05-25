"""Section D: annotation file constraints."""

from __future__ import annotations

import re
import uuid

from ..context import ValidationContext, LoadedFile
from bkk.marker_assets import effective_markers_for_bucket

VALID_BUCKETS = ("front", "body", "back")
ISO_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+\-]\d{2}:?\d{2})?$"
)


def run(ctx: ValidationContext) -> None:
    if not isinstance(ctx.master_manifest.data, dict):
        return
    assets = ctx.master_manifest.data.get("assets") or {}
    declared = {
        e["seq"]: e
        for e in (assets.get("annotations") or [])
        if isinstance(e, dict) and isinstance(e.get("seq"), int)
    }
    for seq, lf in ctx.annotations.items():
        _check_ann_file(ctx, lf, seq=seq, decl=declared.get(seq))


def _check_ann_file(
    ctx: ValidationContext, lf: LoadedFile, *, seq: int, decl: dict | None,
) -> None:
    if not lf.exists:
        return
    if lf.parse_error is not None:
        ctx.report.add(
            "MANIFEST_PARSE", "error", lf.rel,
            f"YAML parse error: {lf.parse_error}",
        )
        return
    if not isinstance(lf.data, dict):
        ctx.report.add(
            "ANN_REQUIRED_KEYS", "error", lf.rel,
            "annotation file top level is not a mapping",
        )
        return
    data = lf.data
    for key in ("text_id", "juan", "edition", "annotations"):
        if key not in data:
            ctx.report.add(
                "ANN_REQUIRED_KEYS", "error", lf.rel,
                f"missing required key '{key}'",
            )

    # text_id matches bundle.
    if data.get("text_id") not in (None, ctx.text_id):
        ctx.report.add(
            "ANN_FILE_AGREES_WITH_FILENAME", "error", lf.rel,
            f"text_id '{data.get('text_id')}' does not match bundle '{ctx.text_id}'",
        )
    # juan agrees with manifest seq.
    juan_field = data.get("juan")
    expected_juan = f"{seq:03d}"
    if isinstance(juan_field, str) and juan_field != expected_juan:
        ctx.report.add(
            "ANN_FILE_AGREES_WITH_FILENAME", "error", lf.rel,
            f"juan field '{juan_field}' does not match manifest seq {seq} (expected '{expected_juan}')",
        )
    # edition is one of the on-disk editions.
    edition = data.get("edition")
    if isinstance(edition, str) and edition not in ctx.editions:
        ctx.report.add(
            "ANN_FILE_AGREES_WITH_FILENAME", "error", lf.rel,
            f"edition '{edition}' has no editions/{edition}/ directory",
        )

    # Look up the juan markers we need to resolve seg_ids.
    juan_lf = ctx.master_juans.get(seq)
    marker_lf = ctx.marker_assets.get(seq)
    marker_asset = (
        marker_lf.data
        if marker_lf is not None and isinstance(marker_lf.data, dict)
        else None
    )
    seg_offsets, seg_buckets = _build_seg_index(juan_lf, marker_asset)

    annotations = data.get("annotations") or []
    if not isinstance(annotations, list):
        return
    for i, ann in enumerate(annotations):
        if not isinstance(ann, dict):
            ctx.report.add(
                "ANN_REQUIRED_KEYS", "error", lf.rel,
                f"annotations[{i}] is not a mapping",
            )
            continue
        seg_id = ann.get("seg_id")
        if not isinstance(seg_id, str):
            ctx.report.add(
                "ANN_REQUIRED_KEYS", "error", lf.rel,
                f"annotations[{i}] missing seg_id",
            )
        elif seg_offsets is not None and seg_id not in seg_offsets:
            ctx.report.add(
                "ANN_SEG_ID_RESOLVES", "error", lf.rel,
                f"annotations[{i}] seg_id '{seg_id}' not found as a tls:seg/tls:head marker in juan seq={seq}",
            )

        # bucket is optional; if present must be valid.
        bucket = ann.get("bucket")
        if bucket is not None and bucket not in VALID_BUCKETS:
            ctx.report.add(
                "ANN_BUCKET_VALID", "error", lf.rel,
                f"annotations[{i}] bucket '{bucket}' not one of {VALID_BUCKETS}",
            )

        # offset bounds (if bucket is known or inferable from seg).
        offset = ann.get("offset")
        if isinstance(offset, int):
            inferred_bucket = bucket or (seg_buckets.get(seg_id) if isinstance(seg_id, str) else None)
            if inferred_bucket and isinstance(juan_lf, LoadedFile) and isinstance(juan_lf.data, dict):
                bucket_obj = juan_lf.data.get(inferred_bucket)
                if isinstance(bucket_obj, dict):
                    text = bucket_obj.get("text", "")
                    if isinstance(text, str) and not (0 <= offset <= len(text)):
                        ctx.report.add(
                            "ANN_OFFSET_BOUNDS", "error", lf.rel,
                            f"annotations[{i}] offset {offset} out of range [0, {len(text)}] for bucket '{inferred_bucket}'",
                        )

        pos = ann.get("pos")
        if pos is not None and not (isinstance(pos, int) and pos >= 1):
            ctx.report.add(
                "ANN_POS", "error", lf.rel,
                f"annotations[{i}] pos must be null or a positive integer, got {pos!r}",
            )

        # Soft checks (warnings only).
        ann_id = ann.get("id")
        if isinstance(ann_id, str) and ann_id.startswith("uuid-"):
            try:
                uuid.UUID(ann_id[len("uuid-"):])
            except ValueError:
                ctx.report.add(
                    "ANN_REQUIRED_KEYS", "warning", lf.rel,
                    f"annotations[{i}] id '{ann_id}' is not a valid UUID",
                )
        md = ann.get("metadata")
        if isinstance(md, dict):
            created = md.get("created")
            if isinstance(created, str) and not ISO_TIMESTAMP_RE.match(created):
                ctx.report.add(
                    "ANN_REQUIRED_KEYS", "warning", lf.rel,
                    f"annotations[{i}].metadata.created '{created}' is not an ISO timestamp",
                )

    if decl is not None and "filename" in decl and isinstance(decl["filename"], str):
        if not lf.path.name == decl["filename"]:
            ctx.report.add(
                "ANN_FILE_AGREES_WITH_FILENAME", "error", lf.rel,
                f"file name does not match manifest assets.annotations[].filename '{decl['filename']}'",
            )


def _build_seg_index(
    juan_lf: LoadedFile | None,
    marker_asset: dict | None,
) -> tuple[dict[str, int] | None, dict[str, str]]:
    """Return (seg_offsets_by_id, seg_bucket_by_id).

    First element is None when we cannot inspect the juan (so seg_id checks
    silently skip rather than false-positive).
    """
    if juan_lf is None or not juan_lf.exists or not isinstance(juan_lf.data, dict):
        return None, {}
    offsets: dict[str, int] = {}
    buckets: dict[str, str] = {}
    for bucket_name in VALID_BUCKETS:
        bucket = juan_lf.data.get(bucket_name)
        if not isinstance(bucket, dict):
            continue
        for m in effective_markers_for_bucket(juan_lf.data, bucket_name, marker_asset):
            if not isinstance(m, dict):
                continue
            mid = m.get("id")
            if (
                isinstance(mid, str) and mid
                and m.get("type") in ("tls:seg", "tls:head")
            ):
                offsets[mid] = m.get("offset", 0) or 0
                buckets[mid] = bucket_name
    return offsets, buckets
