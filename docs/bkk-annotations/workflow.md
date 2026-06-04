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
- Firehose / Jetstream listener — the CLI is the only ingestion path.
- Curation-state transitions beyond the default `proposed` set by the
  harvester.
- Supersedes-chain UI in the SPA (the write endpoint accepts the field;
  there is no UI to drive it yet).
- Multi-PDS resolution beyond a single 429 retry and a fallback to
  `bsky.social`.
