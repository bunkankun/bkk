# Cross-source merge: TLS-first, KRP-on-top

## Context

A growing slice of the corpus has both a TLS source (CBETA-flavor TEI,
with apparatus and `<note>` annotations) and a KRP source (a Kanripo
git repo with a master branch + witness branches). The importer used
to be single-source and write-once: each invocation rebuilt
`<out-root>/<text-id>/` from scratch, silently overwriting whatever
was there. Re-importing the second source therefore destroyed the
first.

The desired rule is asymmetric on purpose:

- **TLS owns the surface (root) edition** when both sources are present.
  The TLS master at `<text-id>.manifest.yaml` plus its sidecar
  (`<text-id>.source.yaml`) and `<text-id>_NNN.ann.yaml` files are the
  canonical reading text.
- **KRP** can be added on top of an existing TLS bundle. Its
  documentary editions slot into `editions/<short>/`. Its synthesized
  master (which carries variant + witness page-break apparatus) is
  demoted to one edition among others under `editions/krp/` — it
  never replaces the TLS master at the bundle root.
- **Operational constraint**: import TLS first, KRP later. The reverse
  direction is rejected with a hard error so the user removes the
  bundle and re-imports in the correct order.

## What changed

### Decision matrix

| State of `<out-root>/<text-id>/`     | Incoming format | Action                                                                                                                                                                |
| ------------------------------------ | --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| empty / does not exist               | TLS             | Normal write (unchanged).                                                                                                                                             |
| empty / does not exist               | KRP             | Normal write (unchanged).                                                                                                                                             |
| TLS-sourced bundle exists            | TLS             | Re-import — TLS-owned files overwritten as before.                                                                                                                    |
| **TLS-sourced bundle exists**        | **KRP**         | **Merge.** KRP editions written under `editions/`; the synthesized KRP master is demoted to `editions/krp/`; the TLS root manifest's `editions:` list is extended. |
| KRP-sourced bundle exists            | KRP             | Re-import — rebuild as before.                                                                                                                                        |
| **KRP-sourced bundle exists**        | **TLS**         | **Hard error.** Refuse to write. Remedy: remove the bundle and re-import in TLS-then-KRP order.                                                                       |
| Bundle exists but can't be classified | either         | **Hard error.** Inspect or `bkk repair manifest <bundle>` and retry.                                                                                                  |

### Source detection

The new
[`inspect_existing_bundle`](../module/bkk/importer/write/merge.py)
helper classifies an on-disk bundle by reading the master manifest at
the bundle root:

- `tls`: `<text-id>.source.yaml` sidecar present (TLS writes it; KRP
  does not). A merged TLS+KRP bundle stays in this state because the
  surface remains TLS-owned.
- `krp`: no sidecar; master manifest carries `entity_encoding`.
- `empty`: no master manifest.
- `unknown`: anything else (corrupt or partial).

The same helper enumerates `tls_owned_editions` — the shorts under
`editions/` whose edition manifest has no `entity_encoding`. The merge
path uses this set to refuse to overwrite TLS-owned editions when a
KRP source happens to ship the same short (e.g., both have a `T`
edition for Taishō-derived texts).

### KRP master demotion

When merge mode is active in
[`bkk.importer.cli._import_one`](../module/bkk/importer/cli.py):

- Each KRP documentary edition is written via the existing
  `write_krp_edition`. If its `short` is in `tls_owned_editions`, the
  write is **skipped with a stderr notice** rather than overwriting
  the TLS-owned manifest.
- The synthesized KRP master is written via `write_krp_edition` (not
  `write_krp_master`) so it lands under `editions/krp/`. Variant +
  witness page-break markers travel with the juan dicts and survive
  unchanged. PUA-map.yaml is written separately at the bundle root via
  the new [`write_pua_map`](../module/bkk/importer/write/bundle.py)
  helper — the validator requires it at the bundle root, not under
  `editions/`.
- `extend_master_editions` appends each newly-written edition's
  `{short, label?}` to the TLS master manifest's `editions:` list and
  re-hashes. Existing entries are re-wrapped as flow dicts so a
  re-import does not flip the file between flow and block style.
  Duplicate shorts on re-import are skipped, so the operation is
  idempotent.

### Apparatus projection onto the TLS surface

The TLS reading text by itself carries no variants and no witness
page-breaks. Once KRP editions are merged in, leaving the TLS surface
unchanged would mean readers of the bundle root see strictly less than
readers of `editions/krp/`. To make the surface a superset rather than a
projection-of-one-edition,
[`project_krp_apparatus_onto_tls`](../module/bkk/importer/write/merge.py)
runs after `extend_master_editions`:

- For every KRP documentary edition plus the demoted master, the helper
  pairs juans by `seq` and invokes
  [`_attach_variants`](../module/bkk/importer/read/krp.py) and
  [`_attach_witness_page_breaks`](../module/bkk/importer/read/krp.py) —
  the same routines the KRP reader uses internally to populate the KRP
  master. Each variant marker carries the witness short it was detected
  against; page-break markers carry the witness id (e.g.
  `KR2d0002_WYG_001-1a`) and the per-edition image binding. Master-vs.
  -witness alignment is done by `difflib.SequenceMatcher` on the
  concatenated bucket text, so page-breaks land at the right master
  offset even when the texts diverge.
- The TLS surface juans at the bundle root are then rewritten with the
  union of their original markers and the projected ones; their
  `assets.parts` hashes and the master manifest's self-hash are
  recomputed in place. The TLS-owned documentary edition under
  `editions/<short>/`, the sidecar, and the ann files are untouched.
- The validator's master-juan rule already accepts marker ids from any
  declared witness short plus `krp`, so the projected markers pass
  validation without further configuration. The same rule is reused for
  `editions/krp/` since the demoted master legitimately carries
  witness-flavored ids.

### TLS-into-existing-KRP refusal

[`bkk.importer.cli._import_one_tls`](../module/bkk/importer/cli.py)
calls `inspect_existing_bundle` before reading the TLS XML. If the
state is `krp` or `unknown` it raises `BundleConflictError` with a
remedy line; the bulk loop's existing per-text exception handler
prints it to stderr and continues with the next text.

## End-to-end test

[`module/tests/test_importer_cross_source_merge.py`](../module/tests/test_importer_cross_source_merge.py)
covers:

- `inspect_existing_bundle` for all four states.
- `extend_master_editions` appends new entries, dedupes existing
  shorts, and recomputes the manifest hash.
- TLS → KRP via the CLI: the sidecar and the TLS-owned documentary
  edition are byte-identical after the merge; the master manifest's
  `editions:` list grows; KRP editions appear under `editions/`; the
  demoted master lands at `editions/krp/`; and the TLS surface juans
  gain variant + witness-page-break markers projected from the KRP
  apparatus.
- KRP → TLS: refuses with a clear remedy message; the KRP master is
  byte-identical after the failed attempt.
- Manifest in unclassifiable state: `_import_one_tls` raises
  `BundleConflictError`.

## Critical files

- [module/bkk/importer/write/merge.py](../module/bkk/importer/write/merge.py)
  — `inspect_existing_bundle`, `ExistingBundle`,
  `extend_master_editions`, `project_krp_apparatus_onto_tls`.
- [module/bkk/importer/write/bundle.py](../module/bkk/importer/write/bundle.py)
  — new `write_pua_map` helper for the merge path.
- [module/bkk/importer/cli.py](../module/bkk/importer/cli.py) —
  `BundleConflictError`; pre-write existence checks in `_import_one`
  and `_import_one_tls`; the demote-master path.
- [module/tests/test_importer_cross_source_merge.py](../module/tests/test_importer_cross_source_merge.py)
  — coverage above.

## Risks / follow-ups

- **Edition-short collisions are silent skips.** When KRP carries a
  short already owned by TLS (e.g., both ship `T`), the KRP edition is
  dropped with only a stderr notice. That's intentional — TLS wins on
  collisions, matching the spirit of "TLS owns the surface" — but a
  user expecting both to land has to read stderr to notice. Consider
  promoting the notice to a warning collected in the summary if this
  bites in practice.
- **Recipe-pinned `master.branch` other than `"master"` is untested.**
  The KRP synthesis pipeline normally produces `edition_short="krp"`
  for the master bundle regardless of the recipe's `master.branch`
  field, so the demoted edition always lands at `editions/krp/`.
  Verified for the convention-based and recipe-driven paths used in
  fixtures. If a future recipe sets a different short on the master
  bundle, the destination directory will track that short rather than
  literally `"master"`.
- **No automated migration from KRP-only to TLS-merged.** Per the
  approved plan we explicitly chose the error-only remedy; the user
  removes the bundle and re-imports. If this proves friction-heavy,
  add `bkk repair migrate-to-tls <text-id>` later — `inspect_existing_bundle`
  + `extend_master_editions` already provide the primitives.
