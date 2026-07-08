"""Command-line entry point for ``bkk voice``.

Exposes two operations:

``add (--bundle <dir> | --text-id <id>)`` walks every juan file in the bundle
(master plus each documentary edition), derives ``voice`` range markers
from the markers already on disk, writes them back into each juan's
marker collection, and refreshes the juan and manifest hashes.

    python -m bkk voice add --bundle <out-root>/<text-id>/
    python -m bkk voice add --text-id <text-id>     # resolved via .bkkrc

Bare-id form resolves the bundle root against (in order)
``voice.out``, ``import.out``, ``global.corpus`` from ``.bkkrc``.

``--source`` selects the derivation:

- ``parens`` (default) — from ``(`` / ``)`` punctuation marker pairs,
  emits ``note`` voice spans for paren-bounded text. The deriver makes
  no claim about whether the paren span is commentary, gloss, or
  alternate reading — only that it's bracketed.
- ``indent`` — from ``line-break``/``indent`` markers, emits
  ``root``/``commentary``/``head``/``attribution`` for sources whose
  layout indents each textual layer differently.
- ``all`` — both derivers, concatenated. The two derivers use disjoint
  voice names (paren → ``note``; indent → ``root``/``commentary``/…),
  so same-name overlaps are impossible by construction; heterogeneous
  overlaps are written through with a per-juan stderr warning.

``--force`` strips any pre-existing ``voice`` markers and rederives;
without it the command refuses to touch a bundle that already carries
voice markers, so reruns are safe.

``--dry-run`` reports per-juan counts without writing.

``remove (--bundle <dir> | --text-id <id>)`` strips every ``voice`` marker from
each juan in the bundle (master and every edition) and refreshes the
affected juan and manifest hashes. It does not derive. Idempotent:
juans with no voice markers are left untouched. Useful for undoing a
bad ``add`` run before re-deriving with different options.
"""

from __future__ import annotations

import argparse
import copy
import re
import sys
from pathlib import Path

import yaml

from bkk.cli_common import resolve_bundle_dir, resolve_rc_path, warn_deprecated
from bkk.importer.hashing import manifest_hash, sha256_jcs, ZERO_HASH
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.marker_assets import hydrate_juan_markers, load_marker_asset
from bkk.short_refs import text_id_arg, text_or_path_arg

from .derive import derive_voice_markers
from .derive_indent import derive_voice_markers_from_indent


_VALID_SOURCES = ("parens", "indent", "all")


_JUAN_RE = re.compile(
    r"^(?P<text_id>.+?)_(?P<seq>\d{3})(?:-(?P<short>[A-Za-z0-9][A-Za-z0-9_-]*))?\.yaml$",
)
_BUCKETS = ("front", "body", "back")


def _add_bundle_selector(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "legacy_bundle", nargs="?", type=text_or_path_arg,
        help=argparse.SUPPRESS,
    )
    sp.add_argument("--bundle", dest="bundle", type=Path, default=None,
                    help="bundle directory")
    sp.add_argument(
        "--text-id", dest="text_id", type=text_id_arg, default=None,
        help="text id to resolve against voice.out / import.out / global.corpus",
    )
    sp.add_argument(
        "--out", dest="out_root", type=Path, default=None,
        help="bundle output root used to resolve --text-id "
             "(overrides voice.out / import.out / global.corpus)",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bkk voice")
    sub = p.add_subparsers(dest="op", required=True)
    pa = sub.add_parser(
        "add",
        help="derive voice markers from (...) punctuation pairs in each "
             "juan and write them back (master + every edition)",
    )
    _add_bundle_selector(pa)
    pa.add_argument(
        "--source", dest="source", choices=_VALID_SOURCES, default=None,
        help="derivation source: 'parens' (default; (…) pairs), "
             "'indent' (layout indentation), or 'all' (both, merged). "
             "Falls back to voice.source in .bkkrc; otherwise 'parens'.",
    )
    pa.add_argument(
        "--force", action="store_true",
        help="replace existing voice markers (default: refuse if any are present)",
    )
    pa.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="report what would be written without modifying files",
    )

    pr = sub.add_parser(
        "remove",
        help="strip every voice marker from each juan (master + every "
             "edition) and refresh juan and manifest hashes; does not derive",
    )
    _add_bundle_selector(pr)
    pr.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="report what would be removed without modifying files",
    )
    return p


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    out_root = args.out_root
    if out_root is None:
        from bkk.config import load_rc
        rc = load_rc()
        out_root = resolve_rc_path(
            None, rc,
            (("voice", "out"), ("import", "out"), ("global", "corpus")),
        )

    try:
        bundle, text_id = _selected_bundle_args(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.op == "remove":
        return _run_remove(
            bundle, out_root, text_id=text_id, dry_run=args.dry_run,
        )

    source = args.source
    if source is None:
        from bkk.config import load_rc
        rc = load_rc()
        source = rc.get("voice", {}).get("source") or "parens"
        if source not in _VALID_SOURCES:
            print(
                f"error: .bkkrc voice.source={source!r} not in "
                f"{list(_VALID_SOURCES)}",
                file=sys.stderr,
            )
            return 2

    return _run_add(
        bundle, out_root, text_id=text_id, source=source,
        force=args.force, dry_run=args.dry_run,
    )


def _selected_bundle_args(args: argparse.Namespace) -> tuple[str | Path | None, str | None]:
    supplied = [
        bool(getattr(args, "legacy_bundle", None)),
        bool(getattr(args, "bundle", None)),
        bool(getattr(args, "text_id", None)),
    ]
    if sum(supplied) != 1:
        raise ValueError("provide exactly one of --bundle or --text-id")
    if getattr(args, "legacy_bundle", None):
        legacy = args.legacy_bundle
        if "/" in legacy or "\\" in legacy or Path(legacy).is_dir():
            warn_deprecated("positional <bundle>", "--bundle <dir>")
            return legacy, None
        warn_deprecated("positional <text-id>", "--text-id <text-id>")
        return None, legacy
    return args.bundle, args.text_id


def _resolve_bundle_dir(
    bundle: str | Path | None,
    out_root: Path | None,
    *,
    text_id: str | None = None,
) -> Path:
    return resolve_bundle_dir(bundle=bundle, text_id=text_id, root=out_root)


def _run_add(
    bundle: str | Path | None,
    out_root,
    *,
    text_id: str | None = None,
    source: str,
    force: bool,
    dry_run: bool,
) -> int:
    try:
        bundle_dir = _resolve_bundle_dir(bundle, out_root, text_id=text_id)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    text_id = bundle_dir.name

    # The bundle is master + zero or more editions/<short>/ subdirs. Each
    # has its own manifest and its own juan files; voice markers are derived
    # per-juan from that juan's own punctuation markers (master and editions
    # diverge in marker counts when variants insert or delete characters).
    targets: list[tuple[Path, Path, str | None]] = []
    master_manifest = bundle_dir / f"{text_id}.manifest.yaml"
    if not master_manifest.exists():
        print(f"error: master manifest not found: {master_manifest}", file=sys.stderr)
        return 2
    targets.append((bundle_dir, master_manifest, None))

    editions_root = bundle_dir / "editions"
    if editions_root.is_dir():
        for sub in sorted(editions_root.iterdir()):
            if not sub.is_dir():
                continue
            mf = sub / f"{text_id}-{sub.name}.manifest.yaml"
            if mf.exists():
                targets.append((sub, mf, sub.name))

    overall_juans = 0
    overall_by_name: dict[str, int] = {}
    failed: list[str] = []
    for juan_dir, manifest_path, short in targets:
        scope = "master" if short is None else f"edition {short}"
        print(f"[{scope}]")
        try:
            stats = _process_one(
                juan_dir, manifest_path, text_id, short,
                source=source, force=force, dry_run=dry_run,
            )
        except (RuntimeError, ValueError) as exc:
            print(f"  error: {exc}", file=sys.stderr)
            print(f"  {scope} skipped; no files written for this scope")
            failed.append(scope)
            continue
        overall_juans += stats["juans"]
        for name, count in stats["by_name"].items():
            overall_by_name[name] = overall_by_name.get(name, 0) + count
        for line in stats["lines"]:
            print(line)

    verb = "would derive" if dry_run else "derived"
    summary = _format_voice_counts(overall_by_name) or "0 voice marker(s)"
    print(f"{verb} {summary} across {overall_juans} juan file(s)")
    if failed:
        print(f"skipped {len(failed)} scope(s) due to errors: {', '.join(failed)}", file=sys.stderr)
        return 1
    if not dry_run and overall_by_name:
        from bkk.repair.markers import externalize_markers
        externalize_markers(bundle_dir, dry_run=False)
    return 0


def _process_one(
    juan_dir: Path, manifest_path: Path, text_id: str, short: str | None,
    *, source: str, force: bool, dry_run: bool,
) -> dict:
    """Apply voice derivation to all juan files under ``juan_dir`` and update
    ``manifest_path``. Returns a small stats dict.

    When ``short`` is None this is the master scope; the regex filters to
    juan files without a ``-<short>`` suffix. Otherwise it matches only
    files with that exact suffix.
    """
    juan_entries: list[tuple[int, Path]] = []
    for entry in sorted(juan_dir.iterdir()):
        if not entry.is_file():
            continue
        name = entry.name
        if name.endswith(".manifest.yaml") or name.endswith(".ann.yaml"):
            continue
        m = _JUAN_RE.match(name)
        if not m or m.group("text_id") != text_id:
            continue
        if m.group("short") != short:
            continue
        juan_entries.append((int(m.group("seq")), entry))
    juan_entries.sort(key=lambda t: t[0])

    if not juan_entries:
        raise RuntimeError(f"no juan files found under {juan_dir}")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(manifest, dict):
        raise RuntimeError(f"{manifest_path.name}: manifest top level is not a mapping")

    lines: list[str] = []
    total_by_name: dict[str, int] = {}
    # First pass: derive everything in memory. If any juan/bucket fails, we
    # abort the scope without having written a single file.
    pending: list[tuple[Path, dict, str]] = []  # (path, juan_data, new_hash)

    for seq, juan_path in juan_entries:
        data = yaml.safe_load(juan_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError(f"{juan_path.name}: top-level YAML is not a mapping")
        data = hydrate_juan_markers(data, load_marker_asset(juan_dir, manifest, seq))

        existing = _existing_voice_count(data)
        if existing and not force:
            raise RuntimeError(
                f"{juan_path.name}: {existing} voice marker(s) already present "
                "(pass --force to replace)"
            )

        juan_by_name: dict[str, int] = {}
        for bucket_name in _BUCKETS:
            bucket = data.get(bucket_name)
            if not isinstance(bucket, dict):
                continue
            text = bucket.get("text") or ""
            markers = bucket.get("markers")
            if not isinstance(markers, list):
                continue
            if force and existing:
                markers = [
                    m for m in markers
                    if not (isinstance(m, dict) and m.get("type") == "voice")
                ]
            try:
                new_voices = _derive_for_bucket(source, len(text), markers)
            except ValueError as exc:
                raise ValueError(f"{juan_path.name} [{bucket_name}]: {exc}") from exc
            if source == "all":
                _warn_voice_overlaps(
                    new_voices, juan_path.name, bucket_name,
                )
            if not new_voices:
                if force and existing:
                    bucket["markers"] = [marker_to_flow(m) for m in markers]
                continue
            for v in new_voices:
                name = v["name"]
                juan_by_name[name] = juan_by_name.get(name, 0) + 1
            # Append voice markers then re-sort by (offset, original index)
            # — same rule the writer uses in importer.write.bundle.
            combined = list(markers) + new_voices
            indexed = list(enumerate(combined))
            indexed.sort(key=lambda p: (p[1]["offset"], p[0]))
            bucket["markers"] = [marker_to_flow(m) for _, m in indexed]

        if not juan_by_name:
            lines.append(f"  juan {seq:03d}: no voice signal; left as-is")
            continue

        for name, count in juan_by_name.items():
            total_by_name[name] = total_by_name.get(name, 0) + count
        lines.append(
            f"  juan {seq:03d}: {_format_voice_counts(juan_by_name)}"
        )

        new_hash = _juan_self_hash(data)
        data["hash"] = new_hash
        pending.append((juan_path, data, new_hash))

    # Second pass: writes only run once every juan in the scope has been
    # successfully derived and re-hashed.
    if not dry_run and pending:
        for juan_path, data, _ in pending:
            juan_path.write_text(dump(data), encoding="utf-8")
        new_hashes = {
            int(_JUAN_RE.match(p.name).group("seq")): h
            for p, _, h in pending
        }
        _update_manifest(manifest_path, new_hashes)

    return {
        "juans": len(juan_entries),
        "by_name": total_by_name,
        "lines": lines,
    }


def _run_remove(
    bundle: str | Path | None,
    out_root,
    *,
    text_id: str | None = None,
    dry_run: bool,
) -> int:
    try:
        bundle_dir = _resolve_bundle_dir(bundle, out_root, text_id=text_id)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    text_id = bundle_dir.name

    targets: list[tuple[Path, Path, str | None]] = []
    master_manifest = bundle_dir / f"{text_id}.manifest.yaml"
    if not master_manifest.exists():
        print(f"error: master manifest not found: {master_manifest}", file=sys.stderr)
        return 2
    targets.append((bundle_dir, master_manifest, None))

    editions_root = bundle_dir / "editions"
    if editions_root.is_dir():
        for sub in sorted(editions_root.iterdir()):
            if not sub.is_dir():
                continue
            mf = sub / f"{text_id}-{sub.name}.manifest.yaml"
            if mf.exists():
                targets.append((sub, mf, sub.name))

    overall_juans = 0
    overall_removed = 0
    failed: list[str] = []
    for juan_dir, manifest_path, short in targets:
        scope = "master" if short is None else f"edition {short}"
        print(f"[{scope}]")
        try:
            stats = _process_one_remove(
                juan_dir, manifest_path, text_id, short, dry_run=dry_run,
            )
        except (RuntimeError, ValueError) as exc:
            print(f"  error: {exc}", file=sys.stderr)
            print(f"  {scope} skipped; no files written for this scope")
            failed.append(scope)
            continue
        overall_juans += stats["juans"]
        overall_removed += stats["removed"]
        for line in stats["lines"]:
            print(line)

    verb = "would remove" if dry_run else "removed"
    print(
        f"{verb} {overall_removed} voice marker(s) "
        f"across {overall_juans} juan file(s)"
    )
    if failed:
        print(
            f"skipped {len(failed)} scope(s) due to errors: "
            f"{', '.join(failed)}",
            file=sys.stderr,
        )
        return 1
    if not dry_run and overall_removed:
        from bkk.repair.markers import externalize_markers
        externalize_markers(bundle_dir, dry_run=False)
    return 0


def _process_one_remove(
    juan_dir: Path, manifest_path: Path, text_id: str, short: str | None,
    *, dry_run: bool,
) -> dict:
    """Strip every voice marker from juan files under ``juan_dir`` and
    refresh ``manifest_path``. Returns a stats dict
    ``{"juans": N, "removed": K, "lines": [...]}``.

    ``short=None`` selects the master scope; otherwise matches only the
    juans tagged with that edition's short id.
    """
    juan_entries: list[tuple[int, Path]] = []
    for entry in sorted(juan_dir.iterdir()):
        if not entry.is_file():
            continue
        name = entry.name
        if name.endswith(".manifest.yaml") or name.endswith(".ann.yaml"):
            continue
        m = _JUAN_RE.match(name)
        if not m or m.group("text_id") != text_id:
            continue
        if m.group("short") != short:
            continue
        juan_entries.append((int(m.group("seq")), entry))
    juan_entries.sort(key=lambda t: t[0])

    if not juan_entries:
        raise RuntimeError(f"no juan files found under {juan_dir}")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(manifest, dict):
        raise RuntimeError(f"{manifest_path.name}: manifest top level is not a mapping")

    lines: list[str] = []
    total_removed = 0
    pending: list[tuple[Path, dict, str]] = []

    for seq, juan_path in juan_entries:
        data = yaml.safe_load(juan_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError(f"{juan_path.name}: top-level YAML is not a mapping")
        data = hydrate_juan_markers(data, load_marker_asset(juan_dir, manifest, seq))

        juan_removed = 0
        for bucket_name in _BUCKETS:
            bucket = data.get(bucket_name)
            if not isinstance(bucket, dict):
                continue
            markers = bucket.get("markers")
            if not isinstance(markers, list):
                continue
            kept: list = []
            for m in markers:
                if isinstance(m, dict) and m.get("type") == "voice":
                    juan_removed += 1
                    continue
                kept.append(m)
            if len(kept) != len(markers):
                bucket["markers"] = [marker_to_flow(m) for m in kept]

        if juan_removed == 0:
            lines.append(f"  juan {seq:03d}: no voice markers to remove")
            continue

        total_removed += juan_removed
        lines.append(
            f"  juan {seq:03d}: removed {juan_removed} voice marker(s)"
        )

        new_hash = _juan_self_hash(data)
        data["hash"] = new_hash
        pending.append((juan_path, data, new_hash))

    if not dry_run and pending:
        for juan_path, data, _ in pending:
            juan_path.write_text(dump(data), encoding="utf-8")
        new_hashes = {
            int(_JUAN_RE.match(p.name).group("seq")): h
            for p, _, h in pending
        }
        _update_manifest(manifest_path, new_hashes)

    return {
        "juans": len(juan_entries),
        "removed": total_removed,
        "lines": lines,
    }


def _format_voice_counts(by_name: dict[str, int]) -> str:
    """Render a per-name voice tally as ``"5 note + 3 root span(s)"``,
    sorted by descending count then by name for tie-stability.
    """
    if not by_name:
        return ""
    items = sorted(by_name.items(), key=lambda p: (-p[1], p[0]))
    inner = " + ".join(f"{count} {name}" for name, count in items)
    return f"{inner} span(s)"


def _derive_for_bucket(
    source: str, text_len: int, markers: list,
) -> list[dict]:
    """Dispatch to the requested deriver(s) and return their merged output.

    For ``--source all`` the two derivers' outputs are simply concatenated.
    Their voice-name spaces are disjoint (paren → ``note``; indent →
    ``root``/``commentary``/``head``/``attribution``), so same-name
    overlaps are impossible by construction and their id prefixes
    (``n`` vs ``r``/``c``/``h``/``a``) don't collide either.
    """
    if source == "parens":
        return derive_voice_markers(text_len, markers)
    if source == "indent":
        return derive_voice_markers_from_indent(text_len, markers)
    if source == "all":
        return list(derive_voice_markers(text_len, markers)) + list(
            derive_voice_markers_from_indent(text_len, markers)
        )
    raise ValueError(f"unknown voice source: {source!r}")


def _warn_voice_overlaps(
    voices: list[dict], juan_name: str, bucket_name: str,
) -> None:
    """Print a stderr warning per voice marker that overlaps another.

    Used under ``--source all`` to surface cases where a parens-derived
    span and an indent-derived span occupy overlapping offsets. Both
    markers are kept; the consumer chooses rendering policy.
    """
    spans = sorted(
        (
            (v["offset"], v["offset"] + v.get("length", 0), v.get("id"))
            for v in voices
            if isinstance(v, dict) and isinstance(v.get("offset"), int)
        ),
        key=lambda s: (s[0], s[1]),
    )
    for i, (a_start, a_end, a_id) in enumerate(spans):
        for b_start, b_end, b_id in spans[i + 1:]:
            if b_start >= a_end:
                break
            print(
                f"  warning: {juan_name} [{bucket_name}]: voice spans "
                f"{a_id} [{a_start},{a_end}) and {b_id} "
                f"[{b_start},{b_end}) overlap",
                file=sys.stderr,
            )


def _existing_voice_count(juan_data: dict) -> int:
    n = 0
    for bucket_name in _BUCKETS:
        bucket = juan_data.get(bucket_name)
        if not isinstance(bucket, dict):
            continue
        for m in bucket.get("markers") or []:
            if isinstance(m, dict) and m.get("type") == "voice":
                n += 1
    return n


def _juan_self_hash(juan_dict: dict) -> str:
    m = copy.deepcopy(juan_dict)
    m["hash"] = ZERO_HASH
    return sha256_jcs(m)


def _update_manifest(manifest_path: Path, new_hashes: dict[int, str]) -> None:
    """Patch ``assets.parts[*].hash`` for each updated juan, then recompute
    the manifest's self-hash and rewrite the file."""
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"{manifest_path.name}: not a mapping")
    assets = data.get("assets")
    if not isinstance(assets, dict):
        raise RuntimeError(f"{manifest_path.name}: missing assets block")
    parts = assets.get("parts")
    if not isinstance(parts, list):
        raise RuntimeError(f"{manifest_path.name}: assets.parts missing or not a list")

    new_parts: list = []
    for entry in parts:
        if not isinstance(entry, dict):
            new_parts.append(entry)
            continue
        seq = entry.get("seq")
        if isinstance(seq, int) and seq in new_hashes:
            entry = dict(entry)
            entry["hash"] = new_hashes[seq]
        new_parts.append(marker_to_flow(entry))
    data["assets"]["parts"] = new_parts
    # Voice operations hydrate external markers into the physical juan before
    # editing. Clear stale marker-asset declarations; the follow-up
    # externalize pass rebuilds them from the edited effective marker lists.
    data["assets"].pop("markers", None)
    data["hash"] = manifest_hash(data)
    manifest_path.write_text(dump(data), encoding="utf-8")


def main() -> None:
    raise SystemExit(run())
