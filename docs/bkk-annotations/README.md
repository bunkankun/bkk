# BKK Annotations Archive

This document describes the on-disk format of the `bkk-annotations` repo —
the canonical archive of annotations against BKK texts. Annotations leave
the per-text bundles (where the older `tls:ann` markers + `.ann.yaml`
sidecars used to live) and instead accumulate here.

This layer is the **archive**. In the eventual full architecture
(`docs/bkk-annotations-four-layers-plan.md`):

- **Source of truth:** Bluesky custom records (DID-signed, append-mostly).
- **Working store:** a backend DB harvested from Bluesky.
- **Archive:** this repo — periodic snapshot of the working store.

The Bluesky transport (post + manual harvest) is now wired in — see
[`workflow.md`](workflow.md). The DB working store is still deferred; harvested
records land in this archive directly alongside the TLS-seed lines.

## Layout

```text
bkk-annotations/
  <text-id>/
    <text-id>_001.ann.jsonl
    <text-id>_002.ann.jsonl
    ...
```

One file per `(text_id, juan_seq)`. The filename is `<text-id>_<NNN>.ann.jsonl`
with `NNN` zero-padded to three digits, matching the juan-file naming in the
text bundles.

Each line in a `.ann.jsonl` file is one annotation record encoded as a single
JSON object. Records are sorted by `(bucket, bucket_offset, id)` for
diff-friendly commits.

## Record shape

```json
{
  "id": "<uuid or uuid-<…>>",
  "text_id": "KR1h0004",
  "edition": "tls",
  "anchor": {
    "marker_id": "KR1h0004_tls_003-1a.5",
    "offset": 0,
    "length": 1,
    "end_marker_id": null,
    "end_length": null
  },
  "payload": { "concept": "…", "form": {…}, "sense": {…}, … },
  "provenance": {
    "did": "did:plc:bkk-tls-legacy",
    "cid": "synth-<sha256 of record minus cid>",
    "created_at": null,
    "source_role": "tls:ann",
    "supersedes": null,
    "tls": { "seg_id": "…", "pos": 1 }
  },
  "curation_state": "accepted",
  "bucket": "body",
  "bucket_offset": 1234
}
```

### Fields

- **`id`** — record identifier. For TLS-seed records this is the original
  payload `id` (a `uuid-<…>` string from the TLS source). For records lacking
  a payload id, a deterministic UUID5 is synthesised from the anchor.
- **`text_id`** / **`edition`** — the source-text identifier and edition short
  the annotation is anchored to.
- **`anchor.marker_id`** — id of a marker in the source bundle that serves as
  the anchor point. Must exist in the corresponding juan file. Marker IDs are
  stable across re-imports once a baseline is frozen (see
  `docs/bkk-marker-ids.md`).
- **`anchor.offset`** — 0-indexed distance (in canonical PUA characters) from
  the marker to the start of the annotated span.
- **`anchor.length`** — span length in canonical PUA characters. `0` means a
  point annotation.
- **`anchor.end_marker_id`** / **`end_length`** — optional, for spans that
  cross another marker. Both null for spans inside a single anchor segment.
- **`payload`** — the annotation's content. Shape is source-dependent. For
  TLS-seed records: `concept`, `concept_id`, `form`, `sense`, `translation`,
  `metadata` (same fields the old `.ann.yaml` sidecar carried, minus
  duplicated id/anchor info).
- **`provenance.did`** — author DID. For TLS-seed records, the constant
  `did:plc:bkk-tls-legacy` (a placeholder; no real atproto registration).
- **`provenance.cid`** — record content identifier. For TLS-seed records, a
  deterministic synthetic CID of shape `synth-<sha256-hex>`, computed by
  hashing the record with `cid` cleared. Re-running the seed migration
  produces identical CIDs on identical input.
- **`provenance.created_at`** — original record creation timestamp where
  known (TLS `metadata.created`); null otherwise.
- **`provenance.source_role`** — what kind of annotation this was in the
  source pipeline. `tls:ann` for the seed corpus.
- **`provenance.supersedes`** — prior record CID this record replaces; null
  for first-version records.
- **`provenance.tls`** — round-trip carry-over for TLS-seed records: the
  original `<seg xml:id>` and 1-indexed `pos` from the TLS source, so the
  TLS exporter can reconstruct source positions. Absent on non-TLS records.
- **`curation_state`** — one of `proposed`, `accepted`, `rejected`,
  `superseded`. TLS-seed records start as `accepted`.
- **`bucket`** / **`bucket_offset`** — derived fields: which juan bucket
  (`front` / `body` / `back`) the resolved anchor falls in, and the
  bucket-relative codepoint offset. Regenerable from the anchor and the
  source bundle; carried inline for fast frontend rendering.

## Provenance and migrations

The TLS-seed corpus is a one-time migration: re-running it overwrites the
archive files in place. Once Bluesky-sourced records start arriving, the
import path stops being the sole writer — seed records will live alongside
harvested records in the same JSONL files, distinguishable by their `did`
and synthetic-CID prefix.

The curation state machine, supersedes-chain semantics, and snapshot cadence
from the DB working store are deliberately not enforced here yet; the format
is forward-compatible with them.
