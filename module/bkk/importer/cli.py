"""Command-line interface for the BKK importer.

TLS invocation::

    # single text
    python -m bkk.importer --format tls --in <tls-root> --out <out-root> --text-id <id>

    # every text discoverable under <tls-root> (prompts for confirmation)
    python -m bkk.importer --format tls --in <tls-root> --out <out-root>

Expects the TLS repository layout:

    <tls-root>/
      tls-texts/data/.../<text-id>.xml      (searched recursively)
      tls-data/notes/swl/<text-id>-ann.xml
      tls-data/notes/doc/<text-id>-ann.xml

KRP invocation::

    # single text from a local kanripo mirror
    python -m bkk.importer --format krp --in <root> --text-id KR3a0013 --out <out>

    # single text fetched on demand from github.com/kanripo/<id>
    python -m bkk.importer --format krp --text-id KR3a0013 --out <out>

    # every text under a corpus prefix (prompts for confirmation; --yes skips)
    python -m bkk.importer --format krp --in <root> --section KR3a --out <out>

    # every discoverable text under SOURCE (prompts for confirmation)
    python -m bkk.importer --format krp --in <root> --out <out>

    # legacy recipe-driven path (overrides everything else)
    python -m bkk.importer --format krp --recipe <recipe.yaml>

Translation invocation::

    # every translation file under <in>/tls-data/translations/
    python -m bkk.importer --format translation --in <tls-root> --out <out>

    # narrow to one source text id (all languages / revisions for that id)
    python -m bkk.importer --format translation --in <tls-root> \\
                           --out <out> --text-id KR1h0004

    # narrow further by target language
    python -m bkk.importer --format translation --in <tls-root> \\
                           --out <out> --text-id KR1h0004 --lang en

Each input XML file becomes one bundle at
``<out>/translations/<file-stem>/``; the stem (e.g.
``KR1h0004-en-588d9aad``) preserves snapshot suffixes so revisions are
imported as distinct bundles. See ``bunkankun.md`` §"Translations".

The recipe-less paths derive editions, master/imglist branches, witnesses,
title, and date from the source repo itself (branch list + ``Readme.org``).
See :mod:`bkk.importer.source` for the discovery + synthesis logic and
:mod:`bkk.importer.recipe` for the schema recipes still pin.

Either format emits a BKK bundle under ``<out-root>/<text-id>/``.

Cross-source co-existence (see ``docs/cross-source-merge.md``):

- TLS owns the surface (root) edition. If a TLS bundle already exists
  at the destination, a subsequent KRP import merges in: documentary
  editions land under ``editions/<short>/``, the synthesized KRP master
  is demoted to ``editions/krp/`` (variant + witness page-break
  markers retained), and the TLS root manifest's ``editions:`` list is
  extended.
- TLS into an existing KRP bundle is rejected with a hard error. The
  operator removes the bundle and re-imports in TLS-then-KRP order.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from .diverge import diff_trees, render_report
from .read.tls import read_tls
from .recipe import Recipe, load_recipe
from .write.bundle import (
    write_bundle, write_krp_edition, write_krp_master, write_pua_map,
)
from .write.merge import (
    extend_master_editions, inspect_existing_bundle,
    project_krp_apparatus_onto_tls,
)


class BundleConflictError(Exception):
    """Raised when an importer refuses to write because the existing bundle
    on disk was produced by an incompatible source. Caught by the bulk
    loop so other texts can continue."""


_DEFAULT_GITHUB_USER = "kanripo"
_DEFAULT_CACHE_DIR = Path.home() / ".cache" / "bkk" / "krp"


def _effective_out_root(out_root: Path, text_id: str, by_section: bool) -> Path:
    """Apply the ``--by-section`` slicing layer.

    With section slicing, each text's bundle lands at
    ``<out>/<section>/<text-id>/`` (section = ``KRnX`` prefix); without it,
    at ``<out>/<text-id>/``. The KRP and TLS write paths use this once per
    text so every downstream call (writers, divergence reports, manifest
    rebuilds, existing-bundle inspection) sees the same effective root.
    """
    if not by_section:
        return out_root
    from .source import section_prefix
    return out_root / section_prefix(text_id)


def _on_exists_skip(args) -> bool:
    """True iff the user asked us to skip existing bundles.

    Tolerant of test fixtures that synthesize a fake args object without
    setting the attribute — defaults to ``False`` (the pre-existing
    "overwrite" behavior).
    """
    return getattr(args, "on_exists", "overwrite") == "skip"


def _report_skipped(bundle_dir: Path, *, kind: str, label: str) -> None:
    """One-line stderr report for a bundle that was skipped on existence."""
    print(
        f"skipping {kind} {label}: bundle already exists at {bundle_dir}",
        file=sys.stderr,
    )


def _read_kanripo_idno(text_xml: Path) -> str | None:
    """Return the value of the first ``<idno type="kanripo">`` in ``text_xml``.

    Used to resolve a canonical text id to one or more split sub-files whose
    filename stems differ from the canonical id but whose TEI header declares
    it. Returns ``None`` on parse error or when no kanripo idno is present.
    """
    try:
        from lxml import etree
    except ImportError:
        return None
    try:
        tree = etree.parse(str(text_xml), etree.XMLParser(recover=True))
    except Exception:  # noqa: BLE001
        return None
    tei_ns = "http://www.tei-c.org/ns/1.0"
    for idno in tree.iter(f"{{{tei_ns}}}idno"):
        if (idno.get("type") or "").strip().lower() == "kanripo":
            val = (idno.text or "").strip()
            if val:
                return val
    return None


def _split_subfile_glob(text_id: str) -> str | None:
    """Return a glob pattern matching letter-suffix split sub-files for
    ``text_id``, or ``None`` if the id doesn't fit the canonical shape.

    Convention (see ``docs/repair.md``): a canonical id like ``KR2b0007``
    is delivered as ``KR2b007a.xml``, ``KR2b007b.xml``, … — i.e. one
    leading zero is dropped from the trailing digit run and a lowercase
    letter is appended. Mirror that here: strip one zero and append
    ``[a-z]``.
    """
    import re
    m = re.match(r"^(.*?)(0+)(\d*)$", text_id)
    if not m or not m.group(2):
        return None
    head, zeros, tail = m.group(1), m.group(2), m.group(3)
    return f"{head}{zeros[1:]}{tail}[a-z].xml"


def _find_tls_texts(in_root: Path, text_id: str) -> list[Path]:
    """Locate the TLS XML(s) for ``text_id`` under ``<in-root>/tls-texts/data/``.

    Two-step resolution:

    1. Exact filename match (``<text-id>.xml``). The TLS repo subdivides
       texts across classification subdirs, so we glob recursively. If
       multiple exact matches exist the shallowest path wins (warning
       to stderr).
    2. Letter-suffix split sub-files. A handful of TLS texts are split
       across files whose stems carry a trailing letter (``KR2b007a.xml``,
       ``KR2b007b.xml``, …) but whose TEI header declares the same
       canonical ``<idno type="kanripo">`` (``KR2b0007``). When the exact
       match fails, glob the heuristic shape and verify candidates by
       reading their kanripo idno.

    Returns the list of matched paths (empty when nothing resolves).
    """
    base = in_root / "tls-texts" / "data"
    if not base.exists():
        return []

    exact = sorted(base.rglob(f"{text_id}.xml"),
                   key=lambda p: (len(p.parts), str(p)))
    if exact:
        if len(exact) > 1:
            paths = "\n  ".join(str(p) for p in exact)
            print(
                f"warning: multiple matches for {text_id}.xml under {base}; "
                f"using {exact[0]}\n  {paths}",
                file=sys.stderr,
            )
        return [exact[0]]

    pattern = _split_subfile_glob(text_id)
    if pattern is None:
        return []
    candidates = sorted(base.rglob(pattern))
    matches = [p for p in candidates if _read_kanripo_idno(p) == text_id]
    return matches


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bkk.importer")
    p.add_argument("--format", choices=["tls", "krp", "translation"], default=None,
                   help="source format: tls, krp, or translation "
                        "(required; or set import.format in .bkkrc)")
    p.add_argument("--recipe", type=Path, default=None,
                   help="recipe YAML pinning per-text knobs (krp); when given, "
                        "supplies --in/--out/--text-id defaults and overrides "
                        "the auto-discovery path")
    p.add_argument("--in", dest="in_root", type=Path, default=None,
                   help="source root: tls repo, or kanripo mirror "
                        "(parent of <prefix>/<text-id>/ clones)")
    p.add_argument("--out", dest="out_root", type=Path, default=None,
                   help="output directory (bundle written under "
                        "<out>/<text-id>/, or <out>/<section>/<text-id>/ "
                        "with --by-section)")
    p.add_argument("--text-id", default=None, help="single text id (e.g. KR6q0053)")
    p.add_argument("--lang", default=None,
                   help="translation: filter to one BCP-47 language tag "
                        "(e.g. en, fr); applies only to --format translation")
    p.add_argument("--section", default=None,
                   help="krp: import every text under a corpus prefix "
                        "(e.g. KR3a); requires confirmation")
    p.add_argument("--by-section", dest="by_section", action="store_true",
                   default=False,
                   help="slice output by KR sub-section: bundles land under "
                        "<out>/<section>/<text-id>/ (e.g. KR6d/KR6d0001/) so "
                        "large corpora don't crowd a single directory; "
                        "applies to both --format tls and --format krp")
    p.add_argument("--github", dest="github_user", default=None,
                   help=f"krp: github user/org to fetch from "
                        f"(default {_DEFAULT_GITHUB_USER!r} when --in is unset)")
    p.add_argument("--master-branch", default="master",
                   help="krp: branch carrying the curated master (default: master)")
    p.add_argument("--imglist-branch", default="_data",
                   help="krp: branch carrying imglist + imginfo (default: _data)")
    p.add_argument("--cache-dir", type=Path, default=_DEFAULT_CACHE_DIR,
                   help=f"krp: cache for github clones "
                        f"(default: {_DEFAULT_CACHE_DIR})")
    p.add_argument("--yes", action="store_true",
                   help="skip the bulk-import confirmation prompt")
    p.add_argument("--on-exists", dest="on_exists",
                   choices=["overwrite", "skip"], default="overwrite",
                   help="behavior when a target bundle directory already "
                        "exists: 'overwrite' (default; pre-existing "
                        "behavior, including the KRP-into-TLS merge) or "
                        "'skip' (leave the on-disk bundle alone). "
                        "Conflict errors (TLS into existing KRP, unknown "
                        "bundle state) are unaffected.")
    p.add_argument("--sample", type=Path, default=None,
                   help="optional sample tree to diff against; emits a "
                        "divergence-from-sample.md alongside the output")
    p.add_argument("--update-ids", dest="update_ids", action="store_true",
                   default=False,
                   help="tls: when a file's <idno type=\"kanripo\"> differs "
                        "from its filename stem, replace every occurrence of "
                        "the provisional id in the XML before importing "
                        "(default: off)")
    p.add_argument("--dry-run", dest="dry_run", action="store_true",
                   default=False,
                   help="tls: resolve and print the bundle names that would "
                        "be written (with --update-ids remapping applied) "
                        "without writing anything")
    return p


def run(argv: list[str] | None = None) -> int:
    from bkk.config import load_rc
    rc = load_rc()
    g = rc.get("global", {})
    imp = rc.get("import", {})

    parser = build_parser()
    defaults: dict = {}
    if "in" in imp:
        defaults["in_root"] = imp["in"]
    for rc_key, dest in [
        ("out", "out_root"), ("format", "format"), ("cache_dir", "cache_dir"),
        ("github", "github_user"), ("master_branch", "master_branch"),
        ("imglist_branch", "imglist_branch"), ("lang", "lang"),
        ("on_exists", "on_exists"),
    ]:
        if rc_key in imp:
            defaults[dest] = imp[rc_key]
    if imp.get("by_section"):
        defaults["by_section"] = True
    if g.get("skip_confirm") or imp.get("skip_confirm"):
        defaults["yes"] = True
    if defaults:
        parser.set_defaults(**defaults)

    args = parser.parse_args(argv)

    # If --in wasn't supplied by rc or CLI, derive it from the resolved format.
    if args.in_root is None and "in" not in imp and args.format is not None:
        root_key = "krp_root" if args.format == "krp" else "tls_root"
        if root_key in g:
            args.in_root = g[root_key]

    if args.format is None:
        parser.error("--format is required (or set import.format in .bkkrc)")

    if args.format == "tls":
        return _run_tls(args)
    if args.format == "krp":
        return _run_krp(args)
    if args.format == "translation":
        return _run_translation(args)
    print(f"error: unknown format {args.format!r}", file=sys.stderr)
    return 2


def _run_tls(args) -> int:
    """Dispatch the TLS path.

    Two shapes:

    1. ``--text-id`` given → single text, resolved via :func:`_find_tls_text`.
    2. No ``--text-id`` → bulk: walk ``<in>/tls-texts/data/`` for every
       ``<id>.xml`` and import each. Prompts for confirmation unless
       ``--yes`` is set.
    """
    if args.in_root is None or args.out_root is None:
        print("error: --in and --out are required for --format tls",
              file=sys.stderr)
        return 2

    pairs = _resolve_tls_targets(args)
    if not pairs:
        print("error: no texts found to import", file=sys.stderr)
        return 2

    if getattr(args, "dry_run", False):
        return _dry_run_tls(args, pairs)

    # --on-exists skip: drop any text whose TLS bundle already exists on
    # disk before we prompt, so the bulk-confirm prompt only lists the
    # texts that will actually be (re)written. KRP/unknown states are
    # *not* filtered here — those still produce hard errors per-text.
    if _on_exists_skip(args) and args.text_id is None:
        pairs = _skip_filter_tls_pairs(args, pairs)
        if not pairs:
            print("nothing to import: all discovered texts already exist.",
                  file=sys.stderr)
            return 0

    # Bulk-mode confirmation: only when discovery (no --text-id) returned
    # multiple texts. A single --text-id that resolved to multiple split
    # sub-files is one logical text — no prompt.
    if args.text_id is None and len(pairs) > 1 and not args.yes:
        if not _confirm_bulk(pairs):
            print("aborted.", file=sys.stderr)
            return 1

    rc = 0
    canonical_ids: set[str] = set()
    for text_id, text_xml in pairs:
        try:
            canonical = _import_one_tls(
                args, text_id, text_xml,
                sample=args.sample if len(pairs) == 1 else None,
            )
            if canonical:
                canonical_ids.add(canonical)
        except Exception as exc:  # noqa: BLE001 — surface per-text failure, keep going
            print(f"error importing {text_id}: {exc}", file=sys.stderr)
            rc = 1

    # When --text-id resolved to multiple split sub-files, write_bundle
    # has overwritten the bundle's manifest on each call, leaving only
    # the last sub-file's juans listed. Rebuild it once so the manifest
    # reflects every imported part.
    if args.text_id is not None and len(pairs) > 1:
        from bkk.repair.manifest import rebuild_manifests
        for cid in sorted(canonical_ids):
            try:
                effective_out = _effective_out_root(
                    args.out_root, cid, args.by_section,
                )
                rebuild_manifests(effective_out / cid)
                print(
                    f"rebuilt manifest for {cid} "
                    f"(canonical id split across {len(pairs)} sub-files)"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"error rebuilding manifest for {cid}: {exc}",
                      file=sys.stderr)
                rc = 1
    return rc


def _resolve_tls_targets(args) -> list[tuple[str, Path]]:
    """Map TLS CLI flags → ``[(text_id, text_xml), ...]`` to import.

    With ``--text-id``: returns the matching XML(s). Single-element list
    in the common case; multi-element when the canonical id is split
    across letter-suffix sub-files (each sub-file is imported by its own
    filename stem — ``read_tls`` then keys the resulting Bundle by the
    kanripo idno, so all parts collapse into one bundle directory).
    Without ``--text-id``: enumerate every ``<id>.xml`` under
    ``<in>/tls-texts/data/``; skip with a warning any id we can't resolve.
    """
    from . import source

    if args.text_id is not None:
        matches = _find_tls_texts(args.in_root, args.text_id)
        if not matches:
            print(
                f"error: {args.text_id}.xml not found anywhere under "
                f"{args.in_root / 'tls-texts' / 'data'} "
                f"(also searched for split sub-files declaring "
                f"<idno type=\"kanripo\">{args.text_id}</idno>)",
                file=sys.stderr,
            )
            return []
        if len(matches) > 1:
            print(
                f"{args.text_id}: split across {len(matches)} sub-files; "
                f"importing all and rebuilding manifest",
                file=sys.stderr,
            )
        return [(p.stem, p) for p in matches]

    pairs: list[tuple[str, Path]] = []
    for tid in source.list_local_tls_text_ids(args.in_root):
        matches = _find_tls_texts(args.in_root, tid)
        if not matches:
            print(f"warning: skipping {tid}: xml not resolvable",
                  file=sys.stderr)
            continue
        pairs.append((tid, matches[0]))
    return pairs


def _dry_run_tls(args, pairs: list[tuple[str, Path]]) -> int:
    """Print the bundle names that would be written, without writing anything."""
    update_ids = getattr(args, "update_ids", False)
    for text_id, text_xml in pairs:
        canonical_id, effective_xml = _prepare_tls_xml(
            text_xml, text_id, update_ids=update_ids,
        )
        if effective_xml is not text_xml:
            effective_xml.unlink(missing_ok=True)
        if canonical_id != text_id:
            print(f"{text_id} → {canonical_id}")
        else:
            print(canonical_id)
    return 0


def _skip_filter_tls_pairs(
    args, pairs: list[tuple[str, Path]],
) -> list[tuple[str, Path]]:
    """Drop pairs whose target TLS bundle already exists on disk.

    KRP-shaped and unknown-shaped existing bundles are *not* filtered —
    those produce hard errors at write time and should remain visible to
    the user, even under ``--on-exists skip``.
    """
    kept: list[tuple[str, Path]] = []
    skipped = 0
    for tid, text_xml in pairs:
        effective_out = _effective_out_root(
            args.out_root, tid, args.by_section,
        )
        existing = inspect_existing_bundle(effective_out, tid)
        if existing.state == "tls":
            bundle_dir = (
                existing.manifest_path.parent if existing.manifest_path
                else effective_out / tid
            )
            _report_skipped(bundle_dir, kind="tls", label=tid)
            skipped += 1
            continue
        kept.append((tid, text_xml))
    if skipped:
        print(
            f"skipped {skipped} text(s) (already imported)",
            file=sys.stderr,
        )
    return kept


def _rewrite_ids(path: Path, old_id: str, new_id: str) -> Path:
    """Write a temp file with every occurrence of ``old_id`` replaced by
    ``new_id``. Returns the temp path; caller must delete it when done."""
    import os
    import tempfile

    content = path.read_text(encoding="utf-8", errors="replace")
    fd, tmp_str = tempfile.mkstemp(suffix=path.suffix)
    try:
        os.close(fd)
        tmp_path = Path(tmp_str)
        tmp_path.write_text(content.replace(old_id, new_id), encoding="utf-8")
    except Exception:
        os.unlink(tmp_str)
        raise
    return tmp_path


def _prepare_tls_xml(
    text_xml: Path, text_id: str, *, update_ids: bool,
) -> tuple[str, Path]:
    """Return ``(canonical_id, effective_xml_path)`` for import.

    When ``update_ids`` is True and the file's ``<idno type="kanripo">``
    differs from ``text_id``, returns the canonical id and a rewritten temp
    file. The caller must delete the temp file when done (it differs from
    ``text_xml`` iff a rename happened).

    When no renaming is needed, returns ``(text_id, text_xml)`` unchanged.
    """
    kanripo_id = _read_kanripo_idno(text_xml)
    if not kanripo_id or kanripo_id == text_id or not update_ids:
        return text_id, text_xml

    print(
        f"note: {text_id}: replacing provisional id with {kanripo_id} "
        f"before import",
        file=sys.stderr,
    )
    return kanripo_id, _rewrite_ids(text_xml, text_id, kanripo_id)


def _import_one_tls(args, text_id: str, text_xml: Path,
                    *, sample: Path | None) -> str:
    """Run read+write for one TLS text. Returns the canonical text id
    (from the file's ``<idno type="kanripo">``) under which the bundle
    was written — same as ``text_id`` for normal texts, but the parent
    canonical id for letter-suffix split sub-files."""
    # Annotation files are looked up by the provisional id (the filename stem).
    swl_xml = args.in_root / "tls-data" / "notes" / "swl" / f"{text_id}-ann.xml"
    doc_xml = args.in_root / "tls-data" / "notes" / "doc" / f"{text_id}-ann.xml"

    update_ids = getattr(args, "update_ids", False)
    canonical_id, effective_xml = _prepare_tls_xml(
        text_xml, text_id, update_ids=update_ids,
    )
    # When renaming, patch annotation files too: their <seg xml:id="...">
    # attributes carry the provisional id and must match the rewritten body
    # markers for annotations to be linked correctly.
    renamed = effective_xml is not text_xml
    effective_swl = (
        _rewrite_ids(swl_xml, text_id, canonical_id)
        if renamed and swl_xml.exists() else swl_xml
    )
    effective_doc = (
        _rewrite_ids(doc_xml, text_id, canonical_id)
        if renamed and doc_xml.exists() else doc_xml
    )
    try:
        bundle = read_tls(
            effective_xml, effective_swl, effective_doc, canonical_id,
            source_xml=text_xml if renamed else None,
            source_swl=swl_xml if renamed and effective_swl is not swl_xml else None,
            source_doc=doc_xml if renamed and effective_doc is not doc_xml else None,
        )
    finally:
        if renamed:
            effective_xml.unlink(missing_ok=True)
            if effective_swl is not swl_xml:
                effective_swl.unlink(missing_ok=True)
            if effective_doc is not doc_xml:
                effective_doc.unlink(missing_ok=True)

    # Use the canonical id from the bundle (split sub-files collapse onto
    # the parent id) so all parts of one logical text share one section
    # bucket even when the per-file ``text_id`` differs from the canonical.
    effective_out = _effective_out_root(
        args.out_root, bundle.text_id, args.by_section,
    )
    existing = inspect_existing_bundle(effective_out, bundle.text_id)
    if existing.state == "krp":
        bundle_dir = existing.manifest_path.parent
        raise BundleConflictError(
            f"{bundle.text_id}: a KRP-sourced bundle already exists at "
            f"{bundle_dir}. TLS imports must precede KRP. "
            f"Remedy: remove {bundle_dir} and re-import TLS first, "
            f"then KRP."
        )
    if existing.state == "unknown":
        bundle_dir = existing.manifest_path.parent
        raise BundleConflictError(
            f"{bundle.text_id}: a bundle already exists at {bundle_dir} but its "
            f"source can't be classified. Inspect manually or run "
            f"`bkk repair manifest {bundle_dir}` and retry."
        )

    # --on-exists skip: leave a pre-existing TLS bundle untouched. The
    # pre-filter in `_run_tls` already drops these from bulk runs; this
    # guard catches the single-text path (and any caller of
    # `_import_one_tls` directly).
    if existing.state == "tls" and _on_exists_skip(args):
        bundle_dir = (
            existing.manifest_path.parent if existing.manifest_path
            else effective_out / bundle.text_id
        )
        _report_skipped(bundle_dir, kind="tls", label=bundle.text_id)
        return ""

    summary = write_bundle(bundle, effective_out)
    print(
        f"wrote {len(summary['juans'])} juan(s) for {summary['text_id']} "
        f"under {summary['out_root']}"
    )

    if sample is not None:
        _emit_divergence(sample, Path(summary["out_root"]), effective_out)
    return bundle.text_id


# ---------- KRP --------------------------------------------------------------


def _run_krp(args) -> int:
    """Dispatch the KRP path.

    Three shapes:

    1. ``--recipe`` given → legacy: load the recipe verbatim and run it.
       Preserves every existing test fixture.
    2. ``--text-id`` given → single text. Source comes from ``--in`` if set,
       otherwise github.com/<--github|kanripo>/<id>.
    3. No ``--text-id`` → bulk. ``--section`` narrows to one corpus prefix;
       omitting both walks the whole source. Both bulk modes prompt for
       confirmation unless ``--yes`` is set.
    """
    if args.recipe is not None:
        return _run_krp_recipe(args)

    if args.out_root is None:
        print("error: --out is required for --format krp (without --recipe)",
              file=sys.stderr)
        return 2
    if args.text_id is not None and args.section is not None:
        print("error: --text-id and --section are mutually exclusive",
              file=sys.stderr)
        return 2
    if args.in_root is not None and args.github_user is not None:
        print("error: --in and --github are mutually exclusive",
              file=sys.stderr)
        return 2

    # Resolve the (text_id, repo_path) pairs to import.
    try:
        pairs = _resolve_targets(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not pairs:
        print("error: no texts found to import", file=sys.stderr)
        return 2

    # --on-exists skip: drop any text whose bundle already exists on disk
    # (KRP- or TLS-shaped — the merge case is treated as "already there"
    # per the design). Unknown-state bundles are *not* filtered; those
    # still produce a hard error inside `_import_one`.
    if _on_exists_skip(args) and args.text_id is None:
        pairs = _skip_filter_krp_pairs(args, pairs)
        if not pairs:
            print("nothing to import: all discovered texts already exist.",
                  file=sys.stderr)
            return 0

    if len(pairs) > 1 and not args.yes:
        if not _confirm_bulk(pairs):
            print("aborted.", file=sys.stderr)
            return 1

    rc = 0
    for text_id, repo_path in pairs:
        try:
            recipe = _synthesize(args, text_id, repo_path)
            effective_out = _effective_out_root(
                args.out_root, text_id, args.by_section,
            )
            _import_one(
                recipe, effective_out,
                args.sample if len(pairs) == 1 else None,
                on_exists=getattr(args, "on_exists", "overwrite"),
            )
        except Exception as exc:  # noqa: BLE001 — surface per-text failure, keep going
            print(f"error importing {text_id}: {exc}", file=sys.stderr)
            rc = 1
    return rc


def _run_krp_recipe(args) -> int:
    """Legacy path: --recipe is authoritative. Preserves prior behaviour."""
    recipe = load_recipe(args.recipe)
    if recipe.format != "krp":
        print(f"error: recipe at {args.recipe} declares format "
              f"{recipe.format!r}, expected krp", file=sys.stderr)
        return 2

    out_root = args.out_root or recipe.output_bundle
    if out_root is None:
        print("error: no output directory: pass --out or set "
              "`output.bundle` in the recipe", file=sys.stderr)
        return 2

    effective_out = _effective_out_root(
        out_root, recipe.text_id or "", args.by_section,
    )
    _import_one(
        recipe, effective_out, args.sample,
        on_exists=getattr(args, "on_exists", "overwrite"),
    )
    return 0


def _skip_filter_krp_pairs(
    args, pairs: list[tuple[str, Path]],
) -> list[tuple[str, Path]]:
    """Drop pairs whose KRP- or TLS-shaped bundle already exists on disk.

    Unknown-state bundles are *not* filtered: those produce a hard
    error in ``_import_one`` and should remain visible.
    """
    kept: list[tuple[str, Path]] = []
    skipped = 0
    for tid, repo_path in pairs:
        effective_out = _effective_out_root(
            args.out_root, tid, args.by_section,
        )
        existing = inspect_existing_bundle(effective_out, tid)
        if existing.state in ("krp", "tls"):
            bundle_dir = (
                existing.manifest_path.parent if existing.manifest_path
                else effective_out / tid
            )
            _report_skipped(bundle_dir, kind=existing.state, label=tid)
            skipped += 1
            continue
        kept.append((tid, repo_path))
    if skipped:
        print(
            f"skipped {skipped} text(s) (already imported)",
            file=sys.stderr,
        )
    return kept


def _resolve_targets(args) -> list[tuple[str, Path]]:
    """Map CLI flags → ``[(text_id, repo_path), ...]`` to import.

    Resolution rules match the docstring on :func:`_run_krp`. Local sources
    are walked from ``--in``; github sources land in ``--cache-dir``.
    """
    # Lazy import: keeps the CLI startup snappy and lets the tls path run
    # without `requests` installed.
    from . import source

    use_github = args.in_root is None
    github_user = args.github_user or (_DEFAULT_GITHUB_USER if use_github else None)

    if args.text_id:
        if use_github:
            repo = source.resolve_github_repo(
                github_user, args.text_id, args.cache_dir,
            )
        else:
            repo = source.resolve_local_repo(args.in_root, args.text_id)
        return [(args.text_id, repo)]

    # Bulk: list ids first, then resolve each.
    if use_github:
        ids = source.list_github_text_ids(github_user, args.section)
    else:
        ids = source.list_local_text_ids(args.in_root, args.section)

    pairs: list[tuple[str, Path]] = []
    for tid in ids:
        if use_github:
            repo = source.resolve_github_repo(github_user, tid, args.cache_dir)
        else:
            repo = source.resolve_local_repo(args.in_root, tid)
        pairs.append((tid, repo))
    return pairs


def _synthesize(args, text_id: str, repo: Path) -> Recipe:
    from . import source
    return source.synthesize_recipe(
        repo, text_id,
        master_branch=args.master_branch,
        imglist_branch=args.imglist_branch,
    )


def _confirm_bulk(pairs: list[tuple[str, Path]]) -> bool:
    """Print the discovered ids and ask once. Returns True on yes."""
    print(f"about to import {len(pairs)} text(s):", file=sys.stderr)
    for tid, repo in pairs:
        print(f"  {tid}  ({repo})", file=sys.stderr)
    try:
        ans = input(f"Import {len(pairs)} texts? [y/N] ").strip().lower()
    except EOFError:
        return False
    return ans in {"y", "yes"}


def _import_one(
    recipe: Recipe, out_root: Path, sample: Path | None,
    *, on_exists: str = "overwrite",
) -> None:
    """Run read+write for one synthesized or loaded recipe.

    If a TLS-sourced bundle already exists at ``<out_root>/<text-id>/``,
    the KRP master is demoted to a regular edition under
    ``editions/krp/`` and the existing TLS surface is preserved. The
    TLS master manifest's ``editions:`` list is extended with the new
    KRP edition shorts.

    Per the plan, an ``unknown`` state at the destination is treated as
    a conflict (the user must investigate before any KRP write touches
    the directory).

    ``on_exists="skip"`` short-circuits the import when a KRP- or TLS-
    shaped bundle already exists at the destination. Unknown-state
    bundles still error.
    """
    # Lazy: keep the lxml/TLS path independent of the krp reader.
    from .read.krp import read_krp

    text_id = recipe.text_id or ""
    existing = inspect_existing_bundle(out_root, text_id) if text_id else None
    if existing is not None and existing.state == "unknown":
        bundle_dir = existing.manifest_path.parent
        raise BundleConflictError(
            f"{text_id}: a bundle already exists at {bundle_dir} but its "
            f"source can't be classified. Inspect manually or run "
            f"`bkk repair manifest {bundle_dir}` and retry."
        )
    if (
        on_exists == "skip"
        and existing is not None
        and existing.state in ("krp", "tls")
    ):
        bundle_dir = (
            existing.manifest_path.parent if existing.manifest_path
            else out_root / text_id
        )
        _report_skipped(bundle_dir, kind=existing.state, label=text_id)
        return
    merge_into_tls = bool(existing and existing.state == "tls")

    documentary, master = read_krp(recipe)

    protected: set[str] = (
        existing.tls_owned_editions if merge_into_tls else set()
    )
    if merge_into_tls:
        # Defensive: don't let a documentary edition collide with the
        # demoted KRP master's ``krp`` short.
        for bundle in documentary:
            if bundle.edition_short == "krp":
                raise BundleConflictError(
                    f"{text_id}: KRP recipe declares a documentary edition "
                    f"with short 'krp', which would collide with the "
                    f"demoted KRP master in merge mode. Rename the witness."
                )

    new_edition_entries: list[dict] = []

    for bundle in documentary:
        if bundle.edition_short in protected:
            print(
                f"skipping KRP edition {bundle.edition_short} for "
                f"{bundle.text_id}: short already owned by the TLS surface "
                f"under editions/{bundle.edition_short}/",
                file=sys.stderr,
            )
            continue
        s = write_krp_edition(bundle, out_root)
        print(
            f"wrote {len(s['juans'])} juan(s) for {s['text_id']} "
            f"edition {s['edition']} under {s['out_root']}"
        )
        entry: dict = {"short": bundle.edition_short}
        label = bundle.metadata.get("edition_label")
        if label:
            entry["label"] = label
        new_edition_entries.append(entry)

    if master is not None:
        if merge_into_tls:
            # Demote: write the synthesized master as a regular edition
            # under ``editions/krp/``, preserving variant + witness
            # page-break markers. PUA-map still belongs at the bundle root.
            if master.edition_short in protected:
                raise BundleConflictError(
                    f"{text_id}: TLS surface already owns "
                    f"editions/{master.edition_short}/, but the demoted "
                    f"KRP master would land there. Resolve manually."
                )
            s = write_krp_edition(master, out_root)
            pua_filename = write_pua_map(master, out_root)
            print(
                f"wrote {len(s['juans'])} juan(s) for {s['text_id']} "
                f"krp-master demoted to edition {s['edition']} under "
                f"{s['out_root']}"
                + (f" (+ {pua_filename})" if pua_filename else "")
            )
            new_edition_entries.append({"short": master.edition_short})
        else:
            s = write_krp_master(master, out_root)
            print(
                f"wrote {len(s['juans'])} juan(s) for {s['text_id']} "
                f"master under {s['out_root']}"
                + (f" (+ {s['pua_map']})" if "pua_map" in s else "")
            )

    if merge_into_tls and new_edition_entries:
        final = extend_master_editions(
            existing.manifest_path, new_edition_entries,
        )
        print(
            f"updated {existing.manifest_path.name}: editions list now "
            f"{[e.get('short') for e in final if isinstance(e, dict)]}"
        )

        # Project KRP master's apparatus (variants + witness page-breaks)
        # onto the TLS surface so the reading text carries the union of
        # every edition's apparatus, not just the TLS-side punctuation.
        # The TLS surface keeps a single TLS-owned documentary edition;
        # use its short to preserve the existing canonical_identifier.
        surface_short = (
            next(iter(existing.tls_owned_editions))
            if existing.tls_owned_editions else "bkk"
        )
        proj = project_krp_apparatus_onto_tls(
            out_root, text_id, surface_short, master, documentary,
        )
        print(
            f"projected onto {text_id} surface: "
            f"{proj['variants_added']} variants, "
            f"{proj['page_breaks_added']} witness page-breaks"
        )

    if sample is not None and text_id:
        ours_root = out_root / text_id
        _emit_divergence(sample, ours_root, out_root)


# ---------- Translation ----------------------------------------------------


# Filename grammar: <text-id>-<lang>[-<tail>]
#   text-id: uppercase-letter prefix + alphanumerics (KR1h0004, CH7x2024,
#            T48n2016, EX1a0001, B…). Lazy so the first '-' boundary wins.
#   lang:    2-3 lowercase letters (en, fr, ja, com, ogr, …). Real-world
#            TLS filenames carry no BCP-47 region/variant subtag here; any
#            extra dash-separated token (variant like 'ku', translator
#            code like 'ge'/'ds'/'oa', revision hash) lands in <tail>.
#   tail:    optional free-form suffix preserved verbatim in the bundle id.
_TRANSLATION_STEM_RE = re.compile(
    r"^(?P<text>[A-Z][A-Za-z0-9]+?)-"
    r"(?P<lang>[a-z]{2,3})"
    r"(?:-(?P<tail>.+))?$"
)


def _run_translation(args) -> int:
    """Dispatch the translation path.

    Walks ``<in>/tls-data/translations/`` for ``<text-id>-<lang>[-<rev>].xml``
    files and imports each as its own bundle under
    ``<out>/translations/<file-stem>/``. ``--text-id`` and ``--lang``
    narrow the discovery set; both are optional.
    """
    if args.in_root is None or args.out_root is None:
        print("error: --in and --out are required for --format translation",
              file=sys.stderr)
        return 2

    paths = _resolve_translation_targets(args)
    if not paths:
        print("error: no translation files found to import", file=sys.stderr)
        return 2

    # --on-exists skip: filename match already gives us text-id, lang and
    # the bundle id (== stem), so we can compute the expected output
    # directory without parsing the XML.
    if _on_exists_skip(args):
        paths = _skip_filter_translation_paths(args, paths)
        if not paths:
            print(
                "nothing to import: all discovered bundles already exist.",
                file=sys.stderr,
            )
            return 0

    if len(paths) > 1 and not args.yes:
        if not _confirm_bulk([(p.stem, p) for p in paths]):
            print("aborted.", file=sys.stderr)
            return 1

    rc = 0
    for xml_path in paths:
        try:
            _import_one_translation(args, xml_path)
        except Exception as exc:  # noqa: BLE001
            print(f"error importing {xml_path.name}: {exc}", file=sys.stderr)
            rc = 1
    return rc


def _skip_filter_translation_paths(
    args, paths: list[Path],
) -> list[Path]:
    """Drop translation files whose bundle dir already exists on disk."""
    from .write.translation import translation_bundle_dir

    kept: list[Path] = []
    skipped = 0
    for xml_path in paths:
        m = _TRANSLATION_STEM_RE.match(xml_path.stem)
        if not m:
            kept.append(xml_path)
            continue
        bundle_dir = translation_bundle_dir(
            args.out_root,
            source_text_id=m.group("text"),
            language=m.group("lang"),
            bundle_id=xml_path.stem,
            by_section=args.by_section,
        )
        if bundle_dir.exists():
            _report_skipped(
                bundle_dir, kind="translation", label=xml_path.stem,
            )
            skipped += 1
            continue
        kept.append(xml_path)
    if skipped:
        print(
            f"skipped {skipped} bundle(s) (already imported)",
            file=sys.stderr,
        )
    return kept


def _resolve_translation_targets(args) -> list[Path]:
    """Map translation CLI flags → list of XML paths to import."""
    base = args.in_root / "tls-data" / "translations"
    if not base.exists():
        print(
            f"error: {base} does not exist "
            f"(expected <in>/tls-data/translations/)",
            file=sys.stderr,
        )
        return []

    lang_filter = (args.lang or "").strip() or None
    text_filter = (args.text_id or "").strip() or None

    matches: list[Path] = []
    for path in sorted(base.rglob("*.xml")):
        m = _TRANSLATION_STEM_RE.match(path.stem)
        if not m:
            continue
        if text_filter and m.group("text") != text_filter:
            continue
        if lang_filter and m.group("lang") != lang_filter:
            continue
        matches.append(path)
    return matches


def _import_one_translation(args, xml_path: Path) -> None:
    """Read one translation XML and write its bundle."""
    from .read.translation import read_translation
    from .write.translation import translation_bundle_dir, write_translation

    m = _TRANSLATION_STEM_RE.match(xml_path.stem)
    lang_hint = m.group("lang") if m else None

    bundle = read_translation(
        xml_path,
        language_hint=lang_hint,
        bundle_id_hint=xml_path.stem,
    )

    # --on-exists skip: per-text guard, mirrors the bulk pre-filter so
    # direct callers and single-file invocations are protected too.
    if _on_exists_skip(args):
        bundle_dir = translation_bundle_dir(
            args.out_root,
            source_text_id=bundle.source_text_id,
            language=bundle.language,
            bundle_id=bundle.bundle_id,
            by_section=args.by_section,
        )
        if bundle_dir.exists():
            _report_skipped(
                bundle_dir, kind="translation", label=bundle.bundle_id,
            )
            return

    source_bundle_root = _effective_out_root(
        args.out_root, bundle.source_text_id, args.by_section,
    )
    summary = write_translation(
        bundle, args.out_root,
        source_bundle_root=source_bundle_root,
        by_section=args.by_section,
    )
    print(
        f"wrote translation bundle {summary['bundle_id']} "
        f"({len(summary['juans'])} juan file(s)) under {summary['out_root']}"
    )


def _emit_divergence(sample: Path, ours_root: Path, out_root: Path) -> None:
    divergences = diff_trees(sample, ours_root)
    report = render_report(divergences)
    report_path = out_root / "divergence-from-sample.md"
    report_path.write_text(report, encoding="utf-8")
    unexpected = sum(1 for d in divergences if d.status == "unexpected")
    expected = len(divergences) - unexpected
    print(
        f"divergence report at {report_path} "
        f"({expected} expected, {unexpected} unexpected)"
    )


def main() -> None:
    raise SystemExit(run())
