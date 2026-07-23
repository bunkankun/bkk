"""Section C: juan field constraints (master + edition juans)."""

from __future__ import annotations

import unicodedata

from ..context import ValidationContext, LoadedFile
from bkk.marker_assets import (
    STRUCTURAL_MARKER_TYPES,
    VALID_BUCKETS,
    effective_markers_for_bucket,
)

KNOWN_MARKER_TYPES = {
    "page-break", "line-break", "indent", "punctuation", "paragraph-break",
    "comment", "head", "variant", "voice", "voice:problem", "substitution",
    "substitution:lemma-repeat",
    "kr:org-directive", "kr:non-cjk", "kr:newline",
    "tls:head", "tls:seg", "tls:ann",
    "tls:div-start", "tls:div-end",
}

REQUIRED_JUAN_KEYS = ("canonical_identifier", "seq", "body", "metadata", "hash")
REQUIRED_BUCKET_KEYS = ("text", "hash")


def run(ctx: ValidationContext) -> None:
    # In KRP, the master juan carries markers from each declared witness; in
    # TLS, it carries the sole witness's markers. Either way the marker
    # edition segment must match a known witness short.
    declared_shorts = _declared_witness_shorts(ctx)
    on_disk_shorts = set(ctx.editions.keys())
    # TLS-shape bundles don't declare witnesses in the manifest, but their
    # master juan may still carry markers from any number of upstream witnesses
    # (WYG, SBCK, CHANT, …) merged in from KRP. Treat editions/ subdirs as
    # additive rather than exhaustive: if nothing is declared, don't constrain
    # the master's marker edition segment at all.
    allowed_master_editions: set[str] | None
    if not declared_shorts:
        allowed_master_editions = None
    else:
        allowed_master_editions = declared_shorts | on_disk_shorts | {"krp"}

    for lf in ctx.master_juans.values():
        seq = lf.data.get("seq") if isinstance(lf.data, dict) else None
        marker_asset = (
            ctx.marker_assets.get(seq).data
            if isinstance(seq, int)
            and ctx.marker_assets.get(seq) is not None
            and isinstance(ctx.marker_assets[seq].data, dict)
            else None
        )
        _check_juan(
            ctx, lf, marker_asset=marker_asset,
            allowed_marker_editions=allowed_master_editions,
        )
    for short, ed in ctx.editions.items():
        # editions/krp/ is the demoted KRP master in a TLS+KRP merge — it
        # legitimately carries page-break ids from every witness, so apply
        # the same allowed-shorts set the master uses.
        allowed = (
            allowed_master_editions if short == "krp" else {short}
        )
        for lf in ed.juans.values():
            seq = lf.data.get("seq") if isinstance(lf.data, dict) else None
            marker_asset = (
                ed.marker_assets.get(seq).data
                if isinstance(seq, int)
                and ed.marker_assets.get(seq) is not None
                and isinstance(ed.marker_assets[seq].data, dict)
                else None
            )
            _check_juan(
                ctx, lf, marker_asset=marker_asset,
                allowed_marker_editions=allowed,
            )


def _declared_witness_shorts(ctx: ValidationContext) -> set[str]:
    out: set[str] = set()
    if isinstance(ctx.master_manifest.data, dict):
        for ed in ctx.master_manifest.data.get("editions") or []:
            if isinstance(ed, dict) and isinstance(ed.get("short"), str):
                out.add(ed["short"])
    return out


def _check_juan(
    ctx: ValidationContext, lf: LoadedFile, *,
    marker_asset: dict | None,
    allowed_marker_editions: set[str] | None,
) -> None:
    if not lf.exists:
        return  # already flagged
    if lf.parse_error is not None:
        ctx.report.add(
            "MANIFEST_PARSE", "error", lf.rel,
            f"YAML parse error: {lf.parse_error}",
        )
        return
    if not isinstance(lf.data, dict):
        ctx.report.add(
            "JUAN_REQUIRED_KEYS", "error", lf.rel,
            "juan top level is not a mapping",
        )
        return
    data = lf.data

    for key in REQUIRED_JUAN_KEYS:
        if key not in data:
            ctx.report.add(
                "JUAN_REQUIRED_KEYS", "error", lf.rel,
                f"missing required key '{key}'",
            )

    # Top-level hash format.
    _check_hash_format(ctx, lf, "hash", data.get("hash"))

    # Buckets.
    bucket_keys_present = [k for k in VALID_BUCKETS if k in data]
    if "body" not in data:
        ctx.report.add(
            "JUAN_BUCKETS_VALID", "error", lf.rel,
            "required bucket 'body' is missing",
        )
    # Any keys at top level that look like buckets but aren't valid?
    for k in data.keys():
        if k in ("canonical_identifier", "seq", "metadata", "hash", *VALID_BUCKETS):
            continue
        # Not a known bucket; only flag if it shadows the bucket namespace by name.
        # We don't aggressively reject unknown top-level keys (forward compat).

    for bucket_name in bucket_keys_present:
        _check_bucket(
            ctx, lf, allowed_marker_editions=allowed_marker_editions,
            bucket_name=bucket_name, marker_asset=marker_asset,
        )


def _check_bucket(
    ctx: ValidationContext, lf: LoadedFile, *,
    allowed_marker_editions: set[str] | None, bucket_name: str,
    marker_asset: dict | None,
) -> None:
    bucket = lf.data.get(bucket_name)
    if not isinstance(bucket, dict):
        ctx.report.add(
            "JUAN_BUCKETS_VALID", "error", lf.rel,
            f"bucket '{bucket_name}' is not a mapping",
        )
        return
    for key in REQUIRED_BUCKET_KEYS:
        if key not in bucket:
            ctx.report.add(
                "JUAN_REQUIRED_KEYS", "error", lf.rel,
                f"bucket '{bucket_name}' missing required key '{key}'",
            )
    text = bucket.get("text", "")
    if not isinstance(text, str):
        ctx.report.add(
            "JUAN_REQUIRED_KEYS", "error", lf.rel,
            f"bucket '{bucket_name}'.text is not a string",
        )
        return
    if unicodedata.normalize("NFC", text) != text:
        ctx.report.add(
            "JUAN_TEXT_NFC", "error", lf.rel,
            f"bucket '{bucket_name}'.text is not in NFC normalization",
        )

    _check_hash_format(ctx, lf, f"{bucket_name}.hash", bucket.get("hash"))

    markers = effective_markers_for_bucket(lf.data, bucket_name, marker_asset)
    inline_markers = bucket.get("markers")
    if inline_markers is not None and not isinstance(inline_markers, list):
        ctx.report.add(
            "JUAN_REQUIRED_KEYS", "error", lf.rel,
            f"bucket '{bucket_name}'.markers is not a list",
        )
    elif isinstance(inline_markers, list):
        for i, marker in enumerate(inline_markers):
            if (
                isinstance(marker, dict)
                and marker.get("type") not in STRUCTURAL_MARKER_TYPES
            ):
                ctx.report.add(
                    "JUAN_INLINE_MARKER_BULKY", "warning", lf.rel,
                    f"{bucket_name}.markers[{i}] type '{marker.get('type')}' should normally live in a marker asset",
                )
                break
    _check_markers(
        ctx, lf, allowed_marker_editions=allowed_marker_editions,
        bucket_name=bucket_name, text=text, markers=markers,
    )


def _check_markers(
    ctx: ValidationContext, lf: LoadedFile, *,
    allowed_marker_editions: set[str] | None,
    bucket_name: str, text: str, markers: list,
) -> None:
    text_len = len(text)
    last_offset: int | None = None
    out_of_order = False
    seen_ids: dict[str, int] = {}

    for i, m in enumerate(markers):
        if not isinstance(m, dict):
            ctx.report.add(
                "JUAN_REQUIRED_KEYS", "error", lf.rel,
                f"{bucket_name}.markers[{i}] is not a mapping",
            )
            continue
        mtype = m.get("type")
        offset = m.get("offset")
        mid = m.get("id") or ""

        if isinstance(mtype, str) and mtype not in KNOWN_MARKER_TYPES:
            ctx.report.add(
                "JUAN_MARKER_TYPE_KNOWN", "warning", lf.rel,
                f"{bucket_name}.markers[{i}] has unknown type '{mtype}'",
            )
        if not isinstance(offset, int):
            ctx.report.add(
                "JUAN_MARKER_OFFSET_BOUNDS", "error", lf.rel,
                f"{bucket_name}.markers[{i}] offset missing or not an int",
            )
        else:
            if not (0 <= offset <= text_len):
                ctx.report.add(
                    "JUAN_MARKER_OFFSET_BOUNDS", "error", lf.rel,
                    f"{bucket_name}.markers[{i}] offset {offset} out of range [0, {text_len}]",
                )
            if last_offset is not None and offset < last_offset and not out_of_order:
                ctx.report.add(
                    "JUAN_MARKER_OFFSET_ORDER", "warning", lf.rel,
                    f"{bucket_name}.markers[{i}] offset {offset} precedes previous offset {last_offset}",
                )
                out_of_order = True
            last_offset = offset

        if isinstance(mid, str) and mid:
            # tls:div-start / tls:div-end intentionally share the bracketed
            # head's xml:id as a structural correlation (open/close pairs +
            # the head marker all carry the same id). Exempt them from the
            # uniqueness check; the format check still applies.
            if mtype not in ("tls:div-start", "tls:div-end"):
                if mid in seen_ids:
                    ctx.report.add(
                        "JUAN_MARKER_ID_UNIQUE", "error", lf.rel,
                        f"{bucket_name}.markers[{i}] duplicate id '{mid}' (also at index {seen_ids[mid]})",
                    )
                else:
                    seen_ids[mid] = i
            # tls:ann markers carry annotation UUIDs and voice markers carry
            # juan-local r1/c1 ids — neither follows the standard
            # <text-id>_<edition>_<location> form. Skip the format check.
            if mtype not in ("tls:ann", "voice"):
                _check_marker_id_format(
                    ctx, lf, bucket_name, i, mid, allowed_marker_editions,
                )


def _check_marker_id_format(
    ctx: ValidationContext, lf: LoadedFile, bucket: str, i: int, mid: str,
    allowed_editions: set[str] | None,
) -> None:
    # Format: <text-id>_<edition>_<location>
    parts = mid.split("_", 2)
    if len(parts) != 3 or not parts[2]:
        ctx.report.add(
            "JUAN_MARKER_ID_FORMAT", "error", lf.rel,
            f"{bucket}.markers[{i}] id '{mid}' does not match <text-id>_<edition>_<location>",
        )
        return
    text_id, edition, _location = parts
    if text_id != _ctx_text_id(lf):
        ctx.report.add(
            "JUAN_MARKER_ID_FORMAT", "error", lf.rel,
            f"{bucket}.markers[{i}] id '{mid}' text-id segment '{text_id}' does not match file's text-id",
        )
        return
    if allowed_editions is not None and edition not in allowed_editions:
        ctx.report.add(
            "JUAN_MARKER_ID_FORMAT", "error", lf.rel,
            f"{bucket}.markers[{i}] id '{mid}' edition segment '{edition}' not in allowed {sorted(allowed_editions)}",
        )


def _ctx_text_id(lf: LoadedFile) -> str:
    """Recover the text-id from the juan filename's stem prefix."""
    stem = lf.path.stem  # e.g. KR3a0013_000 or KR3a0013_000-WYG
    return stem.split("_", 1)[0]


def _check_hash_format(
    ctx: ValidationContext, lf: LoadedFile, field: str, value: object,
) -> None:
    import re
    if value is None:
        return
    if not isinstance(value, str) or not re.match(r"^sha256:[0-9a-f]{64}$", value):
        ctx.report.add(
            "HASH_FORMAT", "error", lf.rel,
            f"{field}: '{value}' does not match sha256:<64-hex>",
        )
