# Annotation post / harvest workflow

This document covers the runtime pipeline that ferries an annotation from the
SPA, through a Bluesky PDS, and into the `bkk-annotations` archive. For the
on-disk record format see `README.md` in this folder; for the broader plan see
`docs/bkk-annotations-four-layers-plan.md`.

```text
SPA selection
   │   POST /api/annotations
   ▼
bkk serve  ──►  com.atproto.repo.createRecord  ──►  user's PDS
                                                      │
                                                      │  bkk annotations harvest
                                                      ▼
                                          com.atproto.repo.listRecords
                                                      │
                                                      ▼
                                       <annotations_root>/<text>/<text>_NNN.ann.jsonl
```

## Lexicons

Three hierarchical NSIDs cover the three contribution kinds, plus one
shared anchor def:

| NSID | Purpose | File |
|---|---|---|
| `org.bunkankun.defs.anchor` | shared `#main` anchor object | [`lexicons/org.bunkankun.defs.anchor.json`](../../lexicons/org.bunkankun.defs.anchor.json) |
| `org.bunkankun.annotation.note` | structured annotation payload | [`lexicons/org.bunkankun.annotation.note.json`](../../lexicons/org.bunkankun.annotation.note.json) |
| `org.bunkankun.comment.post` | markdown comment on a passage or a record | [`lexicons/org.bunkankun.comment.post.json`](../../lexicons/org.bunkankun.comment.post.json) |
| `org.bunkankun.translation.segment` | translation of one anchored span | [`lexicons/org.bunkankun.translation.segment.json`](../../lexicons/org.bunkankun.translation.segment.json) |

The lexicons use atproto's camelCase convention (`textId`, `markerId`,
`createdAt`); the JSONL archives use BKK's snake_case (`text_id`, `marker_id`,
`bucket_offset`).

Two-place rule per kind: the only modules that convert between the two shapes
are
[`serve/routers/annotations_write.py`](../../module/bkk/serve/routers/annotations_write.py)
on the post path (`_annotation_archive_to_wire`, `_comment_archive_to_wire`,
`_translation_archive_to_wire`) and
[`bkk/annotations/harvest.py`](../../module/bkk/annotations/harvest.py)
on the harvest path (`annotation_wire_to_archive`, `comment_wire_to_archive`,
`translation_wire_to_archive`). Add a new field in both places or in neither.

NSIDs are centralised in
[`module/bkk/serve/atproto.py`](../../module/bkk/serve/atproto.py) as
`ANNOTATION_NSID`, `COMMENT_NSID`, `TRANSLATION_NSID`, plus
`LEGACY_ANNOTATION_NSID = "org.bunkankun.annotation"` which the harvester
still reads (records posted before the `.note` rename can't be rewritten
in-place; their archive shape is identical so they fold into the same JSONL).

## Posting from the SPA

1. The user authenticates against Bluesky with handle + app password via the
   menubar dialog
   ([`BlueskyLogin.tsx`](../../module/web/src/components/Menubar/BlueskyLogin.tsx)).
   `POST /api/annotations/bluesky/session` exchanges the password for an
   atproto session via `com.atproto.server.createSession`. The JWT pair and
   the user's DID are attached to the in-memory `UserSession.bluesky` slot
   ([`serve/state.py`](../../module/bkk/serve/state.py)); nothing is written
   to disk and a server restart drops the Bluesky session (GitHub login is
   unaffected).

2. While a selection is active, the compose form
   ([`AnnotationCompose.tsx`](../../module/web/src/components/RightPanel/AnnotationCompose.tsx))
   posts `{text_id, edition, anchor:{marker_id, offset, length}, payload}` to
   `POST /api/annotations`. The handler in
   [`serve/routers/annotations_write.py`](../../module/bkk/serve/routers/annotations_write.py)
   translates archive→wire, calls `com.atproto.repo.createRecord`, returns
   the PDS-assigned `{uri, cid, did}`, and the SPA optimistically inserts the
   new card via `workspace.prependLocalAnnotation`.

   The sibling endpoints `POST /api/comments` and `POST /api/translations`
   follow the same pattern with kind-specific request models:

   - `POST /api/comments` accepts either an `anchor` (with `edition`) or a
     `parent` strong-ref (for replies) — exactly one. Body is markdown plus
     a BCP-47 `lang` tag.
   - `POST /api/translations` accepts an anchor plus inline translation
     `text`, BCP-47 `lang`, and the bundle id (`translation_id`); optional
     `title` and `note` ride along.

3. Token lifecycle is handled in
   [`atproto.create_record`](../../module/bkk/serve/atproto.py): on
   `ExpiredToken`/401 the access JWT is refreshed once and the call is
   retried; on 429 the call is retried once honouring `Retry-After`. All
   other upstream failures are surfaced as HTTP 502 to the client.

## Harvesting

The harvester is a manual CLI: **`bkk annotations harvest`**, defined at
[`module/bkk/annotations/cli.py`](../../module/bkk/annotations/cli.py).

```text
bkk annotations harvest \
  --did did:plc:xxxxxxxxxxxx        # repeatable, defaults to [annotations].dids
  --annotations-root /path/to/archive
  --corpus /path/to/corpus
  --limit 100                       # max records per DID
  --dry-run
```

Defaults fall back through `.bkkrc`: `--did` → `[annotations].dids`,
`--annotations-root` → `[annotations].annotations_root` →
`[serve].annotations_root`, `--comments-root` → `[annotations].comments_root`,
`--translations-root` → `[annotations].translations_root`, `--corpus` →
`[global].corpus`.

For each configured DID, four `listRecords` calls run (one per collection:
`annotation.note`, legacy `annotation`, `comment.post`, `translation.segment`):

1. The PDS endpoint is resolved via `https://plc.directory/<did>`, falling
   back to `https://bsky.social`
   ([`pds.py`](../../module/bkk/annotations/pds.py)).
2. `com.atproto.repo.listRecords` is paged per collection until exhausted or
   `--limit` is hit. Legacy records are tagged with the new NSID's
   `source_role` so the archive stays uniform.
3. Each wire record is translated to archive shape via the matching
   `wire_to_archive` sibling; anchored records additionally have juan seq
   parsed from the marker id (`<text>_<edition>_<NNN>-<rest>` → `NNN`) so
   the bundle's juan YAML can be read for `bucket` and `bucket_offset`.
4. Records land under the kind-specific root: annotations under
   `annotations_root/<text>/<text>_NNN.ann.jsonl`, comments under
   `comments_root/<text>/<text>_NNN.cmt.jsonl` (replies without an anchor
   land in `<text>_replies.cmt.jsonl`), translations under
   `translations_root/<translation_id>/<text>_NNN.tr.jsonl` (folding into
   the proper bkk-tr bundle juan files is future work).

### Merge semantics

For each touched `(text_id, juan_seq)` file
([`harvest.harvest`](../../module/bkk/annotations/harvest.py)):

1. Load the existing JSONL.
2. Drop existing lines whose `provenance.cid` is in the incoming batch
   (re-harvest of the same record is a no-op).
3. Drop existing lines whose `cid` is the target of any incoming
   `supersedes` field.
4. Concatenate existing + incoming, sort by `(bucket, bucket_offset, id)`
   via `bucket_sort_key`, write atomically via `write_records_jsonl`.

Seed records carry `did:plc:bkk-tls-legacy` plus a `synth-<sha256>` CID. The
harvester never produces those identifiers, so seed lines cannot be lost
incidentally.

The harvest is idempotent: a second run on the same set of PDS records
produces a byte-identical output file.

## Live contributions feed (Chat tab)

The right-hand **Chat** tab in the SPA
([`ChatTab.tsx`](../../module/web/src/components/RightPanel/ChatTab.tsx))
shows the most recent BKK records seen anywhere on the AT Protocol
network, auto-refreshing every 15s. It is *not* fed from the on-disk
archive — it calls `GET /api/contributions`, which is served from an
in-memory ring buffer populated by a Jetstream subscriber:

```text
bkk serve startup
   │
   ▼
ContributionFeed.run()
   │  1. seed: for each DID in [annotations].dids,
   │     com.atproto.repo.listRecords per NSID (limit 100 each)
   │  2. subscribe: wss://jetstream2.us-east.bsky.network/subscribe
   │     wantedCollections=org.bunkankun.{annotation.note,
   │                                      annotation,
   │                                      comment.post,
   │                                      translation.segment}
   │     [&wantedDids=… per [annotations].dids if set]
   │     cursor=<24h ago>           (gap-recovery on first connect)
   ▼
async for raw in ws:  (commit events filtered + parsed)
   │
   ▼
OrderedDict[uri → entry]  (cap=500)
   │
   ▼ snapshot(limit)
GET /api/contributions ─► ChatTab  (15s poll)
```

The seed step is what gives the chat real historical depth: Jetstream's
own backfill window is only a few hours, so without seeding the chat
would be empty after a long server downtime even if records exist in
authors' repos. Seeding from `[annotations].dids` re-hydrates everything
those authors have ever posted (up to 100 per kind).

Code: [`serve/contributions_feed.py`](../../module/bkk/serve/contributions_feed.py),
[`serve/routers/contributions.py`](../../module/bkk/serve/routers/contributions.py),
lifespan wiring in [`serve/app.py`](../../module/bkk/serve/app.py).

### Why Jetstream (and not per-DID polling)

The relay propagates our four NSIDs end-to-end now that the authority
DID (`did:plc:bqv4y6ootthsrh6pdkpqhq73`) is published and `_lexicon`
TXT records resolve per group (see
[`lexicons/README.md`](../../lexicons/README.md)). That removes the need
for the per-DID `listRecords` poll the feed used during bootstrap — we
can subscribe to the firehose directly and pick up records from any DID
in real time. If `[annotations].dids` is configured, the list is passed
as a `wantedDids` filter so deployments that want to scope the feed
still can.

A previous implementation polled `com.atproto.repo.listRecords` per DID
on a 30s cycle because the relay was dropping our NSIDs. The git commit
that switched to Jetstream documents the empirical verification.

### Buffer semantics

- Keyed by atproto URI (`at://<did>/<collection>/<rkey>`) for O(1) dedupe.
- `time_us` comes from the Jetstream commit envelope (relay-assigned
  microseconds since epoch) rather than the record's `createdAt`, so
  ordering is monotonic even when authors lie about timestamps.
- Ordering on read: sorted by `time_us` descending.
- Eviction: oldest by insertion order once the buffer reaches `BUFFER_MAX`
  (500). `truncated: true` is returned alongside the items so the UI can
  surface the fact that older records have been dropped.
- `update` events refresh the buffer entry in place; `delete` events
  drop the URI from the buffer.
- Reconnect: on `ConnectionClosed` or any subscribe exception, the
  subscriber sleeps with exponential backoff (1s → 60s) and reconnects.
  The cursor advances with every consumed event and is reused on
  reconnect so no commits are lost during a transient disconnect.
- Initial backfill: on first connect the cursor is set 24h in the past
  so the chat is populated immediately rather than empty until the next
  post.

### Wire shape

`/api/contributions` returns a flattened snake_case projection of the
record (see [`ContributionOut`](../../module/bkk/serve/routers/contributions.py)),
not the on-disk archive shape produced by `wire_to_archive`. The Chat
tab consumes that directly without bucket/`bucket_offset` resolution —
chat is a stream view, not an anchor-resolved view, so we skip the
corpus lookup that `harvest` needs.

### Configuration

```yaml
# .bkkrc
annotations:
  dids:
    - did:plc:xxxxxxxxxxxx
    - did:plc:yyyyyyyyyyyy
```

The same list drives both `bkk annotations harvest` and the live feed —
adding a DID in one place gets it polled and harvested. The serve config
hook is [`ServeConfig.annotation_dids`](../../module/bkk/serve/config.py),
loaded via the merged `rc.get("dids")` in
[`serve/cli.py`](../../module/bkk/serve/cli.py).

Env vars:

- `BKK_DISABLE_CONTRIBUTIONS_POLL=1` — skip the poll task on startup. The
  feed object is still attached so `/contributions` returns
  `{items:[], truncated:false}` uniformly. Use this in tests, offline dev,
  and any context where the server should not touch the network.

## Sense UUID compatibility

TLS-seed annotation records may carry `payload.sense.id` as `uuid-<uuid>`,
while the bkk-core index and frontend picker use the bare UUID form. Any
read path that joins annotations to bkk-core senses must normalize or query
both forms; otherwise existing annotation locations show up in the juan view
but the sense-level "where used" lookup reports zero uses.

## Configuring DIDs in `.bkkrc`

```yaml
annotations:
  dids:                       # YAML list — one DID per line
    - did:plc:xxxxxxxxxxxx
    - did:plc:yyyyyyyyyyyy
  annotations_root: /data/bkk-annotations   # optional override of [serve]
```

A scalar `dids:` value is rejected with a clear error; without the list
markers, YAML would parse the whole thing as a string and the harvester would
iterate it character by character.

## Bluesky session is scoped to the GitHub session

The Bluesky login endpoint calls `_require_user` before accepting an app
password, and the resulting `BlueskySession` is attached to the GitHub-issued
`UserSession` keyed by the `SESSION_COOKIE`. The annotation post handler then
calls both `_require_user` and `_require_bluesky`.

This coupling is intentional under the current model:

- Annotations are mirrored into the user's GitHub-hosted `bkk-annotations`
  archive, so a post that bypasses GitHub auth would have nowhere to land.
- Scoping the in-memory atproto tokens to the GitHub session avoids inventing
  a second cookie / session store just for Bluesky.

**Worth reconsidering** if a standalone Bluesky use case appears — e.g.
browsing other users' Bluesky-hosted annotations without a personal archive,
or letting a reader connect their Bluesky identity for read-only social
features. At that point the Bluesky session would need its own cookie and
the post handler's `_require_user` dependency would need to move to a
GitHub-only path.

## Out of scope (deferred)

- DB working store (SQLite/Postgres) between PDS and archive.
- Curation-state transitions beyond the default `proposed` set by the
  harvester.
- Supersedes-chain UI in the SPA (the write endpoint accepts the field;
  there is no UI to drive it yet).
- Multi-PDS resolution beyond a single 429 retry and a fallback to
  `bsky.social`.

## Future requirements

Sketches for follow-up work — own planning docs when scoped.

### 1. Sub-tabs by annotation kind

**Goal:** filter the Chat stream by payload kind (`form`, `sense`,
`translation`, `concept`, `metadata`) without losing the "all" view.

**What a plan needs to nail down:**

- Whether the filter is client-side (current 200-item snapshot is small
  enough; filter in React, no API change) or server-side (`?kind=form`
  query param, separate buffers per kind).
- Definition of "kind" for records with multiple populated fields. The
  current first-non-empty rule in `ContribCard` is an implicit ranking;
  a sub-tab UI needs the same ranking made explicit.
- Tab UX: pill-style segmented control above the list, or a top-row
  filter chip strip. Lean on existing styles in `AnnotationsTab.tsx`
  / `SearchTab.tsx` for consistency.

Recommended split: client-side filter first (cheap, no API churn), revisit
if buffer size grows or per-kind backfill becomes valuable.

### 2. Click-to-navigate from a contribution card

**Goal:** clicking a card in the Chat tab opens the referenced
`text_id`/`edition`/`marker_id` in the text viewer with the anchor
selected.

**What a plan needs to nail down:**

- Reuse of the existing pane-open flow: which `workspace.*` method opens
  a specific juan + scrolls to a marker?
- Whether to highlight the anchor span the way the
  `AnnotationsTab → AnnCard` jump path does — that path uses resolved
  `(bucket, bucket_offset)`, which the live feed does *not* compute.
  Either (a) compute it on click (lazy juan fetch + marker resolution),
  or (b) extend the feed entry to include bucket/`bucket_offset` (mirror
  `harvest.compute_bucket_position`).
- Behavior when the referenced juan / marker is not in the loaded
  corpus: show a clear "not in this corpus" message rather than a 404.

### 3. DID → handle resolution

**Goal:** show `@handle.bsky.social` instead of `did:plc:abc…` on each
card.

**What a plan needs to nail down:**

- Caching: handle resolution is per-DID, change-rate is low; an LRU in
  the feed object is plenty.
- Source: `com.atproto.identity.resolveHandle` (forward) is not what we
  want — we have the DID and need its handle. Pull from the DID document
  (`https://plc.directory/<did>`) which already includes
  `alsoKnownAs: ["at://handle.example.com"]`. We already hit
  `plc.directory` from `pds.py:resolve_pds`; piggyback there.
- Backfill: a DID whose handle was unknown when it was buffered should
  update in place on next resolution success.
