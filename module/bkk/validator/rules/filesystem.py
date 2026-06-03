"""Section A: filesystem / structural rules."""

from __future__ import annotations

import re

from ..context import ValidationContext

_JUAN_FNAME_RE = re.compile(r"^(?P<text_id>[A-Za-z0-9]+)_(?P<seq>\d+)\.yaml$")


def run(ctx: ValidationContext) -> None:
    _check_master_manifest(ctx)
    _check_referenced_files(ctx)
    _check_orphan_juans(ctx)
    _check_editions(ctx)
    _check_pua_map_location(ctx)


def _check_master_manifest(ctx: ValidationContext) -> None:
    m = ctx.master_manifest
    if not m.exists:
        ctx.report.add(
            "MANIFEST_MISSING", "error", m.rel,
            "master manifest not found",
        )
        return
    if m.parse_error is not None:
        ctx.report.add(
            "MANIFEST_PARSE", "error", m.rel,
            f"YAML parse error: {m.parse_error}",
        )
        return
    if not isinstance(m.data, dict):
        ctx.report.add(
            "MANIFEST_PARSE", "error", m.rel,
            "manifest top level is not a mapping",
        )
        return
    # Bundle dir name should match canonical_identifier text_id segment if present.
    cid = m.data.get("canonical_identifier")
    if isinstance(cid, str):
        # bkk:krp/<text-id>/v1
        parts = cid.split("/")
        if len(parts) >= 3 and parts[1] != ctx.text_id:
            ctx.report.add(
                "BUNDLE_DIR_NAME", "error", m.rel,
                f"manifest text-id '{parts[1]}' does not match bundle directory name '{ctx.text_id}'",
            )


def _check_referenced_files(ctx: ValidationContext) -> None:
    for seq, lf in ctx.master_juans.items():
        if not lf.exists:
            ctx.report.add(
                "JUAN_FILE_MISSING", "error", lf.rel,
                f"juan file referenced by manifest (seq={seq}) is missing",
            )
    for seq, lf in ctx.marker_assets.items():
        if not lf.exists:
            ctx.report.add(
                "MARKER_ASSET_MISSING", "error", lf.rel,
                f"marker asset referenced by manifest (seq={seq}) is missing",
            )


def _check_orphan_juans(ctx: ValidationContext) -> None:
    referenced = {lf.path.name for lf in ctx.master_juans.values()}
    for entry in ctx.bundle_dir.iterdir():
        if not entry.is_file():
            continue
        name = entry.name
        m = _JUAN_FNAME_RE.match(name)
        if not m:
            continue
        if m.group("text_id") != ctx.text_id:
            continue
        if name not in referenced:
            ctx.report.add(
                "JUAN_FILE_ORPHAN", "warning", name,
                "juan file present but not referenced in manifest assets.parts",
            )


def _check_editions(ctx: ValidationContext) -> None:
    declared: dict[str, dict] = {}
    if isinstance(ctx.master_manifest.data, dict):
        for ed in ctx.master_manifest.data.get("editions") or []:
            if isinstance(ed, dict) and isinstance(ed.get("short"), str):
                declared[ed["short"]] = ed

    present = set(ctx.editions.keys())
    declared_set = set(declared.keys())

    for short in declared_set - present:
        ctx.report.add(
            "EDITION_DECLARED_NOT_PRESENT", "error",
            ctx.master_manifest.rel,
            f"edition '{short}' declared in manifest but no editions/{short}/ directory",
        )
    for short in present - declared_set:
        # KRP shape declares editions on master; TLS shape doesn't (master and
        # the sole witness share content). Only flag when the master *does*
        # declare any editions — otherwise this is the TLS layout.
        if declared_set:
            ctx.report.add(
                "EDITION_PRESENT_NOT_DECLARED", "error",
                f"editions/{short}/",
                f"edition directory present but not declared in master manifest editions[]",
            )

    master_seqs = set(ctx.master_juans.keys())
    for short, ed in ctx.editions.items():
        if not ed.manifest.exists:
            ctx.report.add(
                "EDITION_MANIFEST_MISSING", "error", ed.manifest.rel,
                f"edition manifest not found for '{short}'",
            )
            continue
        if ed.manifest.parse_error is not None:
            ctx.report.add(
                "MANIFEST_PARSE", "error", ed.manifest.rel,
                f"YAML parse error: {ed.manifest.parse_error}",
            )
            continue
        # Edition juan files referenced exist?
        for seq, lf in ed.juans.items():
            if not lf.exists:
                ctx.report.add(
                    "JUAN_FILE_MISSING", "error", lf.rel,
                    f"edition juan file referenced (short={short}, seq={seq}) is missing",
                )
        for seq, lf in ed.marker_assets.items():
            if not lf.exists:
                ctx.report.add(
                    "MARKER_ASSET_MISSING", "error", lf.rel,
                    f"edition marker asset referenced (short={short}, seq={seq}) is missing",
                )
        # Coverage: edition seq set must equal master seq set.
        ed_seqs = set(ed.juans.keys())
        if ed_seqs != master_seqs:
            missing = sorted(master_seqs - ed_seqs)
            extra = sorted(ed_seqs - master_seqs)
            details = []
            if missing:
                details.append(f"missing master seq(s): {missing}")
            if extra:
                details.append(f"extra seq(s): {extra}")
            ctx.report.add(
                "EDITION_JUAN_COVERAGE", "error", ed.manifest.rel,
                f"edition '{short}' juan coverage mismatch — " + "; ".join(details),
            )


def _check_pua_map_location(ctx: ValidationContext) -> None:
    editions_dir = ctx.bundle_dir / "editions"
    if not editions_dir.is_dir():
        return
    for sub in editions_dir.iterdir():
        if sub.is_dir() and (sub / "PUA-map.yaml").exists():
            ctx.report.add(
                "PUAMAP_LOCATION", "warning",
                str((sub / "PUA-map.yaml").relative_to(ctx.bundle_dir)),
                "PUA-map.yaml should live at the bundle root, not under editions/",
            )
