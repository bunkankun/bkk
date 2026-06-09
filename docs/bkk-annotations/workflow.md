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

## Lexicon

Records live under NSID **`org.bunkankun.annotation`**, defined in
[`lexicons/org.bunkankun.annotation.json`](../../lexicons/org.bunkankun.annotation.json).
The lexicon uses atproto's camelCase convention (`textId`, `markerId`,
`createdAt`); the JSONL archive uses BKK's snake_case (`text_id`, `marker_id`,
`bucket_offset`).

Two-place rule: the only modules that convert between the two shapes are
[`serve/routers/annotations_write.py:_archive_to_wire`](../../module/bkk/serve/routers/annotations_write.py)
on the post path and
[`bkk/annotations/harvest.py:wire_to_archive`](../../module/bkk/annotations/harvest.py)
on the harvest path. Add a new field in both places or in neither.

The NSID is centralised as `ANNOTATION_NSID` in
[`module/bkk/serve/atproto.py`](../../module/bkk/serve/atproto.py); a rename to
a DNS-verifiable namespace later is mechanical.

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
`[serve].annotations_root`, `--corpus` → `[global].corpus`.

For each configured DID:

1. The PDS endpoint is resolved via `https://plc.directory/<did>`, falling
   back to `https://bsky.social`
   ([`pds.py`](../../module/bkk/annotations/pds.py)).
2. `com.atproto.repo.listRecords` is paged for `collection=
   org.bunkankun.annotation` until exhausted or `--limit` is hit.
3. Each wire record is translated to archive shape; juan seq is parsed from
   the marker id (`<text>_<edition>_<NNN>-<rest>` → `NNN`); the bundle's
   juan YAML is read to compute `bucket` and `bucket_offset`.

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
shows the most recent annotation records from the configured DID roster,
auto-refreshing every 15s. It is *not* fed from the on-disk archive — it
calls `GET /api/contributions`, which is served from an in-memory ring
buffer populated by a background poller:

```text
bkk serve startup
   │
   ▼
ContributionFeed.run()  ─────────────┐
   │   every 30s, for each DID in    │
   │   [annotations].dids:           │
   ▼                                 │
asyncio.to_thread(_poll_did_sync)    │
   │                                 │
   ▼                                 │
resolve_pds(did) ──► listRecords ────┘
   │                                 ▲
   ▼                                 │
OrderedDict[uri → entry]  (cap=500)  │
   │                                 │
   ▼ snapshot(limit)                 │
GET /api/contributions ─► ChatTab ───┘  (15s poll)
```

Code: [`serve/contributions_feed.py`](../../module/bkk/serve/contributions_feed.py),
[`serve/routers/contributions.py`](../../module/bkk/serve/routers/contributions.py),
lifespan wiring in [`serve/app.py`](../../module/bkk/serve/app.py).

### Why polling and not the firehose

Bluesky's relay does not currently propagate our custom NSID
`org.bunkankun.annotation`. We confirmed empirically by subscribing to
`wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=org.bunkankun.annotation`
with a 24h backfill cursor and seeing zero `commit` events while a control
filter on `app.bsky.feed.post` produced hundreds per second. The relay's
collection filter is enforced server-side; nothing the client can do works
around it. See the **future requirements** below for the lexicon-publishing
path that unblocks this.

### Buffer semantics

- Keyed by atproto URI (`at://<did>/<collection>/<rkey>`) for O(1) dedupe.
- Ordering on read: sorted by `time_us` (parsed from the record's
  `createdAt`) descending. Zero is used as a sentinel for unparseable
  timestamps so they sort last instead of crashing.
- Eviction: oldest by insertion order once the buffer reaches `BUFFER_MAX`
  (500). `truncated: true` is returned alongside the items so the UI can
  surface the fact that older records have been dropped.
- Per-DID errors (PDS unreachable, 4xx, malformed payload) are logged and
  skipped — they never poison the cycle for other DIDs.
- The poller pulls a single page of `PAGE_LIMIT=100` per DID per cycle.
  When a DID has more than 100 fresh records (post-backlog flood, a single
  burst), only the newest 100 land in any given cycle; the rest will catch
  up on subsequent cycles because the buffer dedupes on URI.

### Wire shape

`/api/contributions` returns the camelCase atproto wire shape flattened
(see [`ContributionOut`](../../module/bkk/serve/routers/contributions.py)),
not the archive (`wire_to_archive`) shape. The Chat tab consumes that
directly without bucket/`bucket_offset` resolution — chat is a stream view,
not an anchor-resolved view, so we skip the corpus lookup that `harvest`
needs.

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

The live contributions feed has two structural limitations that future
work needs to unblock. Both belong in their own planning docs (sketch
here, not full design).

### 1. Lexicon publishing for `org.bunkankun.annotation`

**Goal:** records authored under this NSID propagate through the public
relay/Jetstream so the Chat tab can show contributions from *any*
atproto user — not just DIDs in our roster.

**What we know:**

- The owner of the NSID's authority domain (`bunkankun.org` for
  `org.bunkankun.annotation`) does not need Bluesky's permission to
  publish a custom NSID; lexicons are discovered via DNS + an authority
  DID, not a central registry.
- The relay carries collections whose lexicons are resolvable through
  that chain. Until ours is, the records stay invisible past the PDS.

**What a plan needs to nail down:**

- Exact lexicon-discovery mechanism in the current AT Protocol spec
  (DNS label name, TXT record format, lexicon-record collection NSID and
  shape). The spec has been moving; resolve against current
  implementations, not stale docs.
- Authority DID: create one for `bunkankun.org`, host its DID document,
  bind it to the domain.
- Lexicon JSON for `org.bunkankun.annotation` — already drafted at
  [`lexicons/org.bunkankun.annotation.json`](../../lexicons/org.bunkankun.annotation.json);
  verify it still matches the record shape we post.
- Publish path: post the lexicon as records under the authority DID via
  the standard lexicon collection.
- Verification: a third-party tool / Jetstream subscriber sees our
  records appear on the firehose end-to-end.

**Out of scope for the lexicon work itself:** changing the record shape
on the wire, renaming the NSID, or migrating existing records.
`ANNOTATION_NSID` lives in
[`module/bkk/serve/atproto.py`](../../module/bkk/serve/atproto.py) so a
rename is mechanical, but it has compounding effects (archived URIs,
in-flight posts, harvest provenance) and should be a separate decision.

**Once lexicon publishing lands**, swap
[`serve/contributions_feed.py`](../../module/bkk/serve/contributions_feed.py)
back to a Jetstream subscriber. The previous implementation lives in
git history (one commit before the polling rewrite) — same
`ContributionFeed` class shape, same buffer semantics, just a different
ingestion side. Both can coexist behind an env-var toggle if a transition
window helps.

### 2. Sub-tabs by annotation kind

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

### 3. Click-to-navigate from a contribution card

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

### 4. DID → handle resolution

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
