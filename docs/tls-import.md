# TLS importer: nested divs + juan-label normalization

## Context

Two TLS sources surfaced bugs that the [`KR6q0053` round-trip
fixture](../module/tests/test_tls_roundtrip.py) didn't exercise:

1. **Nested `<div>` chapter blocks.** Sources like `KR1a0171` wrap the
   bulk of each juan's content in nested `<div>` elements (one per
   chapter). The old reader only walked direct `<head>` / `<p>` / `<pb>`
   children of the juan div, so every paragraph below a nested chapter
   was silently dropped. Round-trip looked clean because the exporter
   wasn't being asked to emit anything beyond what the (broken) reader
   produced.
2. **Short juan-label encodings.** A handful of sources use 1- or
   2-digit juan numbers in marker xml:ids (`..._tls_01-...`). The BKK
   spec uses 3 digits everywhere, so downstream tools (catalog, search,
   validator) saw inconsistent identifiers depending on the source.

Both fixes live in [`bkk.importer.read.tls`](../module/bkk/importer/read/tls.py)
and [`bkk.exporter.tls`](../module/bkk/exporter/tls.py); the validator
rules in [`bkk.validator.rules`](../module/bkk/validator/rules/) were
relaxed to accept the new shapes.

## What changed

### Nested `<div>` round-trip

- **Reader.** `_section_from_div` now walks nested `<div>` elements
  recursively via a shared `_walk_div_children` helper. Each nested div
  brackets its content with paired `tls:div-start` / `tls:div-end`
  markers (id = the nested div's head xml:id), and contributes its own
  attrs/head/p_attrs entry to a new `nested_divs_info` dict. The
  outermost div still owns `section.head_text` / `section.head_marker_id`.
- **Exporter.** `_build_div` now keeps a stack of `_DivCtx` contexts —
  one per open `<div>`. `tls:div-start` pushes a fresh context (creating
  a child `<div>` element under the active one); `tls:div-end` pops.
  Each context has its own paragraph cursor and `<p>`-attrs replay, so
  nested divs reconstruct their own paragraph structure rather than
  inheriting the outer juan's.
- **Markers info.** The juan-level `divs_info` dict picks up entries
  for every nested div, keyed by that div's head xml:id (the same id
  carried by the surrounding `tls:div-start` / `tls:div-end`).

A flat juan with no nested divs (the `KR6q0053` shape) emits zero
`tls:div-*` markers — the bundle stays byte-identical to before.

### Juan-label width normalization

- New `_normalize_marker_id` pads the digits between the second
  underscore and the first hyphen of every marker id to
  `JUAN_LABEL_WIDTH = 3`. No-op on canonical input, on labels already
  ≥3 digits, and on non-numeric labels.
- `_normalize_juan_label_width` runs once at the top of `read_tls`,
  rewriting `Section.head_marker_id`, every `Marker.id`, every key in
  `divs_info` / `markers_info`, every `Annotation.seg_id`, and the
  `seg_id` field inside each `annotations_info` entry. This must run
  *before* edition derivation and juan splitting so they see canonical
  ids.
- The exporter therefore re-emits the canonical (padded) form. This is a
  deliberate, documented round-trip divergence for sources that used
  short labels — the bundle is the source of truth, not the original
  XML.

### Validator updates

- `JUAN_KNOWN_MARKER_TYPES` now includes `tls:div-start` and
  `tls:div-end`. Both share the bracketed head's xml:id by design (the
  pair plus the head marker all carry the same id), so they're exempted
  from the per-marker uniqueness check; the format check still runs.
- The marker-edition allowlist is now optional. TLS-only bundles
  (master == sole witness, no `editions/` subdir, no editions declared)
  carry an implicit edition segment (`tls`) with no on-disk witness to
  match against. Passing `allowed_marker_editions=None` disables the
  check rather than rejecting every marker.
- `PUA_COUNT_MATCHES_TEXT` previously counted PUA codepoints only in
  master juan texts, but the importer aggregates the PUA-map across
  master + every edition. The cross-check now scans the same set, so
  declared and observed totals match.

## End-to-end test

[`module/tests/test_tls_nested_div.py`](../module/tests/test_tls_nested_div.py)
covers:

- nested-div recursion (`tls:div-start` / `tls:div-end` pairing,
  deeply nested cases, head-text isolation),
- the flat-div regression guard (no `tls:div-*` markers when input is
  flat),
- the label normalizer (every carrier mutates consistently, no-ops on
  canonical/non-numeric input),
- a synthetic XML → bundle → re-export round-trip that asserts the
  nested hierarchy survives and every emitted seg id uses the 3-digit
  label form.

## Critical files

- [module/bkk/importer/read/tls.py](../module/bkk/importer/read/tls.py)
  — `_walk_div_children`, `_normalize_marker_id`,
  `_normalize_juan_label_width`, `JUAN_LABEL_WIDTH`.
- [module/bkk/exporter/tls.py](../module/bkk/exporter/tls.py) —
  `_DivCtx` context stack; `tls:div-start` / `tls:div-end` handling.
- [module/bkk/validator/rules/juan.py](../module/bkk/validator/rules/juan.py)
  — `tls:div-*` accepted; `allowed_marker_editions` optional.
- [module/bkk/validator/rules/pua.py](../module/bkk/validator/rules/pua.py)
  — counts across master + every edition.
- [module/tests/test_tls_nested_div.py](../module/tests/test_tls_nested_div.py)
  — new coverage.

## Audit tools

Two scripts under [tools/](../tools/) drove the bug discovery and stayed
useful enough to keep:

- [tools/tls_import_missing.py](../tools/tls_import_missing.py) — walks
  a TLS source tree, lists every `<text-id>.xml` whose corresponding
  bundle didn't get written. Useful for finding silent reader bailouts
  and partial corpus runs. Pipe through `cut -f1 | xargs -I{} python -m
  bkk.importer ... --text-id {}` to rerun just the missing set.
- [tools/validate_corpus.py](../tools/validate_corpus.py) — runs
  `validate_bundle` over every bundle dir under `--input`, in parallel
  via `ProcessPoolExecutor`. Writes per-bundle JSON for any bundle with
  findings, plus `summary.json` and `summary.txt` with rule_id histogram
  and the top-error bundles. The exporter CLI's corpus walker uses the
  same manifest-existence predicate
  ([docs/export-cli.md](export-cli.md)).
- [tools/char_survey.py](../tools/char_survey.py) — rewritten to
  consume bundle YAMLs instead of the old `*.txt` Kanripo source. Counts
  raw codepoints in `front.text` + `body.text` per bundle, writes one
  CSV per bundle plus a corpus-wide `summary.csv`. No NFC, no header
  stripping — what's in the bundle is what gets counted, so the survey
  stays in sync with whatever the importer emits today.

## Risks / follow-ups

- **Round-trip divergence for short-label sources.** Sources with
  `_01-` / `_5-` ids re-export with `_001-` / `_005-`. This is
  intentional, but it means a strict byte-for-byte round-trip test
  can't be written for those texts; existing tests assert structural
  equivalence instead.
- **Nested div attrs only round-trip via the head id.** A nested `<div>`
  with no `<head>` (or no `xml:id` on its head's seg) won't survive
  round-trip cleanly — the exporter has nowhere to look up its attrs.
  None of the corpus sources surveyed so far hit this; flag if any
  do.
- **Reader still ignores text/tail nodes between top-level children.**
  TLS sources are whitespace-only there in practice, but if a future
  source puts loose text between `<p>` and `<div>` it'll be dropped
  silently. Add a warning if needed.
- **Validator's TLS-only detection is shape-based** (no editions
  declared *and* no `editions/` subdir). A bundle that declares editions
  but has no on-disk witness directory falls through to the strict
  allowlist and surfaces every marker as invalid — that's the right
  behavior for a partial bundle, but worth knowing.
