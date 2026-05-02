"""Parse a BKK bundle directory back into a :class:`Bundle` IR.

Inverse of :mod:`bkk.importer.write.bundle`. Used by the exporter to
reconstruct the in-memory shape that the per-format emitters consume. The
sidecar (``<text-id>.source.yaml``), if present, is loaded into
``Bundle.source_info``.

Section recovery: the master manifest's ``table_of_contents`` carries one
entry per ``<div>`` with a ``[bucket, start, end]`` span, so we slice each
bucket's text+markers by those spans. ``tls:ann`` markers added at write
time are dropped (they're rebuilt from the .ann.yaml on the next round-trip).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ..importer.ir import Annotation, Bundle, Juan, Marker, Section


def read_bundle(bundle_dir: Path) -> Bundle:
    """Read the master Bundle from ``<bundle_dir>/<text_id>.manifest.yaml``."""
    bundle_dir = Path(bundle_dir)
    text_id = bundle_dir.name
    manifest_path = bundle_dir / f"{text_id}.manifest.yaml"
    sidecar_path = bundle_dir / f"{text_id}.source.yaml"
    sidecar = sidecar_path if sidecar_path.exists() else None
    return _bundle_from_manifest(
        text_id, bundle_dir, manifest_path, sidecar_path=sidecar,
    )


def read_edition_bundle(text_id: str, edition_dir: Path) -> Bundle:
    """Read a documentary edition Bundle (``editions/<short>/``)."""
    edition_dir = Path(edition_dir)
    short = edition_dir.name
    manifest_path = edition_dir / f"{text_id}-{short}.manifest.yaml"
    return _bundle_from_manifest(text_id, edition_dir, manifest_path)


def read_bundles(bundle_dir: Path) -> tuple[Bundle, list[Bundle]]:
    """Read master + every documentary edition under ``bundle_dir``.

    Returns ``(master, [documentary, ...])``. Documentary list is sorted by
    ``edition_short`` (alphabetical, mirroring ``editions/`` directory order).
    """
    bundle_dir = Path(bundle_dir)
    text_id = bundle_dir.name
    master = read_bundle(bundle_dir)
    documentary: list[Bundle] = []
    editions_dir = bundle_dir / "editions"
    if editions_dir.is_dir():
        for sub in sorted(editions_dir.iterdir()):
            if sub.is_dir():
                documentary.append(read_edition_bundle(text_id, sub))
    return master, documentary


def _bundle_from_manifest(
    text_id: str, file_dir: Path, manifest_path: Path,
    sidecar_path: Path | None = None,
) -> Bundle:
    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))

    source_info: dict | None = None
    if sidecar_path is not None and sidecar_path.exists():
        source_info = yaml.safe_load(sidecar_path.read_text(encoding="utf-8"))

    toc_by_juan = _index_toc(manifest.get("table_of_contents", []))
    ann_files = {
        e["seq"]: file_dir / e["filename"]
        for e in manifest.get("assets", {}).get("annotations", [])
    }

    juans: list[Juan] = []
    for part in manifest["assets"]["parts"]:
        seq = part["seq"]
        juan_data = yaml.safe_load(
            (file_dir / part["filename"]).read_text(encoding="utf-8")
        )
        sections = _sections_from_juan(juan_data, toc_by_juan.get(seq, {}))

        annotations: list[Annotation] = []
        ann_path = ann_files.get(seq)
        if ann_path is not None and ann_path.exists():
            ann_data = yaml.safe_load(ann_path.read_text(encoding="utf-8"))
            annotations = _annotations_from_ann_file(ann_data, source_info)

        juan_metadata = juan_data.get("metadata") or {}
        juans.append(Juan(
            seq=seq, sections=sections, annotations=annotations,
            metadata=dict(juan_metadata),
        ))

    metadata = dict(manifest.get("metadata", {}))
    edition_block = metadata.pop("edition", None) or {}
    if "editions" in manifest:
        metadata["editions"] = list(manifest["editions"])
    # Surface the full TOC for downstream renderers (e.g. KRP Readme.org).
    if "table_of_contents" in manifest:
        metadata["table_of_contents"] = list(manifest["table_of_contents"])

    edition_short = (
        edition_block.get("short")
        if isinstance(edition_block, dict) else None
    ) or _discover_edition_short(file_dir)
    if edition_short == "bkk":
        edition_short = "master"

    return Bundle(
        text_id=text_id,
        juans=juans,
        metadata=metadata,
        edition_short=edition_short,
        source_info=source_info,
    )


def _index_toc(toc: list[dict]) -> dict[int, dict[str, list[dict]]]:
    """Return ``{seq: {bucket: [entry, ...]}}`` preserving manifest order.

    Entries without a ``span`` (KRP shape — navigation only, no bucket
    boundary) are skipped; the bucket for those juans gets recovered as a
    single section in :func:`_sections_from_juan`.
    """
    out: dict[int, dict[str, list[dict]]] = {}
    for entry in toc:
        ref = entry["ref"]
        seq = ref["seq"]
        span = ref.get("span")
        if span is None:
            continue
        bucket, start, end = span
        out.setdefault(seq, {}).setdefault(bucket, []).append({
            "marker_id": ref["marker_id"],
            "start": start,
            "end": end,
            "label": entry["label"],
        })
    return out


def _sections_from_juan(juan_data: dict,
                        toc_for_juan: dict[str, list[dict]]) -> list[Section]:
    sections: list[Section] = []
    for bucket_name in ("front", "body", "back"):
        bucket = juan_data.get(bucket_name)
        if not bucket:
            continue
        text = bucket.get("text", "") or ""
        markers = bucket.get("markers", []) or []
        toc_entries = toc_for_juan.get(bucket_name, [])
        if toc_entries:
            sections.extend(_split_bucket(text, markers, toc_entries))
        else:
            # No bucket-spans in the TOC (KRP shape, or empty bucket): treat
            # the whole bucket as one section. Markers' offsets are already
            # bucket-relative.
            sections.append(Section(
                head_text="",
                head_marker_id="",
                text=text,
                markers=[
                    Marker(
                        type=m["type"],
                        offset=m.get("offset", 0) or 0,
                        content=m.get("content") or "",
                        id=m.get("id") or "",
                        extras={k: v for k, v in m.items()
                                if k not in ("type", "offset", "content", "id")},
                    )
                    for m in markers
                    if m.get("type") != "tls:ann"
                ],
                bucket=bucket_name,
            ))
    return sections


def _split_bucket(bucket_text: str, bucket_markers: list[dict],
                  toc_entries: list[dict]) -> list[Section]:
    """Split a merged bucket back into sections.

    Section boundaries follow ``tls:head`` markers (matched against TOC
    entries by id), not span offsets — at the boundary, markers like the
    previous section's closing punctuation/paragraph-break sit at the same
    offset as the next section's head, and offset filtering would mis-route
    them. Walking the marker list in order keeps each marker on the side it
    came from.
    """
    by_id = {e["marker_id"]: e for e in toc_entries}

    sec_entries: list[dict | None] = [None]
    sec_markers: list[list[dict]] = [[]]
    for m in bucket_markers:
        if m["type"] == "tls:ann":
            continue
        if m["type"] == "tls:head" and m.get("id") in by_id:
            sec_entries.append(by_id[m["id"]])
            sec_markers.append([m])
        else:
            sec_markers[-1].append(m)

    # Markers before the first tls:head belong to the first real section
    # (e.g. a leading <pb/> at offset 0).
    if len(sec_entries) > 1 and sec_markers[0]:
        sec_markers[1] = sec_markers[0] + sec_markers[1]
    sec_entries = sec_entries[1:]
    sec_markers = sec_markers[1:]

    out: list[Section] = []
    for entry, ms in zip(sec_entries, sec_markers):
        start = entry["start"]
        end = entry["end"]
        section_markers = [
            Marker(
                type=m["type"],
                offset=m["offset"] - start,
                content=m.get("content") or "",
                id=m.get("id") or "",
            )
            for m in ms
        ]
        out.append(Section(
            head_text=entry["label"],
            head_marker_id=entry["marker_id"],
            text=bucket_text[start:end],
            markers=section_markers,
        ))
    return out


def _annotations_from_ann_file(ann_data: dict,
                               source_info: dict | None) -> list[Annotation]:
    provenance_by_id: dict[str, str | None] = {}
    if source_info is not None:
        for ann_id, info in (source_info.get("annotations") or {}).items():
            provenance_by_id[ann_id] = info.get("provenance")

    out: list[Annotation] = []
    for entry in ann_data.get("annotations", []):
        payload = {k: v for k, v in entry.items()
                   if k not in ("seg_id", "pos", "bucket", "offset")}
        out.append(Annotation(
            seg_id=entry["seg_id"],
            pos=entry.get("pos"),
            payload=payload,
            source_role="tls:ann",
            provenance=provenance_by_id.get(payload.get("id", "")),
        ))
    return out


def _discover_edition_short(bundle_dir: Path) -> str:
    """Edition short id = the only directory name under ``editions/``.

    Falls back to ``T`` (the TLS default) if the editions tree is absent.
    """
    editions_dir = bundle_dir / "editions"
    if editions_dir.is_dir():
        for d in sorted(editions_dir.iterdir()):
            if d.is_dir():
                return d.name
    return "T"
