"""Patch ``metadata.identifiers`` on a bundle's master manifest."""

from __future__ import annotations

from pathlib import Path

import yaml

from bkk.importer.hashing import manifest_hash
from bkk.importer.write.yaml_writer import dump, reflow_manifest


def apply_alt_ids(
    bundle_dir: Path,
    alt_ids: list[str],
    *,
    dry_run: bool = False,
) -> dict:
    """Set ``metadata.identifiers.alt_id`` on the master manifest under
    ``bundle_dir``. Catalog wins: any existing ``alt_id`` is overwritten.

    Returns ``{'path', 'before', 'after', 'changed'}``.
    """
    bundle_dir = Path(bundle_dir).resolve()
    text_id = bundle_dir.name
    manifest_path = bundle_dir / f"{text_id}.manifest.yaml"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"manifest is not a mapping: {manifest_path}")

    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        manifest["metadata"] = metadata

    identifiers = metadata.get("identifiers")
    if not isinstance(identifiers, dict):
        identifiers = {}
        # Place identifiers right after `title` if present, else first.
        new_md: dict = {}
        for k, v in metadata.items():
            new_md[k] = v
            if k == "title":
                new_md["identifiers"] = identifiers
        if "identifiers" not in new_md:
            new_md = {"identifiers": identifiers, **new_md}
        manifest["metadata"] = new_md
        metadata = new_md

    before = list(identifiers.get("alt_id") or [])
    after = list(alt_ids)
    identifiers["alt_id"] = after

    changed = before != after
    if changed and not dry_run:
        reflow_manifest(manifest)
        manifest["hash"] = manifest_hash(manifest)
        manifest_path.write_text(dump(manifest), encoding="utf-8")

    return {
        "path": manifest_path,
        "before": before,
        "after": after,
        "changed": changed,
    }


def purge_non_alt_ids(
    bundle_dir: Path,
    *,
    dry_run: bool = False,
) -> dict:
    """Drop every key under ``metadata.identifiers`` except ``alt_id``.
    If the section becomes empty, remove it. Recomputes the manifest hash.

    Returns ``{'path', 'removed', 'changed'}`` where ``removed`` is the
    list of keys that were dropped.
    """
    bundle_dir = Path(bundle_dir).resolve()
    text_id = bundle_dir.name
    manifest_path = bundle_dir / f"{text_id}.manifest.yaml"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"manifest is not a mapping: {manifest_path}")

    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        return {"path": manifest_path, "removed": [], "changed": False}

    identifiers = metadata.get("identifiers")
    if not isinstance(identifiers, dict):
        return {"path": manifest_path, "removed": [], "changed": False}

    removed = [k for k in identifiers if k != "alt_id"]
    if not removed:
        return {"path": manifest_path, "removed": [], "changed": False}

    kept = {k: v for k, v in identifiers.items() if k == "alt_id"}
    if kept:
        metadata["identifiers"] = kept
    else:
        del metadata["identifiers"]

    if not dry_run:
        reflow_manifest(manifest)
        manifest["hash"] = manifest_hash(manifest)
        manifest_path.write_text(dump(manifest), encoding="utf-8")

    return {"path": manifest_path, "removed": removed, "changed": True}
