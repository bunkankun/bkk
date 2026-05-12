"""Command-line entry point for ``bkk voice``.

Currently exposes one operation: ``add <bundle-dir-or-text-id>``, which
walks every juan file in the bundle (master plus each documentary
edition), derives ``voice`` range markers from the ``(`` / ``)``
punctuation marker pairs already on disk, writes them back into each
juan's marker collection, and refreshes the juan and manifest hashes.

    python -m bkk voice add <out-root>/<text-id>/
    python -m bkk voice add <text-id>     # resolved via .bkkrc

Bare-id form resolves the bundle root against (in order)
``voice.out``, ``import.out``, ``global.corpus`` from ``.bkkrc``.

``--force`` strips any pre-existing ``voice`` markers and rederives;
without it the command refuses to touch a bundle that already carries
voice markers, so reruns are safe.

``--dry-run`` reports per-juan counts without writing.
"""

from __future__ import annotations

import argparse
import copy
import re
import sys
from pathlib import Path

import yaml

from bkk.importer.hashing import manifest_hash, sha256_jcs, ZERO_HASH
from bkk.importer.write.yaml_writer import dump, marker_to_flow

from .derive import derive_voice_markers


_JUAN_RE = re.compile(
    r"^(?P<text_id>.+?)_(?P<seq>\d{3})(?:-(?P<short>[A-Za-z0-9]+))?\.yaml$",
)
_BUCKETS = ("front", "body", "back")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bkk voice")
    sub = p.add_subparsers(dest="op", required=True)
    pa = sub.add_parser(
        "add",
        help="derive voice markers from (...) punctuation pairs in each "
             "juan and write them back (master + every edition)",
    )
    pa.add_argument(
        "bundle", type=str,
        help="bundle directory, or a bare text-id resolved against "
             "voice.out / import.out / global.corpus from .bkkrc",
    )
    pa.add_argument(
        "--out", dest="out_root", type=Path, default=None,
        help="bundle output root used to resolve a bare text-id "
             "(overrides voice.out / import.out / global.corpus)",
    )
    pa.add_argument(
        "--force", action="store_true",
        help="replace existing voice markers (default: refuse if any are present)",
    )
    pa.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="report what would be written without modifying files",
    )
    return p


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    out_root = args.out_root
    if out_root is None:
        from bkk.config import load_rc
        rc = load_rc()
        out_root = (
            rc.get("voice", {}).get("out")
            or rc.get("import", {}).get("out")
            or rc.get("global", {}).get("corpus")
        )

    return _run_add(args.bundle, out_root, force=args.force, dry_run=args.dry_run)


def _resolve_bundle_dir(bundle: str, out_root: Path | None) -> Path:
    p = Path(bundle).expanduser()
    if p.is_dir():
        return p.resolve()
    if out_root is not None and "/" not in bundle and "\\" not in bundle:
        candidate = (Path(out_root).expanduser() / bundle).resolve()
        if candidate.is_dir():
            return candidate
        raise FileNotFoundError(
            f"bundle directory not found: tried {p} and {candidate}"
        )
    raise FileNotFoundError(f"bundle directory not found: {p}")


def _run_add(bundle: str, out_root, *, force: bool, dry_run: bool) -> int:
    try:
        bundle_dir = _resolve_bundle_dir(bundle, out_root)
    except FileNotFoundError as exc:
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
    overall_root = 0
    overall_cmt = 0
    failed: list[str] = []
    for juan_dir, manifest_path, short in targets:
        scope = "master" if short is None else f"edition {short}"
        print(f"[{scope}]")
        try:
            stats = _process_one(
                juan_dir, manifest_path, text_id, short,
                force=force, dry_run=dry_run,
            )
        except (RuntimeError, ValueError) as exc:
            print(f"  error: {exc}", file=sys.stderr)
            print(f"  {scope} skipped; no files written for this scope")
            failed.append(scope)
            continue
        overall_juans += stats["juans"]
        overall_root += stats["root"]
        overall_cmt += stats["commentary"]
        for line in stats["lines"]:
            print(line)

    verb = "would derive" if dry_run else "derived"
    print(
        f"{verb} {overall_cmt} commentary + {overall_root} root voice "
        f"marker(s) across {overall_juans} juan file(s)"
    )
    if failed:
        print(f"skipped {len(failed)} scope(s) due to errors: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


def _process_one(
    juan_dir: Path, manifest_path: Path, text_id: str, short: str | None,
    *, force: bool, dry_run: bool,
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

    lines: list[str] = []
    total_root = 0
    total_cmt = 0
    # First pass: derive everything in memory. If any juan/bucket fails, we
    # abort the scope without having written a single file.
    pending: list[tuple[Path, dict, str]] = []  # (path, juan_data, new_hash)

    for seq, juan_path in juan_entries:
        data = yaml.safe_load(juan_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError(f"{juan_path.name}: top-level YAML is not a mapping")

        existing = _existing_voice_count(data)
        if existing and not force:
            raise RuntimeError(
                f"{juan_path.name}: {existing} voice marker(s) already present "
                "(pass --force to replace)"
            )

        n_root = 0
        n_cmt = 0
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
                new_voices = derive_voice_markers(len(text), markers)
            except ValueError as exc:
                raise ValueError(f"{juan_path.name} [{bucket_name}]: {exc}") from exc
            if not new_voices:
                if force and existing:
                    bucket["markers"] = [marker_to_flow(m) for m in markers]
                continue
            for v in new_voices:
                if v["name"] == "root":
                    n_root += 1
                else:
                    n_cmt += 1
            # Append voice markers then re-sort by (offset, original index)
            # — same rule the writer uses in importer.write.bundle.
            combined = list(markers) + new_voices
            indexed = list(enumerate(combined))
            indexed.sort(key=lambda p: (p[1]["offset"], p[0]))
            bucket["markers"] = [marker_to_flow(m) for _, m in indexed]

        if n_root == 0 and n_cmt == 0:
            lines.append(f"  juan {seq:03d}: no commentary brackets; left as-is")
            continue

        total_root += n_root
        total_cmt += n_cmt
        lines.append(
            f"  juan {seq:03d}: {n_cmt} commentary span(s), "
            f"{n_root} root span(s)"
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
        "root": total_root,
        "commentary": total_cmt,
        "lines": lines,
    }


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
    data["hash"] = manifest_hash(data)
    manifest_path.write_text(dump(data), encoding="utf-8")


def main() -> None:
    raise SystemExit(run())
