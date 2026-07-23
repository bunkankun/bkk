"""Command-line entry point for ``bkk voice``.

Exposes two operations:

``add (--bundle <dir> | --text-id <id> | --text-prefix <prefix>)`` walks
every juan file in the selected bundle(s) (master plus each documentary
edition), derives ``voice`` range markers from the markers already on disk,
writes the derived markers into each juan's marker asset, and refreshes
marker-asset and manifest hashes.

    python -m bkk voice add --bundle <out-root>/<text-id>/
    python -m bkk voice add --text-id <text-id>     # resolved via .bkkrc
    python -m bkk voice add --text-prefix KR6q      # resolved via .bkkrc

Bare-id and prefix forms resolve the bundle root against ``global.corpus``
from ``.bkkrc`` unless ``--out`` is passed.

``--source`` selects the derivation:

- ``parens`` (default) — from source punctuation marker pairs, emits
  ``note`` spans for ``(``…``)`` text and ``emphasis`` spans for
  ``▲``…``)`` text. The deriver makes no claim about whether a note span
  is commentary, gloss, or alternate reading — only that it's bracketed.
- ``indent`` — from ``line-break``/``indent`` markers, emits
  ``root``/``commentary``/``head``/``attribution`` for sources whose
  layout indents each textual layer differently.
- ``dictionary`` — detects dictionary explanation spans that contain the
  lemma-repeat placeholder ``丨`` and emits ``note`` spans carrying lemma
  metadata for ``bkk chars lemma-repeat apply``.
- ``all`` — both derivers, concatenated. The two derivers use disjoint
  voice names (parens → ``note``/``emphasis``; indent →
  ``root``/``commentary``/…), so same-name overlaps are impossible by
  construction; heterogeneous overlaps are written through with a
  per-juan stderr warning.

For paren derivation, TLS inline note bracket markers are included by
default; pass ``--no-tls-notes`` to ignore ``tls:note-start`` /
``tls:note-end`` markers and derive only from punctuation markers.

``--force`` strips any pre-existing ``voice`` markers and rederives;
without it the command refuses to touch a bundle that already carries
voice markers, so reruns are safe.

``--dry-run`` reports per-juan counts without writing.

``remove (--bundle <dir> | --text-id <id> | --text-prefix <prefix>)`` strips
every ``voice`` marker from each juan in the selected bundle(s) (master and
every edition) and refreshes the affected juan and manifest hashes. It does
not derive. Idempotent: juans with no voice markers are left untouched. Useful
for undoing a bad ``add`` run before re-deriving with different options.
"""

from __future__ import annotations

import argparse
import copy
import re
import sys
from pathlib import Path

import yaml

from bkk.cli_common import (
    add_text_prefix,
    resolve_bundle_dir,
    resolve_rc_path,
    warn_deprecated,
)
from bkk.importer.hashing import manifest_hash, sha256_jcs, ZERO_HASH
from bkk.importer.idassigner import allocate_marker_ids
from bkk.importer.write.yaml_writer import dump, marker_to_flow
from bkk.index.merge import discover_bundles
from bkk.marker_assets import (
    VALID_BUCKETS,
    build_marker_asset,
    effective_markers_for_bucket,
    external_markers_for_bucket,
    hydrate_juan_markers,
    inline_markers_for_bucket,
    load_marker_asset,
    marker_asset_entry_for_seq,
    marker_asset_filename,
)
from bkk.short_refs import text_id_arg, text_or_path_arg

from .derive import VoiceDerivationProblem, derive_voice_markers
from .derive_dictionary import derive_dictionary_voice_markers
from .derive_indent import derive_voice_markers_from_indent
from .problems import (
    VoiceProblemReportError,
    find_voice_problems,
    update_voice_problems_report,
    write_voice_problems_report,
)


_VALID_SOURCES = ("parens", "indent", "dictionary", "all")
_VOICE_PROBLEM_TYPE = "voice:problem"


_JUAN_RE = re.compile(
    r"^(?P<text_id>.+?)_(?P<seq>\d{3})(?:-(?P<short>[A-Za-z0-9][A-Za-z0-9_-]*))?\.yaml$",
)
_BUCKETS = ("front", "body", "back")
_YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _yaml_load_text(text: str):
    return yaml.load(text, Loader=_YAML_LOADER)


def _add_bundle_selector(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "legacy_bundle", nargs="?", type=text_or_path_arg,
        help=argparse.SUPPRESS,
    )
    sp.add_argument("--bundle", dest="bundle", type=Path, default=None,
                    help="bundle directory")
    sp.add_argument(
        "--text-id", dest="text_id", type=text_id_arg, default=None,
        help="text id to resolve against global.corpus",
    )
    add_text_prefix(
        sp,
        help="restrict to text ids starting with this prefix (resolved against global.corpus)",
    )
    sp.add_argument(
        "--out", dest="out_root", type=Path, default=None,
        help="bundle output root used to resolve --text-id/--text-prefix "
             "(overrides global.corpus)",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bkk voice")
    sub = p.add_subparsers(dest="op", required=True)
    pa = sub.add_parser(
        "add",
        help="derive voice markers from source punctuation pairs in each "
             "juan and write them back (master + every edition)",
    )
    _add_bundle_selector(pa)
    pa.add_argument(
        "--source", dest="source", choices=_VALID_SOURCES, default=None,
        help="derivation source: 'parens' (default; punctuation pairs), "
             "'indent' (layout indentation), 'dictionary' (lemma-repeat "
             "dictionary notes), or 'all' (parens + indent, merged). "
             "Falls back to voice.source in .bkkrc; otherwise 'parens'.",
    )
    pa.add_argument(
        "--tls-notes",
        dest="tls_notes",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="include tls:note-start/tls:note-end paren markers in parens "
             "derivation (default: true; falls back to voice.tls_notes in "
             ".bkkrc)",
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
    pp = sub.add_parser(
        "problems",
        help="write a precomputed report of persisted voice:problem markers",
    )
    pp.add_argument(
        "legacy_corpus", nargs="?", type=Path,
        help=argparse.SUPPRESS,
    )
    pp.add_argument(
        "--corpus", dest="corpus", type=Path, default=None,
        help="corpus root to scan (defaults to global.corpus)",
    )
    pp.add_argument(
        "--text-id", dest="text_id", type=text_id_arg, default=None,
        help="restrict the report to one bundle under the corpus root",
    )
    add_text_prefix(
        pp,
        help="restrict the report to text ids starting with this prefix",
    )
    pp.add_argument(
        "--out", dest="report", type=Path, default=None,
        help="report path (defaults to [voice].report or BKK_VOICE_PROBLEMS_REPORT)",
    )
    return p


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    out_root = getattr(args, "out_root", None)
    if out_root is None:
        from bkk.config import load_rc
        rc = load_rc()
        out_root = resolve_rc_path(
            None, rc,
            (("global", "corpus"),),
        )

    if args.op == "problems":
        return _run_problems(args, out_root)

    try:
        bundle, text_id, text_prefix = _selected_bundle_args(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.op == "remove":
        if text_prefix is not None:
            return _run_remove(
                bundle, out_root, text_id=text_id, text_prefix=text_prefix,
                dry_run=args.dry_run,
            )
        return _run_remove(
            bundle, out_root, text_id=text_id, dry_run=args.dry_run,
        )

    source = args.source
    tls_notes = args.tls_notes
    if tls_notes is None:
        from bkk.config import load_rc
        rc = load_rc()
        try:
            tls_notes = _rc_bool(rc, "tls_notes", default=True)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
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

    if text_prefix is not None:
        return _run_add(
            bundle, out_root, text_id=text_id, text_prefix=text_prefix,
            source=source, force=args.force, dry_run=args.dry_run,
            include_tls_notes=tls_notes,
        )
    return _run_add(
        bundle, out_root, text_id=text_id, source=source, force=args.force,
        dry_run=args.dry_run, include_tls_notes=tls_notes,
    )


def _selected_bundle_args(
    args: argparse.Namespace,
) -> tuple[str | Path | None, str | None, str | None]:
    supplied = [
        bool(getattr(args, "legacy_bundle", None)),
        bool(getattr(args, "bundle", None)),
        bool(getattr(args, "text_id", None)),
        bool(getattr(args, "text_prefix", None)),
    ]
    if sum(supplied) != 1:
        raise ValueError("provide exactly one of --bundle, --text-id, or --text-prefix")
    if getattr(args, "legacy_bundle", None):
        legacy = args.legacy_bundle
        if "/" in legacy or "\\" in legacy or Path(legacy).is_dir():
            warn_deprecated("positional <bundle>", "--bundle <dir>")
            return legacy, None, None
        warn_deprecated("positional <text-id>", "--text-id <text-id>")
        return None, legacy, None
    return args.bundle, args.text_id, args.text_prefix


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
    text_prefix: str | None = None,
    source: str,
    force: bool,
    dry_run: bool,
    include_tls_notes: bool = True,
) -> int:
    if text_prefix is not None:
        try:
            bundle_dirs = _resolve_bundle_dirs_for_prefix(out_root, text_prefix)
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        rc = 0
        for bundle_dir in bundle_dirs:
            print(f"[bundle {bundle_dir.name}]")
            bundle_rc = _run_add(
                bundle_dir, out_root, source=source, force=force,
                dry_run=dry_run, include_tls_notes=include_tls_notes,
            )
            if bundle_rc:
                rc = 1 if rc == 0 else rc
        return rc

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
    overall_problems = 0
    problem_rows: list[dict] = []
    failed: list[str] = []
    for juan_dir, manifest_path, short in targets:
        scope = "master" if short is None else f"edition {short}"
        print(f"[{scope}]")
        try:
            stats = _process_one(
                juan_dir, manifest_path, text_id, short,
                source=source, force=force, dry_run=dry_run,
                include_tls_notes=include_tls_notes,
            )
        except (RuntimeError, ValueError) as exc:
            print(f"  error: {exc}", file=sys.stderr)
            print(f"  {scope} skipped; no files written for this scope")
            failed.append(scope)
            continue
        overall_juans += stats["juans"]
        overall_problems += stats.get("problems", 0)
        problem_rows.extend(stats.get("problem_rows", []))
        for name, count in stats["by_name"].items():
            overall_by_name[name] = overall_by_name.get(name, 0) + count
        for line in stats["lines"]:
            print(line)

    verb = "would derive" if dry_run else "derived"
    summary = _format_voice_counts(overall_by_name) or "0 voice marker(s)"
    print(f"{verb} {summary} across {overall_juans} juan file(s)")
    if overall_problems:
        problem_verb = "would mark" if dry_run else "marked"
        print(
            f"{problem_verb} {overall_problems} unresolved voice problem(s)",
            file=sys.stderr,
        )
    if failed:
        print(f"skipped {len(failed)} scope(s) due to errors: {', '.join(failed)}", file=sys.stderr)
        return 1
    if not dry_run:
        report_path = _configured_voice_report_path()
        if report_path is not None:
            try:
                update_voice_problems_report(
                    report_path, text_id=text_id, rows=problem_rows,
                )
            except (OSError, VoiceProblemReportError) as exc:
                print(
                    f"error: could not update voice problem report {report_path}: {exc}",
                    file=sys.stderr,
                )
                return 1
            print(f"updated voice problem report: {report_path}")
    if overall_problems:
        return 1
    return 0


def _run_problems(args: argparse.Namespace, out_root: Path | None) -> int:
    if args.text_id and args.text_prefix:
        print("error: provide at most one of --text-id or --text-prefix", file=sys.stderr)
        return 2

    corpus = args.corpus or args.legacy_corpus or out_root
    if corpus is None:
        print(
            "error: corpus root is required: pass --corpus or configure global.corpus",
            file=sys.stderr,
        )
        return 2
    corpus = Path(corpus)
    if not corpus.is_dir():
        print(f"error: corpus root not found: {corpus}", file=sys.stderr)
        return 2

    report_path = args.report or _configured_voice_report_path()
    if report_path is None:
        print(
            "error: report path is required: pass --out, set [voice].report, "
            "or set BKK_VOICE_PROBLEMS_REPORT",
            file=sys.stderr,
        )
        return 2

    try:
        rows = find_voice_problems(
            corpus, text_id=args.text_id, text_prefix=args.text_prefix,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    write_voice_problems_report(rows, report_path)
    print(f"wrote {len(rows)} voice problem(s) to {report_path}")
    return 0


def _configured_voice_report_path() -> Path | None:
    import os

    env = os.environ.get("BKK_VOICE_PROBLEMS_REPORT")
    if env:
        return Path(env).resolve()
    from bkk.config import load_rc
    rc = load_rc()
    report = (rc.get("voice") or {}).get("report")
    if report:
        return Path(report).resolve()
    return None


def _rc_bool(rc: dict, key: str, *, default: bool) -> bool:
    value = (rc.get("voice") or {}).get(key, default)
    if isinstance(value, bool):
        return value
    raise ValueError(f".bkkrc voice.{key}={value!r} must be true or false")


def _process_one(
    juan_dir: Path, manifest_path: Path, text_id: str, short: str | None,
    *, source: str, force: bool, dry_run: bool, include_tls_notes: bool = True,
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
    manifest = _yaml_load_text(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(manifest, dict):
        raise RuntimeError(f"{manifest_path.name}: manifest top level is not a mapping")
    title = ((manifest.get("metadata") or {}).get("title"))
    title = title if isinstance(title, str) else None

    lines: list[str] = []
    total_by_name: dict[str, int] = {}
    total_problems = 0
    problem_rows: list[dict] = []
    # First pass: derive everything in memory. If any juan/bucket fails, we
    # record a location marker and keep processing the rest of the scope.
    pending_juans: list[tuple[Path, dict, str]] = []
    pending_assets: list[tuple[Path, dict, str, str]] = []
    occupied_ids: set[str] = set()

    for seq, juan_path in juan_entries:
        data = _yaml_load_text(juan_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError(f"{juan_path.name}: top-level YAML is not a mapping")
        marker_asset = load_marker_asset(juan_dir, manifest, seq)

        existing = _existing_voice_count(data, marker_asset, source=source)
        if existing and not force:
            raise RuntimeError(
                f"{juan_path.name}: {existing} voice marker(s) already present "
                "(pass --force to replace)"
            )

        juan_by_name: dict[str, int] = {}
        juan_problems = 0
        juan_changed = False
        asset_changed = False
        asset_markers_by_bucket = {
            bucket_name: [
                dict(m)
                for m in external_markers_for_bucket(marker_asset, bucket_name)
            ]
            for bucket_name in VALID_BUCKETS
        }
        for bucket_name in _BUCKETS:
            bucket = data.get(bucket_name)
            if not isinstance(bucket, dict):
                continue
            text = bucket.get("text") or ""
            markers = effective_markers_for_bucket(data, bucket_name, marker_asset)
            if force and existing:
                inline_markers = inline_markers_for_bucket(data, bucket_name)
                new_inline = [
                    m for m in inline_markers
                    if not _is_replaceable_voice(m, source)
                ]
                if len(new_inline) != len(inline_markers):
                    if new_inline:
                        bucket["markers"] = [marker_to_flow(m) for m in new_inline]
                    else:
                        bucket.pop("markers", None)
                    juan_changed = True

                external_markers = asset_markers_by_bucket.get(bucket_name, [])
                new_external = [
                    m for m in external_markers
                    if not _is_replaceable_voice(m, source)
                ]
                if len(new_external) != len(external_markers):
                    asset_markers_by_bucket[bucket_name] = new_external
                    asset_changed = True

                markers = [
                    m for m in markers
                    if not _is_replaceable_voice(m, source)
                ]
            external_markers = asset_markers_by_bucket.get(bucket_name, [])
            new_external = [
                m for m in external_markers
                if not _is_stale_voice_problem(m, source)
            ]
            if len(new_external) != len(external_markers):
                asset_markers_by_bucket[bucket_name] = new_external
                asset_changed = True
                markers = [
                    m for m in markers
                    if not _is_stale_voice_problem(m, source)
                ]
            try:
                new_voices = _derive_for_bucket(
                    source, text, markers,
                    include_tls_notes=include_tls_notes,
                )
            except VoiceDerivationProblem as exc:
                occupied_ids.update(_occupied_marker_ids_for_juan(data, marker_asset))
                problem = _voice_problem_marker(
                    exc, text_id, seq, short, bucket_name, source, len(text),
                    occupied_ids,
                )
                problem_rows.append(_voice_problem_report_row(
                    text_id=text_id,
                    title=title,
                    short=short,
                    seq=seq,
                    bucket_name=bucket_name,
                    marker=problem,
                ))
                asset_markers_by_bucket.setdefault(bucket_name, []).append(problem)
                asset_markers_by_bucket[bucket_name] = _sorted_marker_flows(
                    asset_markers_by_bucket[bucket_name],
                )
                asset_changed = True
                juan_problems += 1
                total_problems += 1
                lines.append(
                    f"  juan {seq:03d} [{bucket_name}]: "
                    f"marked {exc.code}: {exc.message}"
                )
                continue
            except ValueError as exc:
                raise ValueError(f"{juan_path.name} [{bucket_name}]: {exc}") from exc
            if source == "all":
                _warn_voice_overlaps(
                    new_voices, juan_path.name, bucket_name,
                )
            if not new_voices:
                continue
            for v in new_voices:
                name = v["name"]
                juan_by_name[name] = juan_by_name.get(name, 0) + 1
            existing_external = asset_markers_by_bucket.setdefault(bucket_name, [])
            asset_markers_by_bucket[bucket_name] = _sorted_marker_flows(
                list(existing_external) + new_voices,
            )
            for v in new_voices:
                mid = v.get("id")
                if isinstance(mid, str) and mid:
                    occupied_ids.add(mid)
            asset_changed = True

        forced_cleanup = force and existing and (juan_changed or asset_changed)
        problem_cleanup = asset_changed and not juan_by_name and not forced_cleanup
        if not juan_by_name and not forced_cleanup and not problem_cleanup:
            lines.append(f"  juan {seq:03d}: no voice signal; left as-is")
            continue

        for name, count in juan_by_name.items():
            total_by_name[name] = total_by_name.get(name, 0) + count
        if juan_by_name:
            lines.append(
                f"  juan {seq:03d}: {_format_voice_counts(juan_by_name)}"
            )
        elif forced_cleanup:
            lines.append(f"  juan {seq:03d}: removed existing voice marker(s)")
        elif juan_problems == 0:
            lines.append(f"  juan {seq:03d}: cleared stale voice problem marker(s)")

        if juan_changed:
            new_hash = _juan_self_hash(data)
            data["hash"] = new_hash
            pending_juans.append((juan_path, data, new_hash))
        if asset_changed:
            new_asset = build_marker_asset(
                text_id, seq, short, asset_markers_by_bucket,
            )
            marker_entry = marker_asset_entry_for_seq(manifest, seq)
            marker_filename = (
                marker_entry.get("filename")
                if isinstance(marker_entry, dict)
                and isinstance(marker_entry.get("filename"), str)
                else marker_asset_filename(text_id, seq, short)
            )
            pending_assets.append((
                juan_dir / marker_filename,
                new_asset,
                marker_filename,
                new_asset["hash"],
            ))

    # Second pass: writes only run once every juan in the scope has been
    # successfully derived and re-hashed.
    if not dry_run and (pending_juans or pending_assets):
        for juan_path, data, _ in pending_juans:
            juan_path.write_text(dump(data), encoding="utf-8")
        for marker_path, asset, _, _ in pending_assets:
            marker_path.parent.mkdir(parents=True, exist_ok=True)
            marker_path.write_text(dump(asset), encoding="utf-8")
        new_hashes = {
            int(_JUAN_RE.match(p.name).group("seq")): h
            for p, _, h in pending_juans
        }
        marker_hashes = {
            int(asset["seq"]): (filename, hash_value)
            for _, asset, filename, hash_value in pending_assets
            if isinstance(asset.get("seq"), int)
        }
        _update_manifest_for_voice_add(
            manifest_path, new_hashes, marker_hashes,
        )

    return {
        "juans": len(juan_entries),
        "by_name": total_by_name,
        "problems": total_problems,
        "problem_rows": problem_rows,
        "lines": lines,
    }


def _run_remove(
    bundle: str | Path | None,
    out_root,
    *,
    text_id: str | None = None,
    text_prefix: str | None = None,
    dry_run: bool,
) -> int:
    if text_prefix is not None:
        try:
            bundle_dirs = _resolve_bundle_dirs_for_prefix(out_root, text_prefix)
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        rc = 0
        for bundle_dir in bundle_dirs:
            print(f"[bundle {bundle_dir.name}]")
            bundle_rc = _run_remove(
                bundle_dir, out_root, dry_run=dry_run,
            )
            if bundle_rc:
                rc = 1 if rc == 0 else rc
        return rc

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


def _resolve_bundle_dirs_for_prefix(
    out_root: Path | None,
    text_prefix: str,
) -> list[Path]:
    if out_root is None:
        raise FileNotFoundError(
            "bundle directory not found: bundle root not configured; "
            "pass --out or configure a corpus root"
        )
    root = Path(out_root).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"corpus root not found: {root}")
    bundle_dirs = discover_bundles(root, prefix=text_prefix)
    if not bundle_dirs:
        raise FileNotFoundError(
            f"no bundles found under {root} with prefix {text_prefix!r}"
        )
    return bundle_dirs


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
    manifest = _yaml_load_text(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(manifest, dict):
        raise RuntimeError(f"{manifest_path.name}: manifest top level is not a mapping")

    lines: list[str] = []
    total_removed = 0
    pending: list[tuple[Path, dict, str]] = []

    for seq, juan_path in juan_entries:
        data = _yaml_load_text(juan_path.read_text(encoding="utf-8"))
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


def _occupied_marker_ids_for_juan(
    data: dict,
    marker_asset: dict | None,
) -> set[str]:
    occupied: set[str] = set()
    for bucket_name in _BUCKETS:
        for marker in effective_markers_for_bucket(data, bucket_name, marker_asset):
            mid = marker.get("id") if isinstance(marker, dict) else None
            if isinstance(mid, str) and mid:
                occupied.add(mid)
    return occupied


def _is_stale_voice_problem(marker: object, source: str) -> bool:
    return (
        isinstance(marker, dict)
        and marker.get("type") == _VOICE_PROBLEM_TYPE
        and marker.get("source") == source
    )


def _voice_problem_marker(
    problem: VoiceDerivationProblem,
    text_id: str,
    seq: int,
    short: str | None,
    bucket_name: str,
    source: str,
    text_len: int,
    occupied_ids: set[str],
) -> dict:
    offset = min(max(problem.offset, 0), text_len)
    length = min(max(problem.length, 0), max(0, text_len - offset))
    [marker_id] = allocate_marker_ids(
        [_VOICE_PROBLEM_TYPE],
        text_id=text_id,
        edition=short or "bkk",
        juan_label=f"{seq:03d}",
        occupied_ids=occupied_ids,
    )
    occupied_ids.add(marker_id)
    return marker_to_flow({
        "type": _VOICE_PROBLEM_TYPE,
        "offset": offset,
        "length": length,
        "id": marker_id,
        "source": source,
        "bucket": bucket_name,
        "code": problem.code,
        "message": problem.message,
    })


def _voice_problem_report_row(
    *,
    text_id: str,
    title: str | None,
    short: str | None,
    seq: int,
    bucket_name: str,
    marker: dict,
) -> dict:
    return {
        "id": 0,
        "textid": text_id,
        "title": title,
        "edition": short,
        "seq": seq,
        "bucket": bucket_name,
        "offset": marker.get("offset") if isinstance(marker.get("offset"), int) else 0,
        "length": marker.get("length") if isinstance(marker.get("length"), int) else 0,
        "marker_id": marker.get("id") if isinstance(marker.get("id"), str) else "",
        "source": marker.get("source") if isinstance(marker.get("source"), str) else None,
        "code": marker.get("code") if isinstance(marker.get("code"), str) else None,
        "message": marker.get("message") if isinstance(marker.get("message"), str) else "",
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


def _sorted_marker_flows(markers: list[dict]) -> list[dict]:
    """Sort markers by offset while preserving original order for ties."""
    indexed = list(enumerate(markers))
    indexed.sort(key=lambda p: (p[1].get("offset", 0), p[0]))
    return [marker_to_flow(m) for _, m in indexed]


def _derive_for_bucket(
    source: str, text: str, markers: list, *, include_tls_notes: bool = True,
) -> list[dict]:
    """Dispatch to the requested deriver(s) and return their merged output.

    For ``--source all`` the two derivers' outputs are simply concatenated.
    Their voice-name spaces are disjoint (parens → ``note``/``emphasis``;
    indent → ``root``/``commentary``/``head``/``attribution``), so
    same-name overlaps are impossible by construction and their id prefixes
    (``n``/``e`` vs ``r``/``c``/``h``/``a``) don't collide either.
    """
    text_len = len(text)
    if source == "parens":
        return derive_voice_markers(
            text_len, markers, include_tls_notes=include_tls_notes,
        )
    if source == "indent":
        return derive_voice_markers_from_indent(text_len, markers)
    if source == "dictionary":
        return derive_dictionary_voice_markers(text, markers)
    if source == "all":
        return list(
            derive_voice_markers(
                text_len, markers, include_tls_notes=include_tls_notes,
            )
        ) + list(derive_voice_markers_from_indent(text_len, markers))
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


def _existing_voice_count(
    juan_data: dict, marker_asset: dict | None = None, *, source: str | None = None,
) -> int:
    n = 0
    for bucket_name in _BUCKETS:
        for m in effective_markers_for_bucket(juan_data, bucket_name, marker_asset):
            if _is_replaceable_voice(m, source):
                n += 1
    return n


def _is_replaceable_voice(marker: object, source: str | None) -> bool:
    if not isinstance(marker, dict) or marker.get("type") != "voice":
        return False
    if source == "dictionary":
        return marker.get("source") == "dictionary"
    return True


def _juan_self_hash(juan_dict: dict) -> str:
    m = copy.deepcopy(juan_dict)
    m["hash"] = ZERO_HASH
    return sha256_jcs(m)


def _update_manifest(manifest_path: Path, new_hashes: dict[int, str]) -> None:
    """Patch ``assets.parts[*].hash`` for each updated juan, then recompute
    the manifest's self-hash and rewrite the file."""
    data = _yaml_load_text(manifest_path.read_text(encoding="utf-8"))
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
    # Remove hydrates external markers into the physical juan before editing.
    # Clear stale marker-asset declarations; its follow-up externalize pass
    # rebuilds them from the edited effective marker lists.
    data["assets"].pop("markers", None)
    data["hash"] = manifest_hash(data)
    manifest_path.write_text(dump(data), encoding="utf-8")


def _update_manifest_for_voice_add(
    manifest_path: Path,
    new_part_hashes: dict[int, str],
    marker_hashes: dict[int, tuple[str, str]],
) -> None:
    """Patch changed juan and marker-asset hashes after direct voice writes."""
    data = _yaml_load_text(manifest_path.read_text(encoding="utf-8"))
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
        if isinstance(seq, int) and seq in new_part_hashes:
            entry = dict(entry)
            entry["hash"] = new_part_hashes[seq]
        new_parts.append(marker_to_flow(entry))
    assets["parts"] = new_parts

    existing_markers = assets.get("markers") or []
    markers_by_seq: dict[int, dict] = {}
    passthrough: list = []
    for entry in existing_markers:
        if not isinstance(entry, dict):
            passthrough.append(entry)
            continue
        seq = entry.get("seq")
        if isinstance(seq, int):
            markers_by_seq[seq] = dict(entry)
        else:
            passthrough.append(entry)
    for seq, (filename, hash_value) in marker_hashes.items():
        entry = markers_by_seq.get(seq, {"seq": seq, "role": "markers"})
        entry["filename"] = filename
        entry["hash"] = hash_value
        markers_by_seq[seq] = entry
    if markers_by_seq or passthrough:
        assets["markers"] = passthrough + [
            marker_to_flow(markers_by_seq[seq])
            for seq in sorted(markers_by_seq)
        ]
    else:
        assets.pop("markers", None)

    data["hash"] = manifest_hash(data)
    manifest_path.write_text(dump(data), encoding="utf-8")


def main() -> None:
    raise SystemExit(run())
