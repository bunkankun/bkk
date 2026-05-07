# TLS importer: CBETA-flavor support

## Context

The TLS source repository ships XML files in two related but distinct
shapes. The original
[`tls-import.md`](tls-import.md)-era importer handled exactly one of
them: classic TLS, where `<body>` opens straight into `<div>` chapter
blocks and juan boundaries are *implicit* — derived by walking marker
ids (`<text-id>_<edition>_<juan>-<location>`) for label changes.

A new "CBETA-flavor" of TLS marks juan boundaries **explicitly** with
`<juan>` elements and pulls more of the structural metadata into the
markup itself. The fixture used to drive this work is
[`X63n1222.xml`](../module/input/tls/tls-texts/data/KR6/KR6q/X63n1222.xml)
(Kanripo id `KR6q0116`).

A third format — mainstream CBETA — is out of scope; it will be added
later as its own importer entry.

### What CBETA-flavor looks like

- `<body>` opens with `<milestone unit="juan" n="1"/>` and a `<pb>`
  before any `<div>` child.
- `<juan fun="open" n="NNN">` elements appear **inside** divs and mark
  juan starts. They carry `<mulu type="卷" n="N"/>` and
  `<jhead>TITLE</jhead>` children. `<juan fun="close" n="N"/>` mirrors
  the close.
- Pre-juan content (everything before the first `<juan fun="open">`) is
  destined for `<text-id>_000.yaml` (seq=0).
- `<mulu>` directly under `<div>` (e.g.
  `<mulu type="序">No. 1222-A 刻修禪要訣序</mulu>`) carries TOC labels
  that don't map to `<head>` elements at all.
- Marker ids carry the juan label (`X63n1222_CBETA_001-...`,
  `X63n1222_CBETA_000-...`) but the explicit `<juan>` element is the
  authoritative boundary.
- Divs include children the classic walker doesn't recognise (`mulu`,
  `docNumber`, `byline`, `dialog`/`sp`, `note`, `lb`, `g`, `anchor`);
  content `<seg>`s nest under those wrappers, not directly under `<p>`.

## What changed

### Format detection and dispatch

`read_tls` runs a one-shot probe on the parsed body —

```python
flavor = "cbeta" if body.find(f".//{_q('juan')}") is not None else "classic"
```

— and dispatches to either `_parse_body_classic` (the previous inline
walker, factored out unchanged) or `_parse_body_cbeta`. The bundle's
`source.format` is `"tls"` for classic, `"tls-cbeta"` for the new
flavor; everything downstream of the parser is shape-agnostic.

### CBETA-flavor parser

`_parse_body_cbeta` walks `<body>` children in document order. Pre-div
`<pb>` / `<milestone>` children are buffered as `leading_markers` and
attached to the first opened `<div>`'s section so they survive
round-trip without a synthetic preamble.

Each `<div>` is consumed by `_section_from_div_cbeta`, which dispatches
its body through `_walk_cbeta_div_children`, a permissive walker that:

- emits `<head>`, `<p>`, nested `<div>`, `<pb>` exactly as the classic
  path does;
- emits text-bearing `<mulu>` as a `cbeta:mulu` marker carrying the
  `type` attribute (序, 其他, …) and the inner text;
- emits `<juan fun="open">` as a `cbeta:juan-start` marker carrying
  the normalised `n` attribute and the `<jhead>` text in extras;
- emits `<juan fun="close">` as a `cbeta:juan-end` marker;
- recurses transparently into `<byline>`, `<dialog>`, `<sp>`, and any
  other unrecognised container so segs nested inside still surface.

`<juan>` and div-level `<mulu>` elements have no `xml:id` in the source.
The parser synthesises ids in the canonical
`<text-id>_<edition>_<location>` shape so existing label-extraction
machinery keeps working:

- `X63n1222_CBETA_001-juan-start` / `_001-juan-end`
- `X63n1222_CBETA_000-mulu-1`, `_000-mulu-2`, …

### Splitting at juan boundaries

`_split_sections_into_cbeta_juans` runs *after* marker-id width
normalisation and groups sections at every `cbeta:juan-start` marker.

Splitting is by marker **index**, not offset
(`_split_section_at_marker_index`) — necessary because a pre-juan
`cbeta:mulu` and the trailing `cbeta:juan-start` can both sit at offset
0 of the same section. Index-based splitting keeps the mulu in the
previous group while pulling the juan-start (and everything after) into
the new group.

Pre-juan content is grouped under the synthetic label `"000"`. If no
content precedes the first juan-start, the `"000"` group is omitted
entirely (no synthetic preface section is emitted).

`_build_juans` consumes the pre-computed `juan_groups` for the CBETA
path and:

- forces `Section.bucket = "front"` on every section in the `"000"`
  group via `_with_section_bucket`, so pre-juan content lands in
  `_000.yaml`'s `front` bucket regardless of what `bucket_sections`
  would otherwise infer;
- captures `juan_label` (the `<jhead>` text) and `juan_marker_id` from
  the first `cbeta:juan-start` of each group on `Juan.metadata`;
- sets `metadata["flavor"] = "cbeta"` so the bundle writer can dispatch
  on it.

### Bundle writer

`_build_toc` is now a dispatcher reading `juan.metadata["flavor"]`. The
classic path (`_build_toc_classic`) emits the existing `<head>`-derived
entries with a new `type: section` field — an additive,
backwards-compatible change. The CBETA path (`_build_toc_cbeta`) emits
two new entry kinds:

- `type: juan` — one per juan with a `juan_label`, span
  `[bucket, offset, end_of_bucket]`, label = the `<jhead>` text.
- `type: mulu` — one per `cbeta:mulu` marker found in the juan's
  sections, span `[bucket, offset, offset]` (a point, not a range),
  label = the marker's content.

Classic TLS bundles never emit `type: juan` / `type: mulu` entries;
CBETA bundles never emit `type: section`.

### TOC `type` field — universal

Every TOC entry in every TLS manifest now carries a `type` field. The
existing [`KR6q0053` sample](../module/samples/KR6q0053/) was
regenerated to pick up `type: section` on its TOC entries (and the
downstream manifest hash that flows from it); no other bytes changed.

## End-to-end test

[`module/tests/test_tls_cbeta.py`](../module/tests/test_tls_cbeta.py)
covers:

- the splitter — pre-juan content groups under `"000"`, content
  starting at a juan-start skips `"000"`;
- end-to-end on `X63n1222`: two juan files emitted, pre-juan content
  forced into the `front` bucket of `_000.yaml`, the `<byline>` seg
  `X63n1222_CBETA_001-0834a14.s1` reaches `_001.yaml`'s body bucket,
  the manifest lists both parts;
- the manifest TOC drops `type: section` and emits one `type: juan`
  (label 修禪要訣) plus two `type: mulu` entries (the preface and the
  其他 mulu);
- `source.format` is `tls-cbeta`.

The classic path's coverage is unchanged: `KR6q0053` still
round-trips, [`test_tls_juan_split.py`](../module/tests/test_tls_juan_split.py)
still pins the implicit-boundary semantics, and
[`test_tls_nested_div.py`](../module/tests/test_tls_nested_div.py)
still pins the `tls:div-*` exporter contract.

## Critical files

- [module/bkk/importer/read/tls.py](../module/bkk/importer/read/tls.py)
  — `_parse_body_cbeta`, `_section_from_div_cbeta`,
  `_walk_cbeta_div_children`, `_walk_cbeta_inline_children`,
  `_split_sections_into_cbeta_juans`, `_split_section_at_marker_index`,
  `_with_section_bucket`.
- [module/bkk/importer/write/bundle.py](../module/bkk/importer/write/bundle.py)
  — `_build_toc` dispatcher, `_build_toc_classic`, `_build_toc_cbeta`.
- [module/tests/test_tls_cbeta.py](../module/tests/test_tls_cbeta.py)
  — new coverage.
- [module/samples/KR6q0053/](../module/samples/KR6q0053/) — manifest
  YAMLs regenerated for the additive `type: section` field.

## Risks / follow-ups

- **Mainstream CBETA still unimplemented.** This change recognises the
  TLS-flavor of CBETA only. A `<body>` that opens with `<juan>`-but-no-
  surrounding-`<div>` would currently route through the new path and
  fall through the permissive walker; not yet fixture-tested.
- **`<juan>` close markers are emitted but unused downstream.** The
  splitter only consumes `cbeta:juan-start`; the close markers exist
  to round-trip the source but no current consumer reads them. If a
  future export path needs them, the markers are already in place.
- **Synthetic mulu / juan ids assume sequential indexing.** A mulu
  added or removed mid-source would shift every subsequent mulu id.
  Stable across re-imports of the same source XML, not stable across
  source edits — same caveat as auto-generated section ids elsewhere.
- **`<lb>`, `<g>`, `<anchor>`, `<docNumber>` are no-ops.** Their text
  content (where present) flows through `_emit_seg`, but they don't
  emit dedicated markers. Add markers if a downstream consumer needs
  them.
