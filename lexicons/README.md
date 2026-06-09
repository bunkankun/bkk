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

## Status: published

These lexicons are **published** under the authority DID
`did:plc:bqv4y6ootthsrh6pdkpqhq73` and resolve via `_lexicon` TXT records
on `bunkankun.org`. The relay propagates records under all four NSIDs;
[`module/bkk/serve/contributions_feed.py`](../module/bkk/serve/contributions_feed.py)
subscribes to Jetstream directly.

### DNS resolution (per group)

`goat lex status` resolves NSID **groups** (all segments except the leaf
record name), so each group needs its own TXT record:

| Group | DNS name | Value |
|---|---|---|
| `org.bunkankun.annotation` | `_lexicon.annotation.bunkankun.org` | `did:plc:bqv4y6ootthsrh6pdkpqhq73` |
| `org.bunkankun.comment` | `_lexicon.comment.bunkankun.org` | `did:plc:bqv4y6ootthsrh6pdkpqhq73` |
| `org.bunkankun.defs` | `_lexicon.defs.bunkankun.org` | `did:plc:bqv4y6ootthsrh6pdkpqhq73` |
| `org.bunkankun.translation` | `_lexicon.translation.bunkankun.org` | `did:plc:bqv4y6ootthsrh6pdkpqhq73` |

A root-only TXT at `_lexicon.bunkankun.org` is **not enough** — clients
look up the group-specific name, not a parent. If a future schema group
(e.g. `org.bunkankun.reaction`) lands, add a matching TXT.

Verify end-to-end with:

```bash
goat lex status
```

All four NSIDs should be green; if any go orange, check the TXT record
for that group's DNS name.

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
