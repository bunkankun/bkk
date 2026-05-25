"""Section B: manifest field constraints (master and edition manifests)."""

from __future__ import annotations

import re

from ..context import ValidationContext, LoadedFile
from bkk.marker_assets import (
    VALID_BUCKETS,
    effective_markers_for_bucket,
    marker_asset_hash,
)

HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
KNOWN_CHARSET_IDS = {"bkk:charset/cjk-v1"}
KNOWN_ENCODING_IDS = {"bkk:encoding/kanripo-pua-v1"}
REQUIRED_MASTER_KEYS = (
    "canonical_identifier",
    "canonical_location",
    "canonical_set",
    "assets",
    "table_of_contents",
    "metadata",
    "hash",
)
REQUIRED_EDITION_KEYS = REQUIRED_MASTER_KEYS  # same surface


def run(ctx: ValidationContext) -> None:
    _check_manifest(ctx, ctx.master_manifest, kind="master", edition_short=None)
    for short, ed in ctx.editions.items():
        if ed.manifest.exists and ed.manifest.parse_error is None and isinstance(
            ed.manifest.data, dict
        ):
            _check_manifest(ctx, ed.manifest, kind="edition", edition_short=short)


def _check_manifest(
    ctx: ValidationContext,
    lf: LoadedFile,
    *,
    kind: str,
    edition_short: str | None,
) -> None:
    if not lf.exists or lf.parse_error is not None or not isinstance(lf.data, dict):
        return  # already flagged by filesystem rules
    data = lf.data

    # Required keys.
    for key in REQUIRED_MASTER_KEYS if kind == "master" else REQUIRED_EDITION_KEYS:
        if key not in data:
            ctx.report.add(
                "MANIFEST_REQUIRED_KEYS", "error", lf.rel,
                f"missing required key '{key}'",
            )

    _check_canonical_identifier(ctx, lf, kind, edition_short)
    _check_canonical_location(ctx, lf)
    _check_charset_encoding(ctx, lf)
    _check_hash_format(ctx, lf, "hash", data.get("hash"))
    _check_assets_parts(ctx, lf, edition_short)
    _check_assets_markers(ctx, lf, edition_short)
    _check_toc(ctx, lf, edition_short)
    _check_metadata_edition(ctx, lf, kind, edition_short)


def _check_canonical_identifier(
    ctx: ValidationContext, lf: LoadedFile, kind: str, edition_short: str | None,
) -> None:
    cid = lf.data.get("canonical_identifier")
    if not isinstance(cid, str):
        return
    parts = cid.split("/")
    # Expected: bkk:krp/<text-id>/v1 (master) or bkk:krp/<text-id>/<short>/v1 (edition).
    if kind == "master":
        ok = len(parts) == 3 and parts[0] == "bkk:krp" and parts[1] == ctx.text_id and parts[2] == "v1"
    else:
        ok = (
            len(parts) == 4
            and parts[0] == "bkk:krp"
            and parts[1] == ctx.text_id
            and parts[2] == edition_short
            and parts[3] == "v1"
        )
    if not ok:
        ctx.report.add(
            "CANONICAL_IDENTIFIER_FORMAT", "error", lf.rel,
            f"unexpected canonical_identifier '{cid}'",
        )


def _check_canonical_location(ctx: ValidationContext, lf: LoadedFile) -> None:
    cid = lf.data.get("canonical_identifier")
    loc = lf.data.get("canonical_location")
    if not isinstance(cid, str) or not isinstance(loc, str):
        return
    suffix = cid.removeprefix("bkk:krp/")
    expected = f"https://kanripo.org/bkk/{suffix}"
    if loc != expected:
        ctx.report.add(
            "CANONICAL_LOCATION_MATCHES", "error", lf.rel,
            f"canonical_location '{loc}' does not match canonical_identifier (expected '{expected}')",
        )


def _check_charset_encoding(ctx: ValidationContext, lf: LoadedFile) -> None:
    cs = lf.data.get("canonical_set")
    if isinstance(cs, dict):
        ident = cs.get("identifier")
        if isinstance(ident, str) and ident not in KNOWN_CHARSET_IDS:
            ctx.report.add(
                "CANONICAL_SET_KNOWN", "warning", lf.rel,
                f"canonical_set.identifier '{ident}' is not in the known set {sorted(KNOWN_CHARSET_IDS)}",
            )
        _check_hash_format(ctx, lf, "canonical_set.hash", cs.get("hash"))
    enc = lf.data.get("entity_encoding")
    if isinstance(enc, dict):
        ident = enc.get("identifier")
        if isinstance(ident, str) and ident not in KNOWN_ENCODING_IDS:
            ctx.report.add(
                "CANONICAL_SET_KNOWN", "warning", lf.rel,
                f"entity_encoding.identifier '{ident}' is not in the known set {sorted(KNOWN_ENCODING_IDS)}",
            )
        _check_hash_format(ctx, lf, "entity_encoding.hash", enc.get("hash"))


def _check_hash_format(
    ctx: ValidationContext, lf: LoadedFile, field: str, value: object,
) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not HASH_RE.match(value):
        ctx.report.add(
            "HASH_FORMAT", "error", lf.rel,
            f"{field}: '{value}' does not match sha256:<64-hex>",
        )


def _check_assets_parts(
    ctx: ValidationContext, lf: LoadedFile, edition_short: str | None,
) -> None:
    assets = lf.data.get("assets")
    if not isinstance(assets, dict):
        return
    parts = assets.get("parts")
    if not isinstance(parts, list):
        return

    seen: dict[int, int] = {}
    last_seq: int | None = None
    for i, part in enumerate(parts):
        if not isinstance(part, dict):
            ctx.report.add(
                "MANIFEST_REQUIRED_KEYS", "error", lf.rel,
                f"assets.parts[{i}] is not a mapping",
            )
            continue
        seq = part.get("seq")
        if not isinstance(seq, int):
            ctx.report.add(
                "MANIFEST_REQUIRED_KEYS", "error", lf.rel,
                f"assets.parts[{i}].seq is missing or not an int",
            )
        else:
            if seq in seen:
                ctx.report.add(
                    "ASSETS_PARTS_SEQ_UNIQUE", "error", lf.rel,
                    f"duplicate seq {seq} at assets.parts[{i}] (also at [{seen[seq]}])",
                )
            seen[seq] = i
            if last_seq is not None and seq <= last_seq:
                ctx.report.add(
                    "ASSETS_PARTS_SEQ_UNIQUE", "error", lf.rel,
                    f"assets.parts[{i}].seq {seq} not strictly greater than previous {last_seq}",
                )
            last_seq = seq
        _check_hash_format(ctx, lf, f"assets.parts[{i}].hash", part.get("hash"))

        # seq matches the juan file's seq?
        fname = part.get("filename")
        if isinstance(fname, str) and isinstance(seq, int):
            juan_lf = (
                ctx.editions[edition_short].juans.get(seq)
                if edition_short
                else ctx.master_juans.get(seq)
            )
            if juan_lf is not None and juan_lf.exists and isinstance(juan_lf.data, dict):
                file_seq = juan_lf.data.get("seq")
                if file_seq != seq:
                    ctx.report.add(
                        "ASSETS_PARTS_SEQ_MATCHES_FILE", "error", lf.rel,
                        f"assets.parts[{i}] seq={seq} but juan file '{fname}' has seq={file_seq}",
                    )


def _check_assets_markers(
    ctx: ValidationContext, lf: LoadedFile, edition_short: str | None,
) -> None:
    assets = lf.data.get("assets")
    if not isinstance(assets, dict):
        return
    markers = assets.get("markers")
    if markers is None:
        return
    if not isinstance(markers, list):
        ctx.report.add(
            "MANIFEST_REQUIRED_KEYS", "error", lf.rel,
            "assets.markers is not a list",
        )
        return
    seen: dict[int, int] = {}
    part_seqs = {
        p.get("seq")
        for p in assets.get("parts") or []
        if isinstance(p, dict) and isinstance(p.get("seq"), int)
    }
    for i, entry in enumerate(markers):
        if not isinstance(entry, dict):
            ctx.report.add(
                "MANIFEST_REQUIRED_KEYS", "error", lf.rel,
                f"assets.markers[{i}] is not a mapping",
            )
            continue
        seq = entry.get("seq")
        if not isinstance(seq, int):
            ctx.report.add(
                "MANIFEST_REQUIRED_KEYS", "error", lf.rel,
                f"assets.markers[{i}].seq is missing or not an int",
            )
            continue
        if seq in seen:
            ctx.report.add(
                "ASSETS_MARKERS_SEQ_UNIQUE", "error", lf.rel,
                f"duplicate seq {seq} at assets.markers[{i}] (also at [{seen[seq]}])",
            )
        seen[seq] = i
        if seq not in part_seqs:
            ctx.report.add(
                "ASSETS_MARKERS_SEQ_MATCHES_PART", "error", lf.rel,
                f"assets.markers[{i}] seq={seq} has no matching assets.parts entry",
            )
        filename = entry.get("filename")
        if not isinstance(filename, str):
            ctx.report.add(
                "MANIFEST_REQUIRED_KEYS", "error", lf.rel,
                f"assets.markers[{i}].filename is missing or not a string",
            )
        elif not filename.startswith("assets/") or not filename.endswith(".markers.yaml"):
            ctx.report.add(
                "ASSETS_MARKERS_FILENAME", "error", lf.rel,
                f"assets.markers[{i}].filename '{filename}' should be assets/*.markers.yaml",
            )
        _check_hash_format(ctx, lf, f"assets.markers[{i}].hash", entry.get("hash"))

        asset_lf = (
            ctx.editions[edition_short].marker_assets.get(seq)
            if edition_short and edition_short in ctx.editions
            else ctx.marker_assets.get(seq)
        )
        if asset_lf is None or not asset_lf.exists or not isinstance(asset_lf.data, dict):
            continue
        if asset_lf.data.get("seq") != seq:
            ctx.report.add(
                "MARKER_ASSET_SEQ_MATCHES_MANIFEST", "error", asset_lf.rel,
                f"marker asset seq={asset_lf.data.get('seq')} does not match manifest seq={seq}",
            )
        actual_hash = marker_asset_hash(asset_lf.data)
        if isinstance(entry.get("hash"), str) and entry["hash"] != actual_hash:
            ctx.report.add(
                "MARKER_ASSET_HASH_MATCHES", "error", asset_lf.rel,
                f"marker asset hash {actual_hash} does not match manifest hash {entry['hash']}",
            )


def _check_toc(
    ctx: ValidationContext, lf: LoadedFile, edition_short: str | None,
) -> None:
    toc = lf.data.get("table_of_contents")
    if not isinstance(toc, list):
        return
    # Group spans per (seq, bucket) to detect overlaps.
    spans_by_key: dict[tuple[int, str], list[tuple[int, int, int]]] = {}
    for i, entry in enumerate(toc):
        if not isinstance(entry, dict):
            ctx.report.add(
                "TOC_REF_SPAN_PRESENT", "error", lf.rel,
                f"table_of_contents[{i}] is not a mapping",
            )
            continue
        ref = entry.get("ref")
        if not isinstance(ref, dict):
            ctx.report.add(
                "TOC_REF_SPAN_PRESENT", "error", lf.rel,
                f"table_of_contents[{i}].ref missing or not a mapping",
            )
            continue
        seq = ref.get("seq")
        marker_id = ref.get("marker_id")
        span = ref.get("span")
        if not isinstance(span, list) or len(span) != 3:
            ctx.report.add(
                "TOC_REF_SPAN_PRESENT", "error", lf.rel,
                f"table_of_contents[{i}].ref.span missing or wrong shape",
            )
            continue
        bucket, start, end = span
        if bucket not in VALID_BUCKETS:
            ctx.report.add(
                "TOC_REF_SPAN_BUCKET", "error", lf.rel,
                f"table_of_contents[{i}].ref.span bucket '{bucket}' is not one of {VALID_BUCKETS}",
            )
        if not isinstance(start, int) or not isinstance(end, int):
            ctx.report.add(
                "TOC_REF_SPAN_BOUNDS", "error", lf.rel,
                f"table_of_contents[{i}].ref.span start/end must be integers",
            )
            continue

        # Bound check against the referenced juan's bucket length.
        text_len = _bucket_text_len(ctx, edition_short, seq, bucket)
        if text_len is not None:
            # Allow start==end as a navigation-only sentinel (samples use [body, 0, 0]).
            if not (0 <= start <= end <= text_len):
                ctx.report.add(
                    "TOC_REF_SPAN_BOUNDS", "error", lf.rel,
                    f"table_of_contents[{i}].ref.span [{bucket}, {start}, {end}] out of bounds (bucket len={text_len})",
                )

        # Marker id should resolve.
        if isinstance(marker_id, str) and marker_id:
            if not _juan_has_marker_id(ctx, edition_short, seq, marker_id):
                ctx.report.add(
                    "TOC_MARKER_ID_RESOLVES", "error", lf.rel,
                    f"table_of_contents[{i}].ref.marker_id '{marker_id}' not found in juan seq={seq}",
                )

        if isinstance(seq, int) and bucket in VALID_BUCKETS and start < end:
            spans_by_key.setdefault((seq, bucket), []).append((i, start, end))

    # Overlap check: sort spans by start, ensure end <= next start.
    for (seq, bucket), items in spans_by_key.items():
        items_sorted = sorted(items, key=lambda t: (t[1], t[2]))
        for a, b in zip(items_sorted, items_sorted[1:]):
            if a[2] > b[1]:
                ctx.report.add(
                    "TOC_REF_SPAN_OVERLAP", "error", lf.rel,
                    f"table_of_contents entries [{a[0]}] and [{b[0]}] overlap in juan seq={seq} bucket={bucket}",
                )


def _bucket_text_len(
    ctx: ValidationContext, edition_short: str | None, seq: object, bucket: str,
) -> int | None:
    if not isinstance(seq, int) or bucket not in VALID_BUCKETS:
        return None
    juan_lf = (
        ctx.editions[edition_short].juans.get(seq)
        if edition_short and edition_short in ctx.editions
        else ctx.master_juans.get(seq)
    )
    if juan_lf is None or not juan_lf.exists or not isinstance(juan_lf.data, dict):
        return None
    bucket_obj = juan_lf.data.get(bucket)
    if not isinstance(bucket_obj, dict):
        return None
    text = bucket_obj.get("text", "")
    return len(text) if isinstance(text, str) else None


def _juan_has_marker_id(
    ctx: ValidationContext, edition_short: str | None, seq: object, marker_id: str,
) -> bool:
    if not isinstance(seq, int):
        return False
    juan_lf = (
        ctx.editions[edition_short].juans.get(seq)
        if edition_short and edition_short in ctx.editions
        else ctx.master_juans.get(seq)
    )
    if juan_lf is None or not juan_lf.exists or not isinstance(juan_lf.data, dict):
        # Can't verify; don't false-positive.
        return True
    for bucket in VALID_BUCKETS:
        bucket_obj = juan_lf.data.get(bucket)
        if not isinstance(bucket_obj, dict):
            continue
        marker_lf = (
            ctx.editions[edition_short].marker_assets.get(seq)
            if edition_short and edition_short in ctx.editions
            else ctx.marker_assets.get(seq)
        )
        marker_asset = (
            marker_lf.data
            if marker_lf is not None and isinstance(marker_lf.data, dict)
            else None
        )
        for m in effective_markers_for_bucket(juan_lf.data, bucket, marker_asset):
            if isinstance(m, dict) and m.get("id") == marker_id:
                return True
    return False


def _check_metadata_edition(
    ctx: ValidationContext, lf: LoadedFile, kind: str, edition_short: str | None,
) -> None:
    md = lf.data.get("metadata")
    if not isinstance(md, dict):
        return
    ed = md.get("edition")
    if not isinstance(ed, dict):
        return
    short = ed.get("short")
    expected = "bkk" if kind == "master" else edition_short
    if short != expected:
        ctx.report.add(
            "METADATA_EDITION_SHORT", "error", lf.rel,
            f"metadata.edition.short is '{short}', expected '{expected}'",
        )
