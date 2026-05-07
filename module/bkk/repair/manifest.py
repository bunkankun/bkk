"""Rebuild master + edition manifests from juan YAML files on disk.

Reuses :func:`bkk.importer.write.bundle.build_manifest` for the manifest
shape. The TOC is reconstructed from the markers stored in each juan's
buckets:

- classic TLS: one ``type: section, level: 1`` entry per ``tls:head``
  marker (span = head_offset .. next_head_offset_or_len(text)). Headless
  leading regions are skipped — lossy compared to the original importer
  shape, but in practice every TLS ``<div type="juan">`` carries a
  ``<head>`` so this is lossless for the multi-XML-file case this module
  exists for.
- CBETA-flavor: one ``type: juan`` entry per ``cbeta:juan-start`` marker
  and one ``type: mulu`` point entry per ``cbeta:mulu`` marker. Mirrors
  ``bkk.importer.write.bundle._build_toc_cbeta``.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from bkk.importer.hashing import manifest_hash
from bkk.importer.write.bundle import build_manifest
from bkk.importer.write.yaml_writer import dump, marker_to_flow


_JUAN_RE = re.compile(
    r"^(?P<text_id>.+?)_(?P<seq>\d{3})(?:-(?P<short>[A-Za-z0-9]+))?\.yaml$",
)
_ANN_RE = re.compile(r"^(?P<text_id>.+?)_(?P<seq>\d{3})\.ann\.yaml$")


def rebuild_manifests(bundle_dir: Path) -> dict:
    """Rebuild master + every edition manifest under ``bundle_dir`` from the
    juan YAML files present on disk. Returns a small summary dict."""
    bundle_dir = Path(bundle_dir).resolve()
    if not bundle_dir.is_dir():
        raise FileNotFoundError(f"not a directory: {bundle_dir}")
    text_id = bundle_dir.name

    summary: dict = {
        "bundle_dir": str(bundle_dir),
        "text_id": text_id,
        "master": _rebuild_master(bundle_dir, text_id),
        "editions": [],
    }

    editions_root = bundle_dir / "editions"
    if editions_root.is_dir():
        for sub in sorted(editions_root.iterdir()):
            if not sub.is_dir():
                continue
            summary["editions"].append(
                _rebuild_edition(sub, text_id, sub.name)
            )
    return summary


# ---------- per-manifest rebuild --------------------------------------------


def _rebuild_master(bundle_dir: Path, text_id: str) -> dict:
    manifest_path = bundle_dir / f"{text_id}.manifest.yaml"
    existing = _load_existing(manifest_path)

    juans = _collect_juans(bundle_dir, text_id, edition_short=None)
    anns = _collect_anns(bundle_dir, text_id)
    toc = _toc_from_juans(juans)

    metadata = _carry_over_metadata(existing, is_edition=False)
    parts = [(seq, fname, h) for seq, fname, h, _ in juans]

    manifest = build_manifest(
        text_id=text_id,
        edition_short=None,
        juan_files=parts,
        ann_files=anns,
        toc=toc,
        metadata=metadata,
        entity_encoding="entity_encoding" in existing,
    )
    manifest["hash"] = manifest_hash(manifest)
    manifest_path.write_text(dump(manifest), encoding="utf-8")
    return {
        "manifest": manifest_path.name,
        "parts": len(parts),
        "annotations": len(anns),
        "toc": len(toc),
    }


def _rebuild_edition(edition_dir: Path, text_id: str, short: str) -> dict:
    manifest_path = edition_dir / f"{text_id}-{short}.manifest.yaml"
    existing = _load_existing(manifest_path)

    juans = _collect_juans(edition_dir, text_id, edition_short=short)
    toc = _toc_from_juans(juans)

    metadata = _carry_over_metadata(existing, is_edition=True)
    parts = [(seq, fname, h) for seq, fname, h, _ in juans]

    manifest = build_manifest(
        text_id=text_id,
        edition_short=short,
        juan_files=parts,
        ann_files=[],
        toc=toc,
        metadata=metadata,
        entity_encoding="entity_encoding" in existing,
    )
    manifest["hash"] = manifest_hash(manifest)
    manifest_path.write_text(dump(manifest), encoding="utf-8")
    return {
        "edition": short,
        "manifest": manifest_path.name,
        "parts": len(parts),
        "toc": len(toc),
    }


# ---------- existing-manifest passthrough -----------------------------------


def _load_existing(path: Path) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _carry_over_metadata(existing: dict, *, is_edition: bool) -> dict:
    """Project the existing manifest's metadata bag into the form expected
    by ``build_manifest``: drops the rebuilt ``edition`` block, lifts
    ``edition.label`` into ``edition_label`` (which ``build_manifest``
    re-applies for editions), and lifts the master-only top-level
    ``editions`` list into the metadata bag (where ``build_manifest``
    pops it back out)."""
    md_in = existing.get("metadata") or {}
    md = dict(md_in) if isinstance(md_in, dict) else {}
    edition_block = md.pop("edition", None)
    if (
        is_edition
        and isinstance(edition_block, dict)
        and edition_block.get("label")
    ):
        md["edition_label"] = edition_block["label"]
    if not is_edition:
        editions_top = existing.get("editions")
        if isinstance(editions_top, list):
            md["editions"] = list(editions_top)
    return md


# ---------- juan + annotation discovery -------------------------------------


def _collect_juans(
    juan_dir: Path, text_id: str, edition_short: str | None,
) -> list[tuple[int, str, str, dict]]:
    """Return ``[(seq, filename, juan_hash, juan_data), ...]`` sorted by seq."""
    out: list[tuple[int, str, str, dict]] = []
    for entry in sorted(juan_dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.name.endswith(".ann.yaml"):
            continue
        if entry.name.endswith(".manifest.yaml"):
            continue
        m = _JUAN_RE.match(entry.name)
        if not m or m.group("text_id") != text_id:
            continue
        if m.group("short") != edition_short:
            continue
        seq = int(m.group("seq"))
        data = yaml.safe_load(entry.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        h = data.get("hash")
        if not isinstance(h, str):
            continue
        out.append((seq, entry.name, h, data))
    out.sort(key=lambda t: t[0])
    return out


def _collect_anns(bundle_dir: Path, text_id: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for entry in sorted(bundle_dir.iterdir()):
        if not entry.is_file():
            continue
        m = _ANN_RE.match(entry.name)
        if not m or m.group("text_id") != text_id:
            continue
        seq = int(m.group("seq"))
        out.append((seq, entry.name))
    out.sort(key=lambda t: t[0])
    return out


# ---------- TOC reconstruction ----------------------------------------------


def _toc_from_juans(
    juans: list[tuple[int, str, str, dict]],
) -> list[dict]:
    toc: list[dict] = []
    for seq, _fn, _h, data in juans:
        flavor = ((data.get("metadata") or {}).get("flavor"))
        for bucket_name in ("front", "body", "back"):
            bucket = data.get(bucket_name)
            if not isinstance(bucket, dict):
                continue
            text = bucket.get("text") or ""
            markers = bucket.get("markers") or []
            if flavor == "cbeta":
                toc.extend(_toc_cbeta_bucket(seq, bucket_name, text, markers))
            else:
                toc.extend(_toc_classic_bucket(seq, bucket_name, text, markers))
    return toc


def _toc_classic_bucket(
    seq: int, bucket: str, text: str, markers: list,
) -> list[dict]:
    heads = [
        m for m in markers
        if isinstance(m, dict) and m.get("type") == "tls:head"
    ]
    if not heads:
        return []
    text_len = len(text)
    entries: list[dict] = []
    for i, h in enumerate(heads):
        start = int(h.get("offset", 0))
        if i + 1 < len(heads):
            end = int(heads[i + 1].get("offset", text_len))
        else:
            end = text_len
        entries.append({
            "ref": marker_to_flow({
                "seq": seq,
                "marker_id": h.get("id") or "",
                "span": [bucket, start, end],
            }),
            "label": h.get("content") or "",
            "type": "section",
            "level": 1,
        })
    return entries


def _toc_cbeta_bucket(
    seq: int, bucket: str, text: str, markers: list,
) -> list[dict]:
    if not text and not markers:
        return []
    text_len = len(text)
    entries: list[dict] = []
    for m in markers:
        if not isinstance(m, dict):
            continue
        mtype = m.get("type")
        if mtype == "cbeta:juan-start":
            entries.append({
                "ref": marker_to_flow({
                    "seq": seq,
                    "marker_id": m.get("id") or "",
                    "span": [bucket, int(m.get("offset", 0)), text_len],
                }),
                "label": m.get("jhead") or "",
                "type": "juan",
                "level": 1,
            })
        elif mtype == "cbeta:mulu":
            off = int(m.get("offset", 0))
            entries.append({
                "ref": marker_to_flow({
                    "seq": seq,
                    "marker_id": m.get("id") or "",
                    "span": [bucket, off, off],
                }),
                "label": m.get("content") or "",
                "type": "mulu",
                "level": 1,
            })
    return entries
