"""Detect existing bundles on disk and merge new editions into them.

The importer is normally write-once: each invocation rebuilds
``<out-root>/<text-id>/`` from scratch. When TLS and KRP both have a copy
of the same text we want them to co-exist:

- TLS owns the surface (root) edition and is imported first.
- KRP imported afterwards adds its editions under ``editions/`` and
  extends the TLS master manifest's ``editions:`` list. The TLS surface
  is never overwritten.
- The reverse direction (TLS into an existing KRP bundle) is rejected
  with a hard error; the user removes the bundle and re-imports in
  TLS-then-KRP order.

This module provides the read-side detection helper plus the in-place
update of the TLS master manifest's ``editions:`` list. The actual
write-skipping is handled in ``write_krp_master`` / ``write_krp_edition``
via the ``mode`` parameter they consult.

A second projection step lifts the apparatus carried by the KRP master
(variant markers + witness page-breaks) onto the TLS surface so that
readers of the TLS reading text see the union of all witnesses. The
projection reuses the same ``_attach_variants`` / ``_attach_witness_page_breaks``
helpers the KRP reader applies within its own bundle.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from ..hashing import manifest_hash
from ..ir import Bundle
from .yaml_writer import dump, marker_to_flow


BundleState = Literal["empty", "tls", "krp", "unknown"]


@dataclass
class ExistingBundle:
    """Snapshot of what is already on disk at ``<out_root>/<text_id>/``.

    - ``state`` reflects which source produced the master at the bundle
      root.
    - ``editions`` lists the edition shorts enumerated in the master
      manifest's top-level ``editions:`` list (KRP only or post-merge
      TLS); ``[]`` for a fresh TLS bundle.
    - ``tls_owned_editions`` is the set of edition shorts under
      ``editions/`` whose manifest is TLS-shaped (no ``entity_encoding``).
      The merge path uses this to refuse to overwrite TLS-owned editions
      when a KRP source happens to share a short. KRP-owned editions
      (``entity_encoding`` present) are not listed here and may be
      overwritten on re-import.
    """

    state: BundleState
    manifest_path: Path | None
    editions: list[str] = field(default_factory=list)
    tls_owned_editions: set[str] = field(default_factory=set)


def inspect_existing_bundle(out_root: Path, text_id: str) -> ExistingBundle:
    """Decide whether an existing bundle at ``<out_root>/<text_id>/`` is
    TLS-sourced, KRP-sourced, both (treated as TLS), or absent.

    Detection rules:

    - ``empty``   — no master manifest at the bundle root.
    - ``tls``     — ``<text-id>.source.yaml`` sidecar present (TLS writes it,
                    KRP does not). A merged TLS+KRP bundle stays in this state
                    because the surface remains TLS-owned.
    - ``krp``     — no sidecar, master manifest carries ``entity_encoding``.
    - ``unknown`` — manifest exists but matches neither shape.
    """
    bundle_root = out_root / text_id
    manifest_path = bundle_root / f"{text_id}.manifest.yaml"
    if not manifest_path.is_file():
        return ExistingBundle(state="empty", manifest_path=None)

    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return ExistingBundle(state="unknown", manifest_path=manifest_path)
    if not isinstance(manifest, dict):
        return ExistingBundle(state="unknown", manifest_path=manifest_path)

    sidecar = bundle_root / f"{text_id}.source.yaml"
    has_entity_encoding = "entity_encoding" in manifest

    editions: list[str] = []
    for ent in manifest.get("editions") or []:
        if isinstance(ent, dict) and isinstance(ent.get("short"), str):
            editions.append(ent["short"])

    tls_owned: set[str] = set()
    editions_dir = bundle_root / "editions"
    if editions_dir.is_dir():
        for sub in editions_dir.iterdir():
            if not sub.is_dir():
                continue
            short = sub.name
            ed_manifest = sub / f"{text_id}-{short}.manifest.yaml"
            if not ed_manifest.is_file():
                continue
            try:
                ed = yaml.safe_load(ed_manifest.read_text(encoding="utf-8"))
            except yaml.YAMLError:
                continue
            if isinstance(ed, dict) and "entity_encoding" not in ed:
                tls_owned.add(short)

    if sidecar.is_file():
        return ExistingBundle(
            state="tls",
            manifest_path=manifest_path,
            editions=editions,
            tls_owned_editions=tls_owned,
        )
    if has_entity_encoding:
        return ExistingBundle(
            state="krp",
            manifest_path=manifest_path,
            editions=editions,
            tls_owned_editions=tls_owned,
        )
    return ExistingBundle(state="unknown", manifest_path=manifest_path)


def extend_master_editions(
    manifest_path: Path, new_editions: list[dict],
) -> list[dict]:
    """Append entries to a master manifest's top-level ``editions:`` list.

    ``new_editions`` is a list of ``{"short": ..., "label": ...?}`` dicts.
    Existing shorts are preserved untouched; new shorts are appended in
    the order given. The manifest's self-hash is recomputed.

    Returns the final ``editions`` list as written.
    """
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(
            f"manifest at {manifest_path} is not a YAML mapping"
        )

    manifest = copy.deepcopy(raw)
    raw_existing = list(manifest.get("editions") or [])

    # Re-wrap each entry as a flow dict so re-imports don't flip the file
    # between flow and block style on each run. ``yaml.safe_load`` returns
    # plain dicts, which our writer emits as block-style mappings.
    current: list = [
        marker_to_flow({k: v for k, v in e.items() if v is not None})
        for e in raw_existing
        if isinstance(e, dict)
    ]
    have_shorts = {
        e.get("short") for e in current if isinstance(e, dict)
    }

    for entry in new_editions:
        short = entry.get("short")
        if not isinstance(short, str):
            continue
        if short in have_shorts:
            continue
        current.append(marker_to_flow({
            k: v for k, v in entry.items() if v is not None
        }))
        have_shorts.add(short)

    # Insert ``editions`` just before ``assets`` to match build_manifest's
    # ordering. If the source manifest doesn't have an ``editions`` key yet
    # we synthesize the slot.
    rebuilt: dict = {}
    inserted = False
    for key, val in manifest.items():
        if key == "editions":
            continue
        if key == "assets" and not inserted:
            rebuilt["editions"] = current
            inserted = True
        rebuilt[key] = val
    if not inserted:
        rebuilt["editions"] = current

    rebuilt["hash"] = manifest_hash(rebuilt)
    manifest_path.write_text(dump(rebuilt), encoding="utf-8")
    return current


def project_krp_apparatus_onto_tls(
    out_root: Path,
    text_id: str,
    surface_edition_short: str,
    krp_master: Bundle | None,
    krp_documentary: list[Bundle],
) -> dict:
    """Lift KRP apparatus onto the TLS surface juans during a merge.

    For each KRP edition (documentary witnesses + the demoted master),
    detect variants against the TLS reading text and inject witness
    page-breaks at aligned offsets. The TLS surface juans at the bundle
    root are rewritten with the union of their existing markers plus the
    projected variant / page-break markers; the master manifest's
    ``assets.parts`` hashes and self-hash are refreshed in place. Other
    files (sidecar, ann files, documentary editions) are untouched.

    ``surface_edition_short`` is the case-preserved short the TLS surface
    juans were originally written with (so the rebuilt
    ``canonical_identifier`` matches what was there before).

    Returns ``{"variants_added": int, "page_breaks_added": int}`` to make
    the projection observable from CLI logging and tests.
    """
    # Imports kept local: the helper pulls in the read-side variant /
    # page-break logic and the per-juan bucket writers, neither of which
    # the rest of this module needs.
    from ...exporter.read_bundle import read_bundle
    from ..classify import bucket_sections
    from ..read.krp import _attach_variants, _attach_witness_page_breaks
    from .bundle import (
        _build_bucket, _build_juan_dict, _juan_self_hash,
    )

    bundle_root = out_root / text_id
    manifest_path = bundle_root / f"{text_id}.manifest.yaml"

    surface = read_bundle(bundle_root)

    witnesses: list[tuple[str, dict[int, object]]] = []
    for wb in krp_documentary:
        witnesses.append(
            (wb.edition_short, {wj.seq: wj for wj in wb.juans})
        )
    if krp_master is not None:
        witnesses.append(
            (krp_master.edition_short, {mj.seq: mj for mj in krp_master.juans})
        )

    def _count(markers_pred) -> int:
        return sum(
            1 for tj in surface.juans for sec in tj.sections
            for m in sec.markers if markers_pred(m)
        )

    before_variants = _count(lambda m: m.type == "variant")
    before_pbs = _count(lambda m: m.type == "page-break")

    for tj in surface.juans:
        for wshort, by_seq in witnesses:
            wj = by_seq.get(tj.seq)
            if wj is None:
                continue
            _attach_variants(tj, wj, wshort)
            _attach_witness_page_breaks(tj, wj)

    after_variants = _count(lambda m: m.type == "variant")
    after_pbs = _count(lambda m: m.type == "page-break")

    new_part_hashes: dict[int, str] = {}
    for juan in surface.juans:
        front_secs, body_secs, back_secs = bucket_sections(juan.sections)
        sections_per_bucket = {
            "front": front_secs, "body": body_secs, "back": back_secs,
        }
        bucket_dicts: dict[str, dict] = {}
        for name, secs in sections_per_bucket.items():
            bucket_dicts[name], _ = _build_bucket(secs, juan.annotations)
        back_dict = bucket_dicts["back"] if back_secs else None

        juan_dict = _build_juan_dict(
            text_id, juan.seq, surface_edition_short,
            bucket_dicts["front"], bucket_dicts["body"], back_dict,
            surface.metadata,
        )
        juan_dict["hash"] = _juan_self_hash(juan_dict)
        new_part_hashes[juan.seq] = juan_dict["hash"]

        juan_filename = f"{text_id}_{juan.seq:03d}.yaml"
        (bundle_root / juan_filename).write_text(
            dump(juan_dict), encoding="utf-8"
        )

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    for part in (manifest.get("assets") or {}).get("parts") or []:
        seq = part.get("seq")
        if seq in new_part_hashes:
            part["hash"] = new_part_hashes[seq]
    manifest["hash"] = manifest_hash(manifest)
    manifest_path.write_text(dump(manifest), encoding="utf-8")

    return {
        "variants_added": after_variants - before_variants,
        "page_breaks_added": after_pbs - before_pbs,
    }
