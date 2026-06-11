"""List every BKK record on bsky for the DIDs in .bkkrc.

Walks ``com.atproto.repo.listRecords`` for each DID across all four BKK
collections (annotation.note, comment.post, translation.segment, plus the
legacy flat annotation NSID) and prints one line per record so you can
eyeball ``marker_id`` / ``text_id`` and hand the at-URI to
``bkk annotations delete --uri ...``.

Usage:
    python scripts/list_bsky_records.py            # all DIDs from .bkkrc
    python scripts/list_bsky_records.py --did did:plc:...   # override
    python scripts/list_bsky_records.py --json     # JSONL output
"""

from __future__ import annotations

import argparse
import json
import sys

from bkk.annotations.pds import resolve_pds
from bkk.config import load_rc
from bkk.serve.atproto import (
    ANNOTATION_NSID,
    COMMENT_NSID,
    CURATION_NSID,
    LEGACY_ANNOTATION_NSID,
    TRANSLATION_NSID,
    list_records,
)


COLLECTIONS = (
    ANNOTATION_NSID,
    LEGACY_ANNOTATION_NSID,
    COMMENT_NSID,
    TRANSLATION_NSID,
    CURATION_NSID,
)


def _iter_records(did: str, collection: str):
    service = resolve_pds(did)
    cursor: str | None = None
    while True:
        result = list_records(
            service=service, repo=did, collection=collection,
            limit=100, cursor=cursor,
        )
        for rec in result.get("records") or []:
            if isinstance(rec, dict):
                yield rec
        cursor = result.get("cursor")
        if not cursor:
            return


def _summary(collection: str, value: dict) -> str:
    text_id = value.get("textId") or ""
    edition = value.get("edition") or ""
    anchor = value.get("anchor") if isinstance(value.get("anchor"), dict) else {}
    marker_id = anchor.get("markerId") or ""
    if collection == CURATION_NSID:
        target = value.get("target") if isinstance(value.get("target"), dict) else {}
        return f"state={value.get('state')} target={target.get('uri')}"
    if collection == COMMENT_NSID:
        body = value.get("body") or ""
        snippet = body[:60].replace("\n", " ")
        return f"text_id={text_id} marker_id={marker_id} body={snippet!r}"
    if collection == TRANSLATION_NSID:
        text = value.get("text") or ""
        snippet = text[:60].replace("\n", " ")
        return (
            f"text_id={text_id} edition={edition} marker_id={marker_id} "
            f"translation_id={value.get('translationId')} text={snippet!r}"
        )
    # annotation
    payload = value.get("payload") if isinstance(value.get("payload"), dict) else {}
    orth = payload.get("orth") or ""
    return f"text_id={text_id} edition={edition} marker_id={marker_id} orth={orth!r}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--did", action="append", default=None,
        help="DID to list (repeatable). Default: [annotations].dids in .bkkrc.",
    )
    parser.add_argument(
        "--collection", action="append", default=None,
        help=f"Limit to one NSID (repeatable). Default: all of {list(COLLECTIONS)}.",
    )
    parser.add_argument("--json", action="store_true", help="emit JSONL")
    args = parser.parse_args(argv)

    dids = args.did
    if not dids:
        rc = load_rc()
        dids = rc.get("annotations", {}).get("dids") or []
    if not dids:
        print("error: no DIDs (pass --did or set [annotations].dids in .bkkrc)",
              file=sys.stderr)
        return 2

    collections = tuple(args.collection) if args.collection else COLLECTIONS

    total = 0
    for did in dids:
        for collection in collections:
            try:
                records = list(_iter_records(did, collection))
            except Exception as exc:
                print(f"# listRecords({collection}) failed for {did}: {exc}",
                      file=sys.stderr)
                continue
            for rec in records:
                value = rec.get("value") or {}
                uri = rec.get("uri")
                cid = rec.get("cid")
                total += 1
                if args.json:
                    json.dump(
                        {"did": did, "collection": collection, "uri": uri,
                         "cid": cid, "value": value},
                        sys.stdout, ensure_ascii=False,
                    )
                    sys.stdout.write("\n")
                else:
                    print(f"{collection}\t{uri}\t{_summary(collection, value)}")
    if not args.json:
        print(f"# {total} records across {len(dids)} DID(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
