"""Command-line entry point for ``bkk voice``.

Exposes two operations:

``add (--bundle <dir> | --text-id <id> | --text-prefix <prefix> | --juan <ref>)``
walks every selected juan file in the selected bundle(s) (master plus each
documentary edition), derives ``voice`` range markers from the markers already
on disk, writes the derived markers into each juan's marker asset, and
refreshes marker-asset and manifest hashes.

    python -m bkk voice add --bundle <out-root>/<text-id>/
    python -m bkk voice add --text-id <text-id>     # resolved via .bkkrc
    python -m bkk voice add --text-prefix KR6q      # resolved via .bkkrc
    python -m bkk voice add --juan KR3k0059/147     # one juan only

Bare-id and prefix forms resolve the bundle root against ``global.corpus``
from ``.bkkrc`` unless ``--out`` is passed.

``--source`` selects the derivation:

- ``parens`` (default) — from source punctuation marker pairs, emits
  ``note`` spans for ``(``…``)`` text and ``emphasis`` spans for
  ``▲``…``)`` text. The deriver makes no claim about whether a note span
  is commentary, gloss, or alternate reading — only that it's bracketed.
  It also includes the semantic punctuation rules from ``punctuation``, such
  as ``title`` spans for ``《``…``》`` text.
- ``indent`` — from ``line-break``/``indent`` markers, emits
  ``root``/``commentary``/``head``/``attribution`` for sources whose
  layout indents each textual layer differently.
- ``indent-headings`` — from short lines opened by CJK ``indent`` markers,
  emits ``head`` spans for tractat-style section labels.
- ``tls-seg`` — from ``tls:seg-start``/``tls:seg-end`` runs carrying
  ``seg_type=root`` or ``seg_type=comm``, emits ``root``/``commentary``.
- ``dictionary`` — after generic ``note`` voices exist, detects definition
  notes and emits linked ``lemma``/``def`` spans with
  ``source="dictionary"``.
- ``punctuation`` — from source punctuation marker pairs, emits
  ``title`` spans for ``《``…``》`` text with ``source="punctuation"``.
- ``all`` — parens plus explicit TLS segment voicing when present,
  otherwise indent voicing. Heterogeneous overlaps are written through
  with a per-juan stderr warning.
- ``auto`` — chooses explicit TLS segments, tractat heading indents, or
  generic indentation by marker profile.

For paren derivation, TLS inline note bracket markers are included by
default; pass ``--no-tls-notes`` to ignore ``tls:note-start`` /
``tls:note-end`` markers and derive only from punctuation markers.

``--force`` strips any pre-existing ``voice`` markers and rederives;
without it the command skips juans that already carry voice markers, so
reruns can resume failed juans without touching completed ones.

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
from bkk.short_refs import parse_text_juan_selector, text_id_arg, text_or_path_arg

from .derive import (
    VoiceDerivationProblem,
    derive_voice_markers,
    derive_voice_markers_best_effort,
)
from .derive_dictionary import derive_dictionary_voice_markers
from .derive_indent import derive_voice_markers_from_indent
from .derive_indent_headings import (
    HEADING_INDENT_VOICE_SOURCE,
    derive_voice_markers_from_indent_headings,
    has_indent_heading_profile,
)
from .derive_punctuation import (
    PUNCTUATION_VOICE_SOURCE,
    derive_voice_markers_from_punctuation,
    derive_voice_markers_from_punctuation_best_effort,
)
from .derive_tls_seg import (
    derive_voice_markers_from_tls_segments,
    derive_voice_markers_from_tls_segments_best_effort,
)
from .ctf import build_ctf_asset, ctf_tsv_text
from .problems import (
    VoiceProblemReportError,
    find_voice_problems,
    update_voice_problems_report,
    write_voice_problems_report,
)


_VALID_SOURCES = (
    "parens",
    "indent",
    "indent-headings",
    "tls-seg",
    "dictionary",
    "punctuation",
    "all",
    "auto",
)
_VOICE_PROBLEM_TYPE = "voice:problem"
_AUTO_LAYOUT_SOURCES = {"indent", "indent-headings", "tls-seg"}
_AUTO_LEGACY_LAYOUT_NAMES = {"root", "commentary", "head", "attribution"}


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
        help="derivation source: 'parens' (default; note/emphasis punctuation "
             "pairs plus semantic punctuation), "
             "'indent' (layout indentation), 'indent-headings' (short "
             "CJK-indent section labels), 'tls-seg' (typed TLS segment "
             "runs), 'dictionary' (lemma spans for lemma-repeat notes), "
             "'punctuation' (semantic punctuation pairs such as title "
             "brackets), "
             "'all' (parens + explicit TLS segments, falling back to indent), "
             "or 'auto' (choose TLS, heading indents, or generic indent). "
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
        help="replace existing voice markers (default: skip juans that have any)",
    )
    pa.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="report what would be written without modifying files",
    )
    pa.add_argument(
        "--juan",
        dest="juan_selectors",
        action="append",
        default=None,
        help=(
            "restrict add to one complete juan; repeatable. Accepts "
            "KR refs like KR3k0059/147 as a standalone selector, or a "
            "bare seq like 147 with --bundle/--text-id/--text-prefix."
        ),
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
    pc = sub.add_parser(
        "ctf",
        help="write standalone citation tree fragment sidecars from "
             "indent-heading voice markers",
    )
    _add_bundle_selector(pc)
    for action in pc._actions:
        if action.dest == "out_root":
            action.help = (
                "with --tsv, root for <section>/<text-id>.ctf.tsv "
                "(overrides global.ctf_root); otherwise bundle output root "
                "used to resolve --text-id/--text-prefix"
            )
            break
    pc.add_argument(
        "--juan",
        dest="juan_selectors",
        action="append",
        default=None,
        help=(
            "restrict CTF export to one complete juan; repeatable. Accepts "
            "KR refs like KR3k0059/147 as a standalone selector, or a "
            "bare seq like 147 with --bundle/--text-id/--text-prefix."
        ),
    )
    pc.add_argument(
        "--out-dir",
        dest="out_dir",
        type=Path,
        default=None,
        help="directory for CTF files (defaults to the bundle assets directory)",
    )
    pc.add_argument(
        "--heading-source",
        dest="heading_source",
        choices=("auto", "voices", "derive"),
        default="auto",
        help="heading source: existing indent-heading voices, fresh derivation, "
             "or auto existing-first (default)",
    )
    pc.add_argument(
        "--short",
        dest="short_refs",
        action="store_true",
        help="emit compact refs such as 4c22/1/@8+37 instead of canonical refs",
    )
    pc.add_argument(
        "--tsv",
        dest="tsv",
        action="store_true",
        help="write one whole-text TSV containing id, parent_id, and label",
    )
    pc.add_argument(
        "--force", action="store_true",
        help="overwrite existing CTF files",
    )
    pc.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="report what would be written without modifying files",
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

    if args.op == "remove":
        try:
            bundle, text_id, text_prefix = _selected_bundle_args(args)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if text_prefix is not None:
            return _run_remove(
                bundle, out_root, text_id=text_id, text_prefix=text_prefix,
                dry_run=args.dry_run,
            )
        return _run_remove(
            bundle, out_root, text_id=text_id, dry_run=args.dry_run,
        )

    if args.op == "ctf":
        ctf_out_root = None
        ctf_bundle_root = out_root
        if args.tsv:
            ctf_out_root = args.out_root
            if args.out_root is not None:
                from bkk.config import load_rc
                rc = load_rc()
                ctf_bundle_root = resolve_rc_path(
                    None, rc, (("global", "corpus"),),
                )
        try:
            bundle, text_id, text_prefix, selected_juans = _selected_add_args(args)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if text_prefix is not None:
            return _run_ctf(
                bundle, ctf_bundle_root, text_id=text_id, text_prefix=text_prefix,
                selected_juans=selected_juans, out_dir=args.out_dir,
                tsv=args.tsv, tsv_out_root=ctf_out_root,
                heading_source=args.heading_source, short_refs=args.short_refs,
                force=args.force, dry_run=args.dry_run,
            )
        return _run_ctf(
            bundle, ctf_bundle_root, text_id=text_id, selected_juans=selected_juans,
            out_dir=args.out_dir, tsv=args.tsv, tsv_out_root=ctf_out_root,
            heading_source=args.heading_source, short_refs=args.short_refs,
            force=args.force, dry_run=args.dry_run,
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

    try:
        bundle, text_id, text_prefix, selected_juans = _selected_add_args(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if text_prefix is not None:
        return _run_add(
            bundle, out_root, text_id=text_id, text_prefix=text_prefix,
            source=source, force=args.force, dry_run=args.dry_run,
            include_tls_notes=tls_notes, selected_juans=selected_juans,
        )
    return _run_add(
        bundle, out_root, text_id=text_id, source=source, force=args.force,
        dry_run=args.dry_run, include_tls_notes=tls_notes,
        selected_juans=selected_juans,
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


def _selected_add_args(
    args: argparse.Namespace,
) -> tuple[str | Path | None, str | None, str | None, set[int] | None]:
    juan_selectors = list(getattr(args, "juan_selectors", None) or [])
    text_refs: list[tuple[str, int]] = []
    local_seqs: list[int] = []
    for raw in juan_selectors:
        value = str(raw).strip()
        if value.isdigit():
            local_seqs.append(int(value))
            continue
        try:
            textid, seq = parse_text_juan_selector(value)
        except ValueError as exc:
            raise ValueError(f"invalid --juan selector {raw!r}: {exc}") from exc
        if seq is None:
            raise ValueError(f"--juan selector {raw!r} must include a juan number")
        text_refs.append((textid, seq))

    supplied_bundle_selector = any([
        bool(getattr(args, "legacy_bundle", None)),
        bool(getattr(args, "bundle", None)),
        bool(getattr(args, "text_id", None)),
        bool(getattr(args, "text_prefix", None)),
    ])
    if text_refs:
        if supplied_bundle_selector:
            raise ValueError(
                "--juan TEXT/SEQ cannot be combined with --bundle, --text-id, "
                "or --text-prefix"
            )
        textids = {textid for textid, _ in text_refs}
        if len(textids) != 1:
            raise ValueError("--juan TEXT/SEQ selectors must all name the same text")
        return None, text_refs[0][0], None, {seq for _, seq in text_refs}

    bundle, text_id, text_prefix = _selected_bundle_args(args)
    return bundle, text_id, text_prefix, set(local_seqs) if local_seqs else None


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
    selected_juans: set[int] | None = None,
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
                selected_juans=selected_juans,
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
                selected_juans=selected_juans,
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
    selected_juans: set[int] | None = None,
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
        seq = int(m.group("seq"))
        if selected_juans is not None and seq not in selected_juans:
            continue
        juan_entries.append((seq, entry))
    juan_entries.sort(key=lambda t: t[0])

    if not juan_entries:
        if selected_juans is not None:
            selected = ", ".join(str(seq) for seq in sorted(selected_juans))
            raise RuntimeError(
                f"no selected juan files found under {juan_dir}: {selected}"
            )
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
            lines.append(
                f"  juan {seq:03d}: {existing} voice marker(s) already present; "
                "skipped"
            )
            continue

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
            derive_markers = list(markers)
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
                if source == "dictionary":
                    derive_markers = [
                        m for m in derive_markers
                        if not _is_dictionary_lemma_voice(m)
                    ]
                else:
                    derive_markers = list(markers)
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
                derive_markers = [
                    m for m in derive_markers
                    if not _is_stale_voice_problem(m, source)
                ]
            try:
                new_voices, problems = _derive_for_bucket_best_effort(
                    source, text, derive_markers,
                    include_tls_notes=include_tls_notes,
                )
                if problems:
                    occupied_ids.update(
                        _occupied_marker_ids_for_juan(data, marker_asset)
                    )
                for exc in problems:
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
                    asset_changed = True
                    juan_problems += 1
                    total_problems += 1
                    lines.append(
                        f"  juan {seq:03d} [{bucket_name}]: "
                        f"marked {exc.code}: {exc.message}"
                    )
                if problems:
                    asset_markers_by_bucket[bucket_name] = _sorted_marker_flows(
                        asset_markers_by_bucket[bucket_name],
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
            if source == "dictionary":
                inline_changed, external_changed = _remove_replaced_dictionary_notes(
                    bucket, bucket_name, asset_markers_by_bucket, new_voices,
                )
                juan_changed = juan_changed or inline_changed
                asset_changed = asset_changed or external_changed
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


def _run_ctf(
    bundle: str | Path | None,
    out_root,
    *,
    text_id: str | None = None,
    text_prefix: str | None = None,
    selected_juans: set[int] | None = None,
    out_dir: Path | None,
    tsv: bool,
    tsv_out_root: Path | None,
    heading_source: str,
    short_refs: bool,
    force: bool,
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
            bundle_rc = _run_ctf(
                bundle_dir, out_root, selected_juans=selected_juans,
                out_dir=out_dir, tsv=tsv, tsv_out_root=tsv_out_root,
                heading_source=heading_source,
                short_refs=short_refs, force=force, dry_run=dry_run,
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
    manifest_path = bundle_dir / f"{text_id}.manifest.yaml"
    if not manifest_path.exists():
        print(f"error: master manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    print("[master]")
    if tsv:
        return _run_one_ctf_tsv(
            bundle_dir,
            manifest_path,
            text_id,
            selected_juans=selected_juans,
            tsv_out_root=tsv_out_root,
            heading_source=heading_source,
            short_refs=short_refs,
            force=force,
            dry_run=dry_run,
        )
    try:
        stats = _process_one_ctf(
            bundle_dir,
            manifest_path,
            text_id,
            selected_juans=selected_juans,
            out_dir=out_dir,
            heading_source=heading_source,
            short_refs=short_refs,
            force=force,
            dry_run=dry_run,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"  error: {exc}", file=sys.stderr)
        print("  master skipped; no files written for this scope")
        return 1

    for line in stats["lines"]:
        print(line)
    verb = "would write" if dry_run else "wrote"
    skip_verb = "would skip" if dry_run else "skipped"
    print(
        f"{verb} {stats['written']} CTF file(s); "
        f"{skip_verb} {stats['skipped']} existing file(s) "
        f"across {stats['juans']} juan file(s)"
    )
    return 0


def _run_one_ctf_tsv(
    juan_dir: Path,
    manifest_path: Path,
    text_id: str,
    *,
    selected_juans: set[int] | None,
    tsv_out_root: Path | None,
    heading_source: str,
    short_refs: bool,
    force: bool,
    dry_run: bool,
) -> int:
    try:
        stats = _process_one_ctf_tsv(
            juan_dir,
            manifest_path,
            text_id,
            selected_juans=selected_juans,
            tsv_out_root=tsv_out_root,
            heading_source=heading_source,
            short_refs=short_refs,
            force=force,
            dry_run=dry_run,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"  error: {exc}", file=sys.stderr)
        print("  master skipped; no files written for this scope")
        return 1
    for line in stats["lines"]:
        print(line)
    if dry_run:
        print(
            f"would write {stats['written']} CTF TSV file(s); "
            f"would skip {stats['skipped']} existing file(s)"
        )
    else:
        print(
            f"wrote {stats['written']} CTF TSV file(s); "
            f"skipped {stats['skipped']} existing file(s)"
        )
    return 0


def _process_one_ctf(
    juan_dir: Path,
    manifest_path: Path,
    text_id: str,
    *,
    selected_juans: set[int] | None = None,
    out_dir: Path | None,
    heading_source: str,
    short_refs: bool,
    force: bool,
    dry_run: bool,
) -> dict:
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
        if m.group("short") is not None:
            continue
        seq = int(m.group("seq"))
        if selected_juans is not None and seq not in selected_juans:
            continue
        juan_entries.append((seq, entry))
    juan_entries.sort(key=lambda t: t[0])

    if not juan_entries:
        if selected_juans is not None:
            selected = ", ".join(str(seq) for seq in sorted(selected_juans))
            raise RuntimeError(
                f"no selected juan files found under {juan_dir}: {selected}"
            )
        raise RuntimeError(f"no juan files found under {juan_dir}")

    manifest = _yaml_load_text(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(manifest, dict):
        raise RuntimeError(f"{manifest_path.name}: manifest top level is not a mapping")
    manifest_hash_value = manifest.get("hash")
    if not isinstance(manifest_hash_value, str):
        manifest_hash_value = None

    lines: list[str] = []
    written = 0
    skipped = 0
    pending: list[tuple[Path, dict]] = []
    target_dir = out_dir if out_dir is not None else juan_dir / "assets"

    for seq, juan_path in juan_entries:
        output_path = target_dir / f"{text_id}_{seq:03d}.ctf.yaml"
        if output_path.exists() and not force:
            skipped += 1
            if not dry_run:
                lines.append(
                    f"  juan {seq:03d}: {output_path}; already exists; skipped"
                )
            continue

        if dry_run:
            written += 1
            continue

        data = _yaml_load_text(juan_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError(f"{juan_path.name}: top-level YAML is not a mapping")
        body = data.get("body")
        if not isinstance(body, dict):
            raise RuntimeError(f"{juan_path.name}: missing body bucket")
        text = body.get("text")
        if not isinstance(text, str):
            raise RuntimeError(f"{juan_path.name}: body.text is not a string")
        bucket_hash = body.get("hash")
        if not isinstance(bucket_hash, str):
            bucket_hash = None

        marker_asset = load_marker_asset(juan_dir, manifest, seq)
        markers = effective_markers_for_bucket(data, "body", marker_asset)
        ctf = build_ctf_asset(
            text_id=text_id,
            seq=seq,
            bucket_name="body",
            text=text,
            markers=markers,
            manifest_hash=manifest_hash_value,
            bucket_hash=bucket_hash,
            heading_source=heading_source,
            short_refs=short_refs,
        )
        written += 1
        pending.append((output_path, ctf))
        action = "would write" if dry_run else "wrote"
        lines.append(
            f"  juan {seq:03d}: {action} {output_path} "
            f"({len(ctf['nodes'])} node(s))"
        )

    if not dry_run:
        for output_path, ctf in pending:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(dump(ctf), encoding="utf-8")

    return {
        "juans": len(juan_entries),
        "written": written,
        "skipped": skipped,
        "lines": lines,
    }


def _process_one_ctf_tsv(
    juan_dir: Path,
    manifest_path: Path,
    text_id: str,
    *,
    selected_juans: set[int] | None,
    tsv_out_root: Path | None,
    heading_source: str,
    short_refs: bool,
    force: bool,
    dry_run: bool,
) -> dict:
    output_root = _resolve_ctf_tsv_root(tsv_out_root)
    output_path = _ctf_tsv_output_path(output_root, text_id)

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
        if m.group("short") is not None:
            continue
        seq = int(m.group("seq"))
        if selected_juans is not None and seq not in selected_juans:
            continue
        juan_entries.append((seq, entry))
    juan_entries.sort(key=lambda t: t[0])
    if not juan_entries:
        if selected_juans is not None:
            selected = ", ".join(str(seq) for seq in sorted(selected_juans))
            raise RuntimeError(
                f"no selected juan files found under {juan_dir}: {selected}"
            )
        raise RuntimeError(f"no juan files found under {juan_dir}")
    if output_path.exists() and not force:
        return {"written": 0, "skipped": 1, "lines": []}
    if dry_run:
        return {"written": 1, "skipped": 0, "lines": []}

    manifest = _yaml_load_text(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(manifest, dict):
        raise RuntimeError(f"{manifest_path.name}: manifest top level is not a mapping")
    manifest_hash_value = manifest.get("hash")
    if not isinstance(manifest_hash_value, str):
        manifest_hash_value = None
    title = (manifest.get("metadata") or {}).get("title")
    title = title if isinstance(title, str) and title else None

    all_nodes: list[dict] = []
    for seq, juan_path in juan_entries:
        data = _yaml_load_text(juan_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise RuntimeError(f"{juan_path.name}: top-level YAML is not a mapping")
        body = data.get("body")
        if not isinstance(body, dict):
            raise RuntimeError(f"{juan_path.name}: missing body bucket")
        text = body.get("text")
        if not isinstance(text, str):
            raise RuntimeError(f"{juan_path.name}: body.text is not a string")
        bucket_hash = body.get("hash")
        if not isinstance(bucket_hash, str):
            bucket_hash = None
        marker_asset = load_marker_asset(juan_dir, manifest, seq)
        ctf = build_ctf_asset(
            text_id=text_id,
            seq=seq,
            bucket_name="body",
            text=text,
            markers=effective_markers_for_bucket(data, "body", marker_asset),
            manifest_hash=manifest_hash_value,
            bucket_hash=bucket_hash,
            heading_source=heading_source,
            short_refs=short_refs,
        )
        all_nodes.extend(ctf["nodes"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        ctf_tsv_text(
            text_id=text_id,
            text_label=title,
            nodes=all_nodes,
            short_refs=short_refs,
        ),
        encoding="utf-8",
    )
    return {
        "written": 1,
        "skipped": 0,
        "lines": [
            f"  wrote {output_path} ({len(all_nodes)} node row(s) + text root)"
        ],
    }


def _resolve_ctf_tsv_root(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    from bkk.config import load_rc
    rc = load_rc()
    root = resolve_rc_path(None, rc, (("global", "ctf_root"),))
    if root is None:
        raise RuntimeError(
            "CTF TSV output root is required: pass --out or configure global.ctf_root"
        )
    return root


def _ctf_tsv_output_path(output_root: Path, text_id: str) -> Path:
    section = text_id[:4]
    return output_root / section / f"{text_id}.ctf.tsv"


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

    For ``--source all``, explicit TLS segment voicing wins over indent
    voicing for the root/commentary layer because both sources produce the
    same names.
    """
    text_len = len(text)
    if source == "parens":
        return (
            derive_voice_markers(
                text_len, markers, include_tls_notes=include_tls_notes,
            )
            + derive_voice_markers_from_punctuation(text_len, markers)
        )
    if source == "indent":
        return derive_voice_markers_from_indent(text_len, markers)
    if source == "indent-headings":
        return derive_voice_markers_from_indent_headings(text_len, markers, text)
    if source == "tls-seg":
        return derive_voice_markers_from_tls_segments(text_len, markers)
    if source == "dictionary":
        return derive_dictionary_voice_markers(text, markers)
    if source == "punctuation":
        return derive_voice_markers_from_punctuation(text_len, markers)
    if source == "auto":
        return _derive_auto_for_bucket(text, markers)
    if source == "all":
        paren_voices = list(
            derive_voice_markers(
                text_len, markers, include_tls_notes=include_tls_notes,
            )
        )
        punctuation_voices = list(
            derive_voice_markers_from_punctuation(text_len, markers)
        )
        tls_voices = list(derive_voice_markers_from_tls_segments(text_len, markers))
        layout_voices = (
            tls_voices if tls_voices
            else list(derive_voice_markers_from_indent(text_len, markers))
        )
        return paren_voices + punctuation_voices + layout_voices
    raise ValueError(f"unknown voice source: {source!r}")


def _derive_for_bucket_best_effort(
    source: str, text: str, markers: list, *, include_tls_notes: bool = True,
) -> tuple[list[dict], list[VoiceDerivationProblem]]:
    """Derive voices and return recoverable problems for ``bkk voice add``."""
    text_len = len(text)
    if source == "parens":
        paren_voices, paren_problems = derive_voice_markers_best_effort(
            text_len, markers, include_tls_notes=include_tls_notes,
        )
        punctuation_voices, punctuation_problems = (
            derive_voice_markers_from_punctuation_best_effort(text_len, markers)
        )
        return (
            list(paren_voices) + list(punctuation_voices),
            paren_problems + punctuation_problems,
        )
    if source == "tls-seg":
        return derive_voice_markers_from_tls_segments_best_effort(
            text_len, markers,
        )
    if source == "punctuation":
        return derive_voice_markers_from_punctuation_best_effort(
            text_len, markers,
        )
    if source == "indent-headings":
        return derive_voice_markers_from_indent_headings(text_len, markers, text), []
    if source == "auto":
        return _derive_auto_for_bucket_best_effort(text, markers)
    if source == "all":
        paren_voices, problems = derive_voice_markers_best_effort(
            text_len, markers, include_tls_notes=include_tls_notes,
        )
        punctuation_voices, punctuation_problems = (
            derive_voice_markers_from_punctuation_best_effort(text_len, markers)
        )
        tls_voices, tls_problems = derive_voice_markers_from_tls_segments_best_effort(
            text_len, markers,
        )
        layout_voices = (
            list(tls_voices) if tls_voices or tls_problems
            else list(derive_voice_markers_from_indent(text_len, markers))
        )
        return (
            list(paren_voices)
            + list(punctuation_voices)
            + layout_voices,
            problems + punctuation_problems + tls_problems,
        )
    return _derive_for_bucket(
        source, text, markers, include_tls_notes=include_tls_notes,
    ), []


def _derive_auto_for_bucket(text: str, markers: list) -> list[dict]:
    voices, problems = _derive_auto_for_bucket_best_effort(text, markers)
    if problems:
        raise problems[0]
    return voices


def _derive_auto_for_bucket_best_effort(
    text: str, markers: list,
) -> tuple[list[dict], list[VoiceDerivationProblem]]:
    text_len = len(text)
    tls_voices, tls_problems = derive_voice_markers_from_tls_segments_best_effort(
        text_len, markers,
    )
    if tls_voices or tls_problems:
        return _tag_voice_source(list(tls_voices), "tls-seg"), tls_problems
    if has_indent_heading_profile(text_len, markers, text):
        return derive_voice_markers_from_indent_headings(text_len, markers, text), []
    return _tag_voice_source(
        list(derive_voice_markers_from_indent(text_len, markers)),
        "indent",
    ), []


def _tag_voice_source(voices: list[dict], source: str) -> list[dict]:
    for voice in voices:
        if isinstance(voice, dict):
            voice.setdefault("source", source)
    return voices


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
    if source == "punctuation":
        return marker.get("source") == PUNCTUATION_VOICE_SOURCE
    if source == "indent-headings":
        return marker.get("source") == HEADING_INDENT_VOICE_SOURCE
    if source == "auto":
        marker_source = marker.get("source")
        if marker_source in _AUTO_LAYOUT_SOURCES:
            return True
        return (
            marker_source is None
            and marker.get("name") in _AUTO_LEGACY_LAYOUT_NAMES
        )
    return True


def _is_dictionary_lemma_voice(marker: object) -> bool:
    return (
        isinstance(marker, dict)
        and marker.get("type") == "voice"
        and marker.get("source") == "dictionary"
        and marker.get("name") == "lemma"
    )


def _remove_replaced_dictionary_notes(
    bucket: dict,
    bucket_name: str,
    asset_markers_by_bucket: dict[str, list[dict]],
    new_voices: list[dict],
) -> tuple[bool, bool]:
    def_spans = {
        (
            marker.get("offset"),
            marker.get("length"),
        )
        for marker in new_voices
        if (
            isinstance(marker, dict)
            and marker.get("type") == "voice"
            and marker.get("name") == "def"
            and isinstance(marker.get("offset"), int)
            and isinstance(marker.get("length"), int)
        )
    }
    if not def_spans:
        return False, False

    inline_changed = False
    inline_markers = inline_markers_for_bucket({bucket_name: bucket}, bucket_name)
    new_inline = [
        marker for marker in inline_markers
        if not _is_replaced_dictionary_note(marker, def_spans)
    ]
    if len(new_inline) != len(inline_markers):
        inline_changed = True
        if new_inline:
            bucket["markers"] = [marker_to_flow(marker) for marker in new_inline]
        else:
            bucket.pop("markers", None)

    external_changed = False
    external_markers = asset_markers_by_bucket.get(bucket_name, [])
    new_external = [
        marker for marker in external_markers
        if not _is_replaced_dictionary_note(marker, def_spans)
    ]
    if len(new_external) != len(external_markers):
        external_changed = True
        asset_markers_by_bucket[bucket_name] = new_external

    return inline_changed, external_changed


def _is_replaced_dictionary_note(
    marker: object,
    def_spans: set[tuple[object, object]],
) -> bool:
    return (
        isinstance(marker, dict)
        and marker.get("type") == "voice"
        and marker.get("name") == "note"
        and marker.get("source") != "dictionary"
        and (marker.get("offset"), marker.get("length")) in def_spans
    )


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
