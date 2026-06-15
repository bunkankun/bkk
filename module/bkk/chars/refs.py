"""Load BKK reference assets (canonical character set + substitution mappings).

The canonicalizer needs three pieces of information at run time:

- the inclusion blocks of the canonical character set, to decide whether a
  given codepoint is admissible without substitution;
- the set of excluded codepoints declared by the charset, even when they
  fall inside an inclusion block;
- a substitution mapping that resolves each excluded codepoint to its
  canonical replacement, plus the mapping's identifier and hash so that
  the resulting markers and the manifest can pin the mapping by version.

This module loads the YAML files shipped under ``module/refs/`` and packs
the relevant data into a :class:`CanonicalizationContext`. Inclusion-block
membership is also exposed via :func:`in_inclusion_block` so that callers
can flag any codepoint that is outside the charset and has no mapping
entry (an error in v1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from bkk.importer.hashing import ZERO_HASH, sha256_jcs


DEFAULT_REFS_DIR = Path(__file__).resolve().parents[2] / "refs"
DEFAULT_CHARSET_FILENAME = "bkk-charset-cjk-v1.yaml"
DEFAULT_MAPPING_FILENAMES = ("bkk-mapping-variant-fold-v1.yaml",)


@dataclass
class MappingEntry:
    entry_id: str
    replacement_cp: int
    reason: str
    mapping_index: int  # which mapping in ctx.mappings produced the entry


@dataclass
class MappingAsset:
    canonical_identifier: str
    hash: str
    filename: str


@dataclass
class CanonicalizationContext:
    charset_id: str
    charset_hash: str
    charset_filename: str
    inclusion_blocks: list[tuple[int, int]]
    excluded: dict[int, dict[str, Any]]
    mappings: list[MappingAsset]
    mapping_entries: dict[int, MappingEntry] = field(default_factory=dict)

    def in_inclusion_block(self, cp: int) -> bool:
        for lo, hi in self.inclusion_blocks:
            if lo <= cp <= hi:
                return True
        return False


def _parse_codepoint(s: str | int) -> int:
    if isinstance(s, int):
        return s
    s = str(s).strip()
    if s.startswith("U+") or s.startswith("u+"):
        return int(s[2:], 16)
    return int(s, 0)


def _self_hash(data: dict) -> str:
    payload = dict(data)
    payload["hash"] = ZERO_HASH
    return sha256_jcs(payload)


def load_charset(path: Path) -> tuple[str, str, list[tuple[int, int]], dict[int, dict[str, Any]]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"{path.name}: not a mapping")

    charset_id = data.get("canonical_identifier")
    if not isinstance(charset_id, str):
        raise RuntimeError(f"{path.name}: missing canonical_identifier")

    declared = data.get("hash")
    computed = _self_hash(data)
    if isinstance(declared, str) and declared != ZERO_HASH and declared != computed:
        raise RuntimeError(
            f"{path.name}: declared hash {declared} does not match computed {computed}"
        )

    blocks: list[tuple[int, int]] = []
    for entry in data.get("inclusion_blocks") or []:
        if not isinstance(entry, dict):
            continue
        rng = entry.get("range")
        if not isinstance(rng, list) or len(rng) != 2:
            continue
        blocks.append((_parse_codepoint(rng[0]), _parse_codepoint(rng[1])))

    excluded: dict[int, dict[str, Any]] = {}
    for entry in data.get("excluded") or []:
        if not isinstance(entry, dict):
            continue
        cp = _parse_codepoint(entry["cp"])
        excluded[cp] = {
            "char": entry.get("char"),
            "reason": entry.get("reason"),
            "replaced_by": _parse_codepoint(entry["replaced_by"])
                if entry.get("replaced_by") is not None else None,
        }

    return charset_id, computed, blocks, excluded


def load_mapping(path: Path, mapping_index: int) -> tuple[MappingAsset, dict[int, MappingEntry]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"{path.name}: not a mapping")

    mapping_id = data.get("canonical_identifier")
    if not isinstance(mapping_id, str):
        raise RuntimeError(f"{path.name}: missing canonical_identifier")

    declared = data.get("hash")
    computed = _self_hash(data)
    if isinstance(declared, str) and declared != ZERO_HASH and declared != computed:
        raise RuntimeError(
            f"{path.name}: declared hash {declared} does not match computed {computed}"
        )

    entries: dict[int, MappingEntry] = {}
    for entry in data.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        entry_id = entry.get("id")
        source = entry.get("source") or {}
        replacement = entry.get("replacement") or {}
        reason = entry.get("reason") or ""
        if not isinstance(entry_id, str):
            continue
        if "cp" not in source or "cp" not in replacement:
            continue
        source_cp = _parse_codepoint(source["cp"])
        replacement_cp = _parse_codepoint(replacement["cp"])
        if source_cp in entries:
            raise RuntimeError(
                f"{path.name}: duplicate mapping entry for U+{source_cp:04X}"
            )
        entries[source_cp] = MappingEntry(
            entry_id=entry_id,
            replacement_cp=replacement_cp,
            reason=str(reason),
            mapping_index=mapping_index,
        )

    asset = MappingAsset(
        canonical_identifier=mapping_id,
        hash=computed,
        filename=path.name,
    )
    return asset, entries


def load_context(
    refs_dir: Path | None = None,
    *,
    charset_filename: str = DEFAULT_CHARSET_FILENAME,
    mapping_filenames: tuple[str, ...] = DEFAULT_MAPPING_FILENAMES,
) -> CanonicalizationContext:
    """Load the charset + mapping(s) referenced by their filenames."""
    refs_dir = Path(refs_dir).resolve() if refs_dir else DEFAULT_REFS_DIR
    if not refs_dir.is_dir():
        raise FileNotFoundError(f"refs dir not found: {refs_dir}")

    charset_path = refs_dir / charset_filename
    if not charset_path.is_file():
        raise FileNotFoundError(f"charset not found: {charset_path}")
    charset_id, charset_hash, blocks, excluded = load_charset(charset_path)

    mappings: list[MappingAsset] = []
    mapping_entries: dict[int, MappingEntry] = {}
    for i, fn in enumerate(mapping_filenames):
        path = refs_dir / fn
        if not path.is_file():
            raise FileNotFoundError(f"mapping not found: {path}")
        asset, entries = load_mapping(path, mapping_index=i)
        mappings.append(asset)
        for cp, entry in entries.items():
            if cp in mapping_entries:
                raise RuntimeError(
                    f"{fn}: codepoint U+{cp:04X} already mapped by "
                    f"{mappings[mapping_entries[cp].mapping_index].filename}"
                )
            mapping_entries[cp] = entry

    return CanonicalizationContext(
        charset_id=charset_id,
        charset_hash=charset_hash,
        charset_filename=charset_filename,
        inclusion_blocks=blocks,
        excluded=excluded,
        mappings=mappings,
        mapping_entries=mapping_entries,
    )
