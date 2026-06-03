# BKK marker IDs

Every marker in a BKK text bundle carries a stable, deterministic ID. The IDs
exist to be **anchor points** for things that live outside the text layer —
annotations, translations, cross-edition references — so a re-import of the
source must not silently move them.

This document defines the ID format, the stability contract, and the
`bkk.validator --marker-ids` subcommand that enforces it.

## ID shape

All marker IDs share the shape

    <text-id>_<edition>_<juan-label>-<slug>

- `text-id` — the Kanripo identifier (e.g. `KR6q0053`).
- `edition` — short edition code (e.g. `T`, `tls`, `swl`).
- `juan-label` — zero-padded juan number (e.g. `001`).
- `slug` — per-marker locator.

There are two slug families:

### Source-derived slugs (authoritative)

- **TLS**: the original `xml:id` of the source element.
- **KRP**: the `ed_n` page/line/voice code.
- **CBETA**: synthesised from CBETA structural attributes.

If the source carries one of these IDs, the importer reuses it verbatim. These
slugs are the ground truth and never change as long as the source doesn't.

### Importer-inserted slugs (`bkk*` prefix)

For markers that the source leaves anonymous (no `xml:id`, no `ed_n`), the
importer assigns a slug of the form

    bkk<type-short><n>

- `type-short` — short code for the marker type (see
  [idassigner.py](../module/bkk/importer/idassigner.py)): `pb`, `lb`, `p`,
  `h`, `th`, …
- `n` — 1-based counter, per `(text, edition, juan, marker type)`.

Order is determined by the marker's offset within the juan's merged bucket
text, so the assignment is reproducible across re-imports as long as source
ordering is stable.

The `bkk` prefix distinguishes importer-inserted slugs from source-derived
slugs like `1a.5`, so a reader can tell which IDs were ever externally
referenceable in the source.

### Collision suffixes (`_dup{N}`)

When two source elements collide on the same `xml:id`, the importer keeps the
first ID intact and appends `_dup1`, `_dup2`, … to the duplicates. This is a
defect signal — the source should not have duplicate `xml:id`s — and the
suffix must remain stable as long as the source does.

## Stability contract

Once a baseline snapshot is frozen for a text (piece 7 of the annotation-layer
plan), every subsequent import of the same source must produce a **superset**
of that snapshot:

1. Every baseline ID must still be present.
2. Every baseline ID must still point at a marker of the same type.
3. Position drift inside a single ID is allowed (text edits move things), but
   the ID cannot be **repurposed** — i.e. attached to a different conceptual
   marker.
4. New markers may appear and receive new IDs. Additions are fine; removals
   and renames are not.

This is the contract that keeps the four-layer architecture (text bundles +
core + translations + annotations) stable: annotation anchors of shape
`(marker_id, offset, length)` survive re-imports because the marker IDs
themselves don't move.

## Snapshot file

A frozen baseline lives next to the master manifest as

    <bundle-dir>/<text-id>.marker-ids.yaml

Shape:

```yaml
text_id: KR6q0053
juans:
  - seq: 1
    edition: T
    ids:
      - { id: "KR6q0053_T_001-0495a", type: "page-break" }
      - { id: "KR6q0053_T_001-0495a.4-h", type: "tls:head" }
      - { id: "KR6q0053_T_001-bkkpb1", type: "page-break" }
```

Order matches the marker order in the bundle (offset-sorted, structural
markers stable per `effective_markers_for_bucket`). All editions present
in the bundle contribute their juan snapshots.

The snapshot itself is a small per-text commitment: hand-editing it is
how a maintainer formally acknowledges a renamed marker.

## `bkk.validator --marker-ids`

The validator gains two flags:

- `--freeze-marker-ids` — walk the bundle, gather every marker ID, and write
  the snapshot file. Refuses to overwrite an existing snapshot unless
  `--force` is also passed.
- `--marker-ids` — run only the marker-ID drift check against the snapshot.
  Reports
  - `missing`: ID present in the baseline, absent from the current import.
  - `repurposed`: ID present in both but with a different marker type.
  - `extra`: ID present in the current import, absent from the baseline
    (informational only — additions are allowed).

`--marker-ids` is also invoked as part of the default `bkk.validator` run
when a snapshot file is present; missing snapshot is silently ignored so
that texts can be onboarded gradually.
