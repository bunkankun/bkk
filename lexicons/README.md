# BKK AT Protocol lexicons

This directory holds the AT Protocol lexicons that govern BKK's records on
Bluesky. Four NSIDs cover the three contribution kinds plus one shared
anchor definition:

| NSID | File | Kind |
|---|---|---|
| `org.bunkankun.defs.anchor` | [`org.bunkankun.defs.anchor.json`](org.bunkankun.defs.anchor.json) | shared `#main` anchor def |
| `org.bunkankun.annotation.note` | [`org.bunkankun.annotation.note.json`](org.bunkankun.annotation.note.json) | structured annotation (form/sense/concept/…) |
| `org.bunkankun.comment.post` | [`org.bunkankun.comment.post.json`](org.bunkankun.comment.post.json) | markdown comment on a passage or a record |
| `org.bunkankun.translation.segment` | [`org.bunkankun.translation.segment.json`](org.bunkankun.translation.segment.json) | inline translation of one anchored span |

All record lexicons reference the shared anchor via
`lex:org.bunkankun.defs.anchor#main`. The hierarchical naming (record type
as leaf) leaves room for sibling schemas under each namespace (e.g.
`org.bunkankun.annotation.update`, `org.bunkankun.comment.reaction`) without
crowding the parent NSID.

## Status: drafted, not yet published

These lexicons are **drafted** and used in production by `bkk serve` (post
path) and `bkk annotations harvest` (read path), but they are **not yet
resolvable** through Bluesky's relay because the authority DID for
`bunkankun.org` has not been provisioned.

Until that happens, records under these NSIDs only flow through the
private polling loop (see
[`docs/bkk-annotations/workflow.md`](../docs/bkk-annotations/workflow.md));
the public firehose / Jetstream drops them silently.

### Path to publication

1. Confirm ownership of `bunkankun.org`.
2. Provision an authority DID (`did:web:bunkankun.org` is simplest;
   `did:plc` via Bluesky is more portable).
3. Publish the DID document (for `did:web`, at
   `https://bunkankun.org/.well-known/did.json`).
4. Post each lexicon as a `com.atproto.lexicon.schema` record under that
   DID (one record per NSID, including `defs.anchor`).
5. Verify a Jetstream subscriber sees records with our NSIDs end-to-end.
6. Swap [`module/bkk/serve/contributions_feed.py`](../module/bkk/serve/contributions_feed.py)
   from per-DID polling to a Jetstream subscriber (the previous
   subscriber implementation lives in git history just before the polling
   rewrite).

## Round-trip coverage

Every wire shape has matching converters in two places:

- post path: `_annotation_archive_to_wire`, `_comment_archive_to_wire`,
  `_translation_archive_to_wire` in
  [`module/bkk/serve/routers/annotations_write.py`](../module/bkk/serve/routers/annotations_write.py).
- harvest path: `annotation_wire_to_archive`, `comment_wire_to_archive`,
  `translation_wire_to_archive` in
  [`module/bkk/annotations/harvest.py`](../module/bkk/annotations/harvest.py).

[`module/tests/test_bsky_lexicon_roundtrip.py`](../module/tests/test_bsky_lexicon_roundtrip.py)
verifies that an archive record round-tripped through wire and back is
preserved on every stable field, for all three kinds plus the legacy flat
NSID.

## Legacy NSID

Records posted before the rename to hierarchical NSIDs live under the flat
NSID `org.bunkankun.annotation`. These cannot be rewritten in-place, so
the harvester reads both during a transition window: the legacy collection
is polled and its records are tagged with the new `annotation.note`
`source_role`, then fold into the same JSONL archive as the new records.
See [`LEGACY_ANNOTATION_NSID`](../module/bkk/serve/atproto.py).
