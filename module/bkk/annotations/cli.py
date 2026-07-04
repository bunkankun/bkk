"""``bkk annotations`` CLI."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from bkk.config import load_rc
from bkk.short_refs import text_id_arg

from . import delete as _delete
from .harvest import harvest
from .validate import (
    DEFAULT_SEARCH_WINDOW,
    format_text_summary,
    run as run_validate,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bkk annotations",
        description="Manage the bkk-annotations archive (harvest, validate, repair).",
    )
    sub = p.add_subparsers(dest="subcommand", required=True)

    h = sub.add_parser("harvest", help="pull records from one or more DIDs and merge into the archive")
    h.add_argument("--did", action="append", default=None,
                   help="DID to harvest from; repeatable. Defaults to [annotations].dids in .bkkrc.")
    h.add_argument("--annotations-root", type=Path, default=None,
                   help="archive root (default: [annotations].annotations_root or [serve].annotations_root)")
    h.add_argument("--comments-root", type=Path, default=None,
                   help="comments archive root "
                        "(default: [annotations].comments_root, else <annotations-root>/../bkk-comments)")
    h.add_argument("--translations-root", type=Path, default=None,
                   help="translations archive root "
                        "(default: [annotations].translations_root, else <annotations-root>/../bkk-translations)")
    h.add_argument("--corpus", type=Path, default=None,
                   help="corpus root (default: [global].corpus)")
    h.add_argument("--limit", type=int, default=None,
                   help="max records per DID (default: unlimited)")
    h.add_argument("--dry-run", action="store_true",
                   help="report counts without writing files")
    h.add_argument("--verbose", "-v", action="store_true", help="log harvest progress")

    d = sub.add_parser("delete", help="hard-delete records from bsky and the archive")
    sel = d.add_mutually_exclusive_group(required=True)
    sel.add_argument("--uri", help="at-URI ``at://did/collection/rkey``")
    sel.add_argument("--cid", help="provenance.cid of the record")
    sel.add_argument("--id", dest="record_id", help="archive ``id`` field")
    sel.add_argument("--rejected", action="store_true",
                     help="delete every archive row currently flagged "
                          "curation_state=rejected (bulk)")
    d.add_argument("--annotations-root", type=Path, default=None,
                   help="archive root (default: [annotations].annotations_root or [serve].annotations_root)")
    d.add_argument("--comments-root", type=Path, default=None,
                   help="comments archive root (default: [annotations].comments_root)")
    d.add_argument("--translations-root", type=Path, default=None,
                   help="translations archive root (default: [annotations].translations_root)")
    d.add_argument("--archive-only", action="store_true",
                   help="skip the bsky deleteRecord call (use when the bsky record is gone or was never published)")
    d.add_argument("--remote-only", action="store_true",
                   help="delete on bsky but leave the archive alone; "
                        "with --uri, also works when the record never made it into the archive")
    d.add_argument("--dry-run", action="store_true",
                   help="report what would be deleted, do nothing")
    d.add_argument("--handle", default=None,
                   help="Bluesky handle (default: $BKK_BLUESKY_HANDLE)")
    d.add_argument("--app-password", default=None,
                   help="Bluesky app password (default: $BKK_BLUESKY_APP_PASSWORD)")

    for name, help_text in (
        ("validate", "check that each archived annotation's orth matches the body at its anchor"),
        ("repair",   "validate + rewrite records whose anchor can be shifted to a unique nearby match"),
    ):
        s = sub.add_parser(name, help=help_text)
        s.add_argument("text_id", nargs="?", default=None, type=text_id_arg,
                       help="restrict to a single text id (default: scan whole archive)")
        s.add_argument("--annotations-root", type=Path, default=None,
                       help="archive root (default: [annotations].annotations_root or [serve].annotations_root)")
        s.add_argument("--corpus", type=Path, default=None,
                       help="corpus root (default: [global].corpus)")
        s.add_argument("--window", type=int, default=DEFAULT_SEARCH_WINDOW,
                       help=f"chars to search either side of the cached offset (default: {DEFAULT_SEARCH_WINDOW})")
        s.add_argument("--json", action="store_true",
                       help="emit per-finding JSONL on stdout in addition to the summary")
        s.add_argument("--max-findings", type=int, default=25,
                       help="cap the number of findings printed in the text summary (default: 25)")
        s.add_argument("--verbose", "-v", action="store_true", help="log progress")
        s.add_argument("--quiet", action="store_true",
                       help="suppress the per-file stderr progress line")
        if name == "repair":
            s.add_argument("--write", action="store_true",
                           help="actually rewrite files (default: dry-run, report only)")

    return p


def _cmd_harvest(args: argparse.Namespace) -> int:
    rc = load_rc()
    g = rc.get("global", {})
    ann_rc = rc.get("annotations", {})
    serve_rc = rc.get("serve", {})

    dids = args.did or ann_rc.get("dids") or []
    if isinstance(dids, str):
        print(
            f"error: [annotations].dids must be a YAML list, got scalar {dids!r}. "
            "Use:\n  dids:\n    - did:plc:...",
            file=sys.stderr,
        )
        return 2
    if not dids:
        print("error: no DIDs to harvest "
              "(pass --did or set [annotations].dids in .bkkrc)", file=sys.stderr)
        return 2

    annotations_root = (
        args.annotations_root
        or ann_rc.get("annotations_root")
        or serve_rc.get("annotations_root")
    )
    if annotations_root is None:
        print("error: no archive root configured "
              "(pass --annotations-root or set [serve].annotations_root)",
              file=sys.stderr)
        return 2
    annotations_root = Path(annotations_root)

    comments_root = args.comments_root or ann_rc.get("comments_root")
    translations_root = args.translations_root or ann_rc.get("translations_root")

    corpus_root = args.corpus or g.get("corpus")
    if corpus_root is None:
        print("error: no corpus configured "
              "(pass --corpus or set [global].corpus)", file=sys.stderr)
        return 2
    corpus_root = Path(corpus_root)

    summary = harvest(
        dids=list(dids),
        annotations_root=annotations_root,
        comments_root=Path(comments_root) if comments_root else None,
        translations_root=Path(translations_root) if translations_root else None,
        corpus_root=corpus_root,
        limit_per_did=args.limit,
        dry_run=args.dry_run,
    )
    json.dump(summary, sys.stdout)
    sys.stdout.write("\n")
    return 0


def _resolve_archive_roots(args: argparse.Namespace) -> dict[str, Path | None]:
    """Resolve annotation / comment / translation roots from args + .bkkrc."""
    rc = load_rc()
    ann_rc = rc.get("annotations", {})
    serve_rc = rc.get("serve", {})

    annotations_root = (
        args.annotations_root
        or ann_rc.get("annotations_root")
        or serve_rc.get("annotations_root")
    )
    comments_root = getattr(args, "comments_root", None) or ann_rc.get("comments_root")
    translations_root = (
        getattr(args, "translations_root", None) or ann_rc.get("translations_root")
    )
    return {
        "annotations_root": Path(annotations_root) if annotations_root else None,
        "comments_root": Path(comments_root) if comments_root else None,
        "translations_root": Path(translations_root) if translations_root else None,
    }


def _cmd_delete(args: argparse.Namespace) -> int:
    if args.archive_only and args.remote_only:
        print("error: --archive-only and --remote-only are mutually exclusive",
              file=sys.stderr)
        return 2

    roots = _resolve_archive_roots(args)
    if roots["annotations_root"] is None:
        print("error: no archive root configured "
              "(pass --annotations-root or set [serve].annotations_root)",
              file=sys.stderr)
        return 2

    if args.rejected:
        hits = _delete.find_rejected(**roots)
        if not hits:
            print(json.dumps({"status": "nothing_to_do", "rejected_count": 0}))
            return 0
    else:
        hit = _delete.locate(
            uri=args.uri, cid=args.cid, record_id=args.record_id, **roots,
        )
        if hit is None:
            if args.remote_only and args.uri:
                # Record was never archived (e.g. harvest rejected it for a
                # bad marker_id). Synthesize a hit so the remote-delete path
                # runs against the supplied at-URI directly.
                hit = _delete.ArchiveHit(
                    path=Path("<remote-only>"),
                    record={"provenance": {"uri": args.uri, "cid": args.cid}},
                    kind=_delete.KIND_ANNOTATION,
                )
            else:
                print(json.dumps({"status": "not_found"}))
                return 1
        hits = [hit]

    # Open the bsky session once, lazily, if any candidate needs a remote
    # delete. Iterating fresh-auth per record would cost an extra
    # createSession round-trip each time.
    auth: _RemoteAuth | None = None
    needs_remote = (
        not args.archive_only
        and not args.dry_run
        and any(_delete.is_bsky_native(h.record) for h in hits)
    )
    if needs_remote:
        try:
            auth = _open_remote_session(args)
        except _CliError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return exc.code

    results: list[dict] = []
    overall_status = "ok"
    for h in hits:
        rec_result = _delete_one(h, args=args, auth=auth)
        if rec_result.get("status") == "error":
            overall_status = "error"
        results.append(rec_result)

    if args.rejected:
        json.dump({
            "status": overall_status,
            "rejected_count": len(hits),
            "results": results,
        }, sys.stdout, ensure_ascii=False)
    else:
        json.dump(results[0], sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0 if overall_status == "ok" else 1


class _CliError(Exception):
    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


class _RemoteAuth:
    """Cached bsky session reused across hits in a bulk delete."""

    def __init__(self, *, did: str, access_jwt: str, refresh_jwt: str, service: str):
        self.did = did
        self.access_jwt = access_jwt
        self.refresh_jwt = refresh_jwt
        self.service = service


def _open_remote_session(args: argparse.Namespace) -> _RemoteAuth:
    handle = args.handle or os.environ.get("BKK_BLUESKY_HANDLE")
    app_password = args.app_password or os.environ.get("BKK_BLUESKY_APP_PASSWORD")
    if not handle or not app_password:
        raise _CliError(
            "remote delete needs --handle/--app-password "
            "(or BKK_BLUESKY_HANDLE / BKK_BLUESKY_APP_PASSWORD)",
            code=2,
        )
    from bkk.serve.atproto import create_session
    from .pds import resolve_pds

    sess = create_session(handle, app_password)
    access_jwt = sess.get("accessJwt")
    refresh_jwt = sess.get("refreshJwt")
    session_did = sess.get("did")
    if not isinstance(access_jwt, str) or not isinstance(refresh_jwt, str):
        raise _CliError("atproto createSession returned no JWTs", code=1)
    if not isinstance(session_did, str):
        raise _CliError("atproto createSession returned no DID", code=1)
    return _RemoteAuth(
        did=session_did,
        access_jwt=access_jwt,
        refresh_jwt=refresh_jwt,
        service=resolve_pds(session_did),
    )


def _delete_one(
    hit: _delete.ArchiveHit,
    *,
    args: argparse.Namespace,
    auth: _RemoteAuth | None,
) -> dict:
    prov = hit.record.get("provenance") or {}
    record_uri = prov.get("uri") if isinstance(prov.get("uri"), str) else None
    actions: list[dict] = []
    status = "ok"

    is_native = _delete.is_bsky_native(hit.record)
    do_remote = not args.archive_only and is_native
    if not args.archive_only and not is_native:
        actions.append({"action": "skipped_remote", "reason": "synth or no at-uri"})

    if do_remote:
        assert record_uri is not None
        if args.dry_run:
            actions.append({"action": "would_delete_remote", "uri": record_uri})
        elif auth is None:
            actions.append({"action": "skipped_remote", "reason": "no session"})
            status = "error"
        else:
            try:
                did, collection, rkey = _delete.parse_at_uri(record_uri)
            except ValueError as exc:
                actions.append({"action": "skipped_remote", "reason": str(exc)})
                status = "error"
            else:
                if did != auth.did:
                    actions.append({
                        "action": "skipped_remote",
                        "reason": f"session DID {auth.did!r} does not own {did!r}",
                    })
                    status = "error"
                else:
                    from bkk.serve.atproto import delete_record
                    try:
                        delete_record(
                            service=auth.service,
                            access_jwt=auth.access_jwt,
                            refresh_jwt=auth.refresh_jwt,
                            repo=did,
                            collection=collection,
                            rkey=rkey,
                        )
                    except Exception as exc:
                        actions.append({"action": "remote_delete_failed", "error": str(exc)})
                        status = "error"
                    else:
                        actions.append({"action": "deleted_remote", "uri": record_uri})

    if not args.remote_only and status != "error":
        if args.dry_run:
            actions.append({"action": "would_remove_from", "path": str(hit.path)})
        elif _delete.archive_remove(hit):
            actions.append({"action": "removed_from", "path": str(hit.path)})
        else:
            actions.append({"action": "archive_unchanged", "path": str(hit.path)})

    return {
        "status": status,
        "kind": hit.kind,
        "path": str(hit.path),
        "uri": record_uri,
        "cid": prov.get("cid"),
        "id": hit.record.get("id"),
        "actions": actions,
    }


def _resolve_roots(args: argparse.Namespace) -> tuple[Path, Path] | int:
    """Shared root resolution for validate/repair. Returns 2 on error."""
    rc = load_rc()
    g = rc.get("global", {})
    ann_rc = rc.get("annotations", {})
    serve_rc = rc.get("serve", {})

    annotations_root = (
        args.annotations_root
        or ann_rc.get("annotations_root")
        or serve_rc.get("annotations_root")
    )
    if annotations_root is None:
        print("error: no archive root configured "
              "(pass --annotations-root or set [serve].annotations_root)",
              file=sys.stderr)
        return 2

    corpus_root = args.corpus or g.get("corpus")
    if corpus_root is None:
        print("error: no corpus configured "
              "(pass --corpus or set [global].corpus)", file=sys.stderr)
        return 2
    return Path(annotations_root), Path(corpus_root)


def _cmd_validate_or_repair(args: argparse.Namespace, *, write: bool) -> int:
    roots = _resolve_roots(args)
    if isinstance(roots, int):
        return roots
    annotations_root, corpus_root = roots
    if not annotations_root.is_dir():
        print(f"error: annotations root not found: {annotations_root}", file=sys.stderr)
        return 2
    if not corpus_root.is_dir():
        print(f"error: corpus root not found: {corpus_root}", file=sys.stderr)
        return 2

    def _emit_progress(line: str) -> None:
        print(line, file=sys.stderr, flush=True)

    summary = run_validate(
        annotations_root,
        corpus_root,
        text_id_filter=args.text_id,
        write=write,
        window=args.window,
        progress=None if args.quiet else _emit_progress,
    )

    if args.json:
        for f in summary.findings:
            if f.status in ("ok", "no_orth"):
                continue
            sys.stdout.write(json.dumps({
                "text_id": f.text_id,
                "juan_seq": f.juan_seq,
                "id": f.annotation_id,
                "marker_id": f.marker_id,
                "status": f.status,
                "bucket": f.bucket,
                "bucket_offset": f.bucket_offset,
                "anchor_offset": f.anchor_offset,
                "orth": f.orth,
                "found_at_offset": f.found_at_offset,
                "proposed_bucket_offset": f.proposed_bucket_offset,
                "delta": f.delta,
                "detail": f.detail,
            }, ensure_ascii=False))
            sys.stdout.write("\n")

    print(format_text_summary(summary, max_findings=args.max_findings))
    has_problems = any(
        k not in ("ok", "no_orth") for k in summary.by_status
    )
    return 1 if has_problems and not write else 0


def run(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "verbose", False):
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.subcommand == "harvest":
        return _cmd_harvest(args)
    if args.subcommand == "delete":
        return _cmd_delete(args)
    if args.subcommand == "validate":
        return _cmd_validate_or_repair(args, write=False)
    if args.subcommand == "repair":
        return _cmd_validate_or_repair(args, write=getattr(args, "write", False))
    parser.error(f"unknown subcommand: {args.subcommand}")
    return 2


__all__ = ["run"]
