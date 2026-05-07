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
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from ..hashing import manifest_hash
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
