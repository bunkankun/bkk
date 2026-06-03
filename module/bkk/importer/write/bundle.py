"""Orchestrate Bundle → on-disk BKK files.

Lays out:

    <out-root>/<text-id>/
      <text-id>.manifest.yaml          (master)
      <text-id>_NNN.yaml               (master juan — byte-copy of T juan in v1)
      <text-id>_NNN.ann.yaml           (annotations, role tls:ann)
      editions/T/<text-id>-T.manifest.yaml
      editions/T/<text-id>_NNN-T.yaml

For v1 the master edition is a byte-copy of T (no interpretive changes yet).
Juan files carry a self-referential ``hash`` field; we use the zero-then-patch
pattern (see ``hashing.manifest_hash``) so the file's own hash matches the
manifest's ``parts.hash`` reference.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Callable

from ..canonicalize import merge_sections
from ..classify import bucket_sections
from ..hashing import ZERO_HASH, manifest_hash, sha256_jcs, sha256_text
from ..ir import Annotation, Bundle, Juan, Marker, Section
from .yaml_writer import dump, marker_to_flow
from bkk.marker_assets import (
    build_marker_asset,
    marker_asset_filename,
    split_inline_external_markers,
)


# ---------- helpers ---------------------------------------------------------


def _marker_dict(m: Marker) -> dict:
    if m.type == "variant":
        # Variant markers carry their own shape: {type, offset, length, content,
        # <witness-short>...}. ``length`` and the witness keys live in extras.
        d: dict = {
            "type": m.type,
            "offset": m.offset,
            "length": m.extras.get("length", len(m.content)),
            "content": m.content,
        }
        for k, v in m.extras.items():
            if k == "length":
                continue
            d[k] = v
        return marker_to_flow(d)

    d = {
        "type": m.type,
        "offset": m.offset,
        "content": m.content,
        "id": m.id,
    }
    for k, v in m.extras.items():
        d[k] = v
    return marker_to_flow(d)


def _ann_marker_content(payload: dict) -> str:
    """Compose the short ``form syn_func`` summary used on tls:ann markers."""
    form = payload.get("form") or {}
    sense = payload.get("sense") or {}
    orth = form.get("orth") or form.get("orig") or ""
    syn = sense.get("syn_func") or ""
    if orth and syn:
        return f"{orth} {syn}"
    return orth or syn


def _resolve_ann_offset(ann: Annotation, seg_offsets: dict[str, int]) -> int | None:
    """Resolve the annotation's anchor to a bucket-relative offset."""
    base = seg_offsets.get(ann.marker_id)
    if base is None:
        return None
    return base + ann.offset


def _build_bucket(
    sections: list[Section],
    annotations: list[Annotation],
    *,
    keep_marker_ids: set[str] | None = None,
) -> tuple[dict, list[dict], list[tuple[Annotation, int]]]:
    """Build the per-bucket dict (text/hash/markers) plus the list of
    ``(annotation, resolved_offset)`` for annotations that fell in this
    bucket."""
    text, markers, seg_offsets = merge_sections(sections)

    bucket_anns: list[tuple[Annotation, int]] = []
    for ann in annotations:
        if ann.marker_id not in seg_offsets:
            continue
        offset = _resolve_ann_offset(ann, seg_offsets)
        if offset is None:
            continue
        bucket_anns.append((ann, offset))
        markers.append(Marker(
            type="tls:ann",
            offset=offset,
            content=_ann_marker_content(ann.payload),
            id=ann.payload.get("id", ""),
        ))

    # Stable sort: by (offset, original index). Python's sort is stable, so
    # text-stream markers (added in order) precede tls:ann markers at the
    # same offset.
    indexed = list(enumerate(markers))
    indexed.sort(key=lambda p: (p[1].offset, p[0]))
    markers_sorted = [m for _, m in indexed]

    bucket = {
        "text": text,
        "hash": sha256_text(text) if text else ZERO_HASH,
    }
    marker_dicts = [_marker_dict(m) for m in markers_sorted]
    inline, external = split_inline_external_markers(
        marker_dicts, keep_ids=keep_marker_ids,
    )
    if inline:
        bucket["markers"] = inline
    return bucket, external, bucket_anns


def _build_juan_dict(
    text_id: str, seq: int, edition_short: str,
    front: dict, body: dict, back: dict | None,
    metadata: dict,
) -> dict:
    """Build the juan dict (with zeroed self-hash; caller patches)."""
    d: dict = {
        "canonical_identifier": (
            f"bkk:krp/{text_id}/{edition_short.lower()}/v1/juan/{seq}"
        ),
        "seq": seq,
        "front": front,
        "body": body,
    }
    if back is not None:
        d["back"] = back

    # juan-level metadata mirrors a subset of the bundle metadata.
    md: dict = {}
    if "title" in metadata:
        md["title"] = metadata["title"]
    md["edition"] = {"short": edition_short.lower()}
    if "source" in metadata:
        md["source"] = metadata["source"]
    d["metadata"] = md
    d["hash"] = ZERO_HASH
    return d


def _juan_self_hash(juan_dict: dict) -> str:
    """Compute the juan's self-referential hash (zero-then-JCS pattern)."""
    m = copy.deepcopy(juan_dict)
    m["hash"] = ZERO_HASH
    return sha256_jcs(m)


def _build_toc(
    sections_per_bucket: dict[str, list[Section]],
    juan: Juan,
) -> list[dict]:
    """Build TOC entries for one juan.

    Classic TLS (no ``flavor`` in juan.metadata): one ``type: section`` entry
    per section. Span is ``[bucket, start, end]`` where end is exclusive
    (start of next section in the same bucket, or len(text)).

    CBETA-flavor (``juan.metadata["flavor"] == "cbeta"``): emit one
    ``type: juan`` entry per ``cbeta:juan-start`` marker (label = jhead text;
    span = ``[bucket, marker_offset, len(bucket_text)]``) and one
    ``type: mulu`` point entry per ``cbeta:mulu`` marker (label = marker
    content; span = ``[bucket, offset, offset]``). ``<head>``-derived
    section entries are not emitted.
    """
    flavor = juan.metadata.get("flavor")
    if flavor == "cbeta":
        return _build_toc_cbeta(sections_per_bucket, juan.seq)
    return _build_toc_classic(sections_per_bucket, juan.seq)


def _build_toc_classic(
    sections_per_bucket: dict[str, list[Section]],
    seq: int,
) -> list[dict]:
    toc: list[dict] = []
    for bucket_name, secs in sections_per_bucket.items():
        cursor = 0
        for sec in secs:
            start = cursor
            end = cursor + len(sec.text)
            toc.append({
                "ref": marker_to_flow({
                    "seq": seq,
                    "marker_id": sec.head_marker_id,
                    "span": [bucket_name, start, end],
                }),
                "label": sec.head_text,
                "type": "section",
                "level": 1,
            })
            # Nested-div TOC entries (level >= 2). Pair each tls:div-start
            # with its matching tls:div-end via a balanced stack; well-formed
            # nesting in TEI sources guarantees pairing.
            start_to_end_offset: dict[int, int] = {}
            stack: list[int] = []
            for i, m in enumerate(sec.markers):
                if m.type == "tls:div-start":
                    stack.append(i)
                elif m.type == "tls:div-end" and stack:
                    s = stack.pop()
                    start_to_end_offset[s] = m.offset
            # Emit nested-div entries in document (DFS pre-) order.
            for i, m in enumerate(sec.markers):
                if m.type != "tls:div-start" or i not in start_to_end_offset:
                    continue
                extras = m.extras or {}
                level = extras.get("level")
                head_text = extras.get("head_text", "")
                if not head_text or not isinstance(level, int) or level < 2:
                    continue
                toc.append({
                    "ref": marker_to_flow({
                        "seq": seq,
                        "marker_id": m.id,
                        "span": [
                            bucket_name,
                            start + m.offset,
                            start + start_to_end_offset[i],
                        ],
                    }),
                    "label": head_text,
                    "type": "section",
                    "level": level,
                })
            cursor = end
    return toc


def _build_toc_cbeta(
    sections_per_bucket: dict[str, list[Section]],
    seq: int,
) -> list[dict]:
    toc: list[dict] = []
    for bucket_name, secs in sections_per_bucket.items():
        text, markers, _ = merge_sections(secs)
        if not text and not markers:
            continue
        end_of_bucket = len(text)
        for m in markers:
            if m.type == "cbeta:juan-start":
                label = m.extras.get("jhead", "") or ""
                toc.append({
                    "ref": marker_to_flow({
                        "seq": seq,
                        "marker_id": m.id,
                        "span": [bucket_name, m.offset, end_of_bucket],
                    }),
                    "label": label,
                    "type": "juan",
                    "level": 1,
                })
            elif m.type == "cbeta:mulu":
                toc.append({
                    "ref": marker_to_flow({
                        "seq": seq,
                        "marker_id": m.id,
                        "span": [bucket_name, m.offset, m.offset],
                    }),
                    "label": m.content,
                    "type": "mulu",
                    "level": 1,
                })
    return toc


def build_manifest(
    text_id: str,
    edition_short: str | None,           # None → master manifest
    juan_files: list[tuple[int, str, str]],   # (seq, filename, juan_self_hash)
    ann_files: list[tuple[int, str]],         # TLS-only; KRP passes []
    marker_files: list[tuple[int, str, str]] | None,
    toc: list[dict],
    metadata: dict,
    *,
    entity_encoding: bool = False,           # KRP-only PUA-encoding marker
) -> dict:
    """Build a manifest dict with ``hash`` zeroed; caller patches it."""
    is_edition = edition_short is not None
    edition_seg = f"{edition_short}/" if is_edition else ""
    canonical_id = f"bkk:krp/{text_id}/{edition_seg}v1".rstrip("/")
    canonical_loc = f"https://kanripo.org/bkk/{text_id}/{edition_seg}v1".rstrip("/")

    parts = [
        marker_to_flow({"seq": seq, "filename": fn, "hash": h})
        for seq, fn, h in juan_files
    ]

    assets: dict = {"parts": parts}
    if ann_files:
        assets["annotations"] = [
            marker_to_flow({"seq": seq, "role": "tls:ann", "filename": fn})
            for seq, fn in ann_files
        ]
    if marker_files:
        assets["markers"] = [
            marker_to_flow({
                "seq": seq,
                "role": "markers",
                "filename": fn,
                "hash": h,
            })
            for seq, fn, h in marker_files
        ]

    md = dict(metadata)
    edition_block: dict = {"short": edition_short if is_edition else "bkk"}
    if is_edition and metadata.get("edition_label"):
        edition_block["label"] = metadata["edition_label"]
    md["edition"] = edition_block
    # ``edition_label`` and ``editions`` flow into the manifest via dedicated
    # slots; drop them from the generic metadata bag.
    md.pop("edition_label", None)
    editions_list = md.pop("editions", None)

    manifest: dict = {
        "canonical_identifier": canonical_id,
        "canonical_location": canonical_loc,
        "canonical_set": {
            "identifier": "bkk:charset/cjk-v1",
            "hash": ZERO_HASH,  # TODO bkk-cjk-v1
        },
        "metadata": md,
    }
    if entity_encoding:
        manifest["entity_encoding"] = {
            "identifier": "bkk:encoding/kanripo-pua-v1",
            "hash": ZERO_HASH,
        }
    # Master-only top-level enumeration of documentary editions (KRP only;
    # TLS bundles don't populate ``editions``).
    if not is_edition and editions_list:
        manifest["editions"] = [marker_to_flow(e) for e in editions_list]
    manifest["assets"] = assets
    manifest["table_of_contents"] = toc
    manifest["hash"] = ZERO_HASH
    return manifest


def _build_ann_file(
    text_id: str, seq: int, edition_short: str,
    bucket_anns_by_bucket: dict[str, list[tuple[Annotation, int]]],
) -> dict:
    """Build the merged .ann.yaml dict for one juan."""
    entries: list[dict] = []
    bucket_priority = ["front", "body", "back"]
    for bucket_name in bucket_priority:
        items = bucket_anns_by_bucket.get(bucket_name, [])
        for ann, offset in items:
            payload = dict(ann.payload)
            ordered: dict = {}
            for key in ("id", "concept", "concept_id"):
                if key in payload:
                    ordered[key] = payload.pop(key)
            # Canonical anchor (new shape): marker_id + offset + length.
            ordered["marker_id"] = ann.marker_id
            ordered["anchor_offset"] = ann.offset
            ordered["length"] = ann.length
            # Round-trip carry-over for TLS-seed records (consumed by the
            # TLS exporter).
            if ann.tls_seg_id is not None:
                ordered["seg_id"] = ann.tls_seg_id
                ordered["pos"] = ann.tls_pos
            # Bucket-resolved offset for fast frontend rendering.
            ordered["bucket"] = bucket_name
            ordered["offset"] = offset
            for key, val in payload.items():
                ordered[key] = val
            entries.append(ordered)

    entries.sort(key=lambda e: (
        bucket_priority.index(e["bucket"]),
        e["offset"],
        e.get("id", ""),
    ))

    return {
        "text_id": text_id,
        "juan": f"{seq:03d}",
        "edition": edition_short,
        "annotations": entries,
    }


# ---------- top-level entry -------------------------------------------------


def write_bundle(
    bundle: Bundle, out_root: Path, *, skip_manifest_writes: bool = False,
) -> dict:
    """Write the BKK tree for ``bundle`` under ``out_root``. Return a small
    summary dict describing what was written.

    ``skip_manifest_writes`` suppresses writing the edition and master
    manifests (but still writes all juan/marker/annotation files). Use this
    when appending a companion volume to an existing CBETA bundle so that the
    first volume's manifest — and its primary identifier — are not overwritten
    before :func:`rebuild_manifests` consolidates everything."""
    text_id = bundle.text_id
    edition_short = bundle.edition_short
    bundle_root = out_root / text_id
    edition_root = bundle_root / "editions" / edition_short
    bundle_root.mkdir(parents=True, exist_ok=True)
    edition_root.mkdir(parents=True, exist_ok=True)

    juan_edition_files: list[tuple[int, str, str]] = []
    juan_master_files: list[tuple[int, str, str]] = []
    marker_edition_files: list[tuple[int, str, str]] = []
    marker_master_files: list[tuple[int, str, str]] = []
    ann_files: list[tuple[int, str]] = []
    toc_master: list[dict] = []
    toc_edition: list[dict] = []

    for juan in bundle.juans:
        front_secs, body_secs, back_secs = bucket_sections(juan.sections)
        sections_per_bucket = {
            "front": front_secs, "body": body_secs, "back": back_secs,
        }
        juan_toc = _build_toc(sections_per_bucket, juan)
        keep_marker_ids = {
            entry.get("ref", {}).get("marker_id")
            for entry in juan_toc
            if isinstance(entry, dict)
            and isinstance(entry.get("ref"), dict)
            and isinstance(entry["ref"].get("marker_id"), str)
        }

        bucket_dicts: dict[str, dict] = {}
        external_markers_by_bucket: dict[str, list[dict]] = {}
        bucket_anns_by_bucket: dict[str, list[tuple[Annotation, int]]] = {}
        for name, secs in sections_per_bucket.items():
            bdict, external, banns = _build_bucket(
                secs, juan.annotations, keep_marker_ids=keep_marker_ids,
            )
            bucket_dicts[name] = bdict
            external_markers_by_bucket[name] = external
            bucket_anns_by_bucket[name] = banns

        back_dict = bucket_dicts["back"] if back_secs else None

        # Edition juan file (zero-then-patch self hash).
        edition_juan = _build_juan_dict(
            text_id, juan.seq, edition_short,
            bucket_dicts["front"], bucket_dicts["body"], back_dict,
            bundle.metadata,
        )
        edition_juan_hash = _juan_self_hash(edition_juan)
        edition_juan["hash"] = edition_juan_hash
        edition_juan_filename = f"{text_id}_{juan.seq:03d}-{edition_short}.yaml"
        (edition_root / edition_juan_filename).write_text(
            dump(edition_juan), encoding="utf-8"
        )
        juan_edition_files.append(
            (juan.seq, edition_juan_filename, edition_juan_hash)
        )

        # Master juan: byte-copy of the edition juan.
        master_juan_filename = f"{text_id}_{juan.seq:03d}.yaml"
        (bundle_root / master_juan_filename).write_text(
            dump(edition_juan), encoding="utf-8"
        )
        juan_master_files.append(
            (juan.seq, master_juan_filename, edition_juan_hash)
        )

        # Marker assets (one per juan per manifest root).
        if any(external_markers_by_bucket.values()):
            edition_marker_asset = build_marker_asset(
                text_id, juan.seq, edition_short, external_markers_by_bucket,
            )
            edition_marker_filename = marker_asset_filename(
                text_id, juan.seq, edition_short,
            )
            (edition_root / "assets").mkdir(parents=True, exist_ok=True)
            (edition_root / edition_marker_filename).write_text(
                dump(edition_marker_asset), encoding="utf-8",
            )
            marker_edition_files.append(
                (juan.seq, edition_marker_filename, edition_marker_asset["hash"])
            )

            master_marker_asset = build_marker_asset(
                text_id, juan.seq, None, external_markers_by_bucket,
            )
            master_marker_filename = marker_asset_filename(text_id, juan.seq, None)
            (bundle_root / "assets").mkdir(parents=True, exist_ok=True)
            (bundle_root / master_marker_filename).write_text(
                dump(master_marker_asset), encoding="utf-8",
            )
            marker_master_files.append(
                (juan.seq, master_marker_filename, master_marker_asset["hash"])
            )

        # Annotation file (master only, per plan).
        if juan.annotations:
            ann_dict = _build_ann_file(
                text_id, juan.seq, edition_short, bucket_anns_by_bucket,
            )
            ann_filename = f"{text_id}_{juan.seq:03d}.ann.yaml"
            (bundle_root / ann_filename).write_text(dump(ann_dict), encoding="utf-8")
            ann_files.append((juan.seq, ann_filename))

        # TOC entries — one per section, computed offsets per spec.
        toc_edition.extend(juan_toc)
        toc_master.extend(juan_toc)

    if not skip_manifest_writes:
        # ---- edition manifest ----
        edition_manifest = build_manifest(
            text_id, edition_short, juan_edition_files, [], marker_edition_files,
            toc_edition,
            bundle.metadata,
        )
        edition_manifest["hash"] = manifest_hash(edition_manifest)
        edition_manifest_filename = f"{text_id}-{edition_short}.manifest.yaml"
        (edition_root / edition_manifest_filename).write_text(
            dump(edition_manifest), encoding="utf-8",
        )

        # ---- master manifest ----
        master_manifest = build_manifest(
            text_id, None, juan_master_files, ann_files, marker_master_files,
            toc_master, bundle.metadata,
        )
        master_manifest["hash"] = manifest_hash(master_manifest)
        master_manifest_filename = f"{text_id}.manifest.yaml"
        (bundle_root / master_manifest_filename).write_text(
            dump(master_manifest), encoding="utf-8"
        )

    # ---- source sidecar ----
    # Captures source-format-specific information (full teiHeader, div/head/
    # seg/pb attrs, annotation provenance + trees) needed by a future XML
    # exporter to round-trip back to TEI. Sibling to the manifest but *not*
    # part of the bundle: not referenced from any manifest, not in any hash
    # chain.
    sidecar_filename: str | None = None
    if bundle.source_info is not None:
        sidecar_filename = f"{text_id}.source.yaml"
        (bundle_root / sidecar_filename).write_text(
            dump(bundle.source_info), encoding="utf-8"
        )

    summary = {
        "text_id": text_id,
        "out_root": str(bundle_root),
        "juans": [s for s, _, _ in juan_edition_files],
        "annotations": [s for s, _ in ann_files],
    }
    if sidecar_filename is not None:
        summary["source_sidecar"] = sidecar_filename
    return summary


# ---------- KRP writer -----------------------------------------------------
#
# KRP bundles share the unified manifest shape with TLS (assets.parts,
# TOC ref.seq + label, metadata.edition: {short, label?}, source.path);
# KRP-only blocks layered on top:
# - ``entity_encoding:`` (Kanripo PUA mapping) on every KRP manifest,
# - ``metadata.base_edition`` on the master manifest,
# - top-level ``editions:`` list on the master manifest,
# - per-juan metadata carries ``juan_title``, per-file ``source.path``,
#   and (master only) ``image_base_urls``.
# - master writes a ``PUA-map.yaml`` at the bundle root.
# - master and documentary editions live in separate Bundles; the CLI
#   orchestrates one call per edition.


def _krp_juan_metadata(bundle: Bundle, juan: Juan, is_master: bool) -> dict:
    """Build the per-juan metadata dict, merging bundle + juan overrides."""
    md: dict = {}
    if "title" in bundle.metadata:
        md["title"] = bundle.metadata["title"]
    edition_block: dict = {
        "short": "krp" if is_master else bundle.edition_short,
    }
    if not is_master and bundle.metadata.get("edition_label"):
        edition_block["label"] = bundle.metadata["edition_label"]
    md["edition"] = edition_block
    if is_master and "base_edition" in bundle.metadata:
        md["base_edition"] = bundle.metadata["base_edition"]
    if "date" in bundle.metadata:
        md["date"] = bundle.metadata["date"]
    if juan.metadata.get("juan_title"):
        md["juan_title"] = juan.metadata["juan_title"]
    juan_source = juan.metadata.get("source")
    if juan_source:
        md["source"] = juan_source
    elif "source" in bundle.metadata:
        md["source"] = bundle.metadata["source"]
    if "image_base_urls" in bundle.metadata:
        md["image_base_urls"] = bundle.metadata["image_base_urls"]
    return md


def _build_krp_juan_dict(
    text_id: str, seq: int, slug: str,
    front: dict, body: dict, back: dict | None,
    metadata: dict,
) -> dict:
    d: dict = {
        "canonical_identifier": (
            f"bkk:krp/{text_id}/{slug}/v1/juan/{seq}"
        ),
        "seq": seq,
        "front": front,
        "body": body,
    }
    if back is not None:
        d["back"] = back
    d["metadata"] = metadata
    d["hash"] = ZERO_HASH
    return d


def _build_krp_toc_entry(
    juan_seq: int, head_text: str, head_marker_id: str,
    bucket: str, start: int, end: int,
    level: int = 1,
) -> dict:
    return {
        "ref": marker_to_flow({
            "seq": juan_seq,
            "marker_id": head_marker_id,
            "span": [bucket, start, end],
        }),
        "label": head_text,
        "type": "section",
        "level": level,
    }


def _build_pua_map_dict(pua_map: dict) -> dict:
    """Wrap the PUA-map's entries as flow dicts so each renders on one line."""
    return {
        "text_id": pua_map["text_id"],
        "total_unique": pua_map["total_unique"],
        "total_occurrences": pua_map["total_occurrences"],
        "entries": [marker_to_flow(e) for e in pua_map["entries"]],
    }


def _write_krp_juans(
    bundle: Bundle,
    juan_dir: Path,
    filename_for: Callable[[int], str],
    slug: str,
    is_master: bool,
) -> tuple[list[tuple[int, str, str]], list[tuple[int, str, str]], list[dict]]:
    """Write juan and marker-asset files for a KRP bundle."""
    juan_files: list[tuple[int, str, str]] = []
    marker_files: list[tuple[int, str, str]] = []
    toc: list[dict] = []
    for juan in bundle.juans:
        front_secs, body_secs, back_secs = bucket_sections(juan.sections)
        sections_per_bucket = {
            "front": front_secs, "body": body_secs, "back": back_secs,
        }

        # TOC: prefer Mandoku ``head`` markers (``** ...`` etc.) where KRP
        # source supplies them. Otherwise fall back to one entry per titled
        # section. Front-bucket entries are skipped: the front matter
        # (頭注 / opening outline) adds no navigation value.
        juan_toc: list[dict] = []
        for bucket_name, secs in sections_per_bucket.items():
            if bucket_name == "front":
                continue
            cursor = 0
            for sec in secs:
                start = cursor
                end = cursor + len(sec.text)
                cursor = end
                head_markers = [
                    m for m in sec.markers
                    if m.type == "head" and m.content and m.id
                ]
                if head_markers:
                    for idx, marker in enumerate(head_markers):
                        next_offset = (
                            head_markers[idx + 1].offset
                            if idx + 1 < len(head_markers) else len(sec.text)
                        )
                        level = marker.extras.get("level", 1)
                        if not isinstance(level, int) or level < 1:
                            level = 1
                        juan_toc.append(_build_krp_toc_entry(
                            juan.seq, marker.content, marker.id,
                            bucket_name, start + marker.offset,
                            start + next_offset, level,
                        ))
                    continue
                if not sec.head_text:
                    continue
                juan_toc.append(_build_krp_toc_entry(
                    juan.seq, sec.head_text, sec.head_marker_id,
                    bucket_name, start, end,
                ))
        keep_marker_ids = {
            entry.get("ref", {}).get("marker_id")
            for entry in juan_toc
            if isinstance(entry, dict)
            and isinstance(entry.get("ref"), dict)
            and isinstance(entry["ref"].get("marker_id"), str)
        }

        bucket_dicts: dict[str, dict] = {}
        external_markers_by_bucket: dict[str, list[dict]] = {}
        for name, secs in sections_per_bucket.items():
            bdict, external, _ = _build_bucket(
                secs, juan.annotations, keep_marker_ids=keep_marker_ids,
            )
            bucket_dicts[name] = bdict
            external_markers_by_bucket[name] = external

        back_dict = bucket_dicts["back"] if back_secs else None
        juan_md = _krp_juan_metadata(bundle, juan, is_master)
        juan_dict = _build_krp_juan_dict(
            bundle.text_id, juan.seq, slug,
            bucket_dicts["front"], bucket_dicts["body"], back_dict,
            juan_md,
        )
        juan_hash = _juan_self_hash(juan_dict)
        juan_dict["hash"] = juan_hash

        filename = filename_for(juan.seq)
        (juan_dir / filename).write_text(dump(juan_dict), encoding="utf-8")
        juan_files.append((juan.seq, filename, juan_hash))

        if any(external_markers_by_bucket.values()):
            asset_short = None if is_master else bundle.edition_short
            marker_asset = build_marker_asset(
                bundle.text_id, juan.seq, asset_short, external_markers_by_bucket,
            )
            marker_filename = marker_asset_filename(
                bundle.text_id, juan.seq, asset_short,
            )
            (juan_dir / "assets").mkdir(parents=True, exist_ok=True)
            (juan_dir / marker_filename).write_text(
                dump(marker_asset), encoding="utf-8",
            )
            marker_files.append((juan.seq, marker_filename, marker_asset["hash"]))

        toc.extend(juan_toc)

    return juan_files, marker_files, toc


def write_krp_edition(bundle: Bundle, out_root: Path) -> dict:
    """Write a documentary KRP edition tree under
    ``<out_root>/<text-id>/editions/<short>/``."""
    text_id = bundle.text_id
    short = bundle.edition_short
    bundle_root = out_root / text_id
    edition_root = bundle_root / "editions" / short
    edition_root.mkdir(parents=True, exist_ok=True)

    juan_files, marker_files, toc = _write_krp_juans(
        bundle,
        edition_root,
        filename_for=lambda seq: f"{text_id}_{seq:03d}-{short}.yaml",
        slug=short,
        is_master=False,
    )

    manifest = build_manifest(
        text_id, edition_short=short,
        juan_files=juan_files, ann_files=[], marker_files=marker_files, toc=toc,
        metadata=bundle.metadata, entity_encoding=True,
    )
    manifest["hash"] = manifest_hash(manifest)
    manifest_filename = f"{text_id}-{short}.manifest.yaml"
    (edition_root / manifest_filename).write_text(
        dump(manifest), encoding="utf-8"
    )

    return {
        "text_id": text_id,
        "edition": short,
        "out_root": str(bundle_root),
        "juans": [s for s, _, _ in juan_files],
    }


def write_krp_master(bundle: Bundle, out_root: Path) -> dict:
    """Write the master/bkk edition + PUA-map at ``<out_root>/<text-id>/``."""
    text_id = bundle.text_id
    bundle_root = out_root / text_id
    bundle_root.mkdir(parents=True, exist_ok=True)

    juan_files, marker_files, toc = _write_krp_juans(
        bundle,
        bundle_root,
        filename_for=lambda seq: f"{text_id}_{seq:03d}.yaml",
        slug="krp",
        is_master=True,
    )

    manifest = build_manifest(
        text_id, edition_short=None,
        juan_files=juan_files, ann_files=[], marker_files=marker_files, toc=toc,
        metadata=bundle.metadata, entity_encoding=True,
    )
    manifest["hash"] = manifest_hash(manifest)
    manifest_filename = f"{text_id}.manifest.yaml"
    (bundle_root / manifest_filename).write_text(
        dump(manifest), encoding="utf-8"
    )

    pua_filename: str | None = None
    if bundle.pua_map is not None:
        pua_filename = "PUA-map.yaml"
        pua_dict = _build_pua_map_dict(bundle.pua_map)
        (bundle_root / pua_filename).write_text(
            dump(pua_dict), encoding="utf-8"
        )

    summary = {
        "text_id": text_id,
        "edition": "krp",
        "out_root": str(bundle_root),
        "juans": [s for s, _, _ in juan_files],
    }
    if pua_filename is not None:
        summary["pua_map"] = pua_filename
    return summary


def write_pua_map(bundle: Bundle, out_root: Path) -> str | None:
    """Write ``<out_root>/<text-id>/PUA-map.yaml`` from ``bundle.pua_map`` if
    present. Returns the filename written, or ``None`` if the bundle has no
    PUA map.

    Used in merge mode where the KRP master is demoted to a regular edition
    but the PUA-map (which is bundle-wide, not edition-scoped) still belongs
    at the bundle root.
    """
    if bundle.pua_map is None:
        return None
    bundle_root = out_root / bundle.text_id
    bundle_root.mkdir(parents=True, exist_ok=True)
    pua_dict = _build_pua_map_dict(bundle.pua_map)
    filename = "PUA-map.yaml"
    (bundle_root / filename).write_text(dump(pua_dict), encoding="utf-8")
    return filename
