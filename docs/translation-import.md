# Translation importer: TLS `type="transl"` → BKK translation bundle

## Context

TLS / HXWD ships translations of its source texts as TEI XML files under
`tls-data/translations/`, one file per `(text-id, language[, revision])`
tuple — e.g. `KR1h0004-en.xml`, `KR1h0004-fr-138ffefe.xml`. The body of
each file is a flat list of `<seg corresp="#KR…_tls_<location>">`
elements that pin one translated span to a source marker; the
`<teiHeader>` carries title, editor, license, source bibliography, and
creation metadata.

The BKK design treats translations as **bundles in their own right**
(see [`bunkankun.md` §"Translations"](../bunkankun.md), lines 500–641):
Markdown body with a YAML header, addressable, hashable, composable with
their source via recipes. This importer is the TLS → BKK migration path.

## CLI

```
bkk import --format translation --in <tls-root> --out <out-root>
                                [--text-id KR1h0004]
                                [--lang en]
                                [--by-section]
                                [--yes]
```

`--in` defaults to `global.tls_root` from `.bkkrc`. The reader scans
`<in>/tls-data/translations/` recursively (so the `ai/` and `by-hand/`
subtrees are picked up alongside top-level files) for stems matching
`^(?P<text>[A-Z][A-Za-z0-9]+?)-(?P<lang>[a-z]{2,3})(?:-(?P<tail>.+))?$`
— `<text-id>-<lang>[-<tail>]`. The text-id accepts any uppercase-letter
prefix (KR, CH, T, B, EX, …); lang is a bare 2-3 letter code (TLS
filenames carry no BCP-47 region/variant subtag in practice); anything
after that — variant codes like `ku`, translator codes like `ge`/`ds`,
revision hashes, or arbitrary combinations — is opaque `<tail>` and is
preserved verbatim in the bundle id. `--text-id` and `--lang` filter
the resolved set; without filters, every matching file is imported. The
existing `_confirm_bulk` prompt fires when more than one file is
resolved (suppressed by `--yes` or `global.skip_confirm`).

`--by-section` slips a 4-char prefix between `translations/` and the
text-id, so a large corpus doesn't crowd a single directory: e.g.
`<out>/translations/KR1h/KR1h0004/en/KR1h0004-en/`.

## Output layout

```
<out-root>/translations/<source-text-id>/<lang>/<bundle-id>/
  <bundle-id>.md            # bundle entry point: YAML manifest + juan TOC body
  <bundle-id>_001.md        # one Markdown file per source juan
  <bundle-id>_002.md
  ...
  <bundle-id>.source.yaml   # raw teiHeader sidecar (round-trip)
```

`<bundle-id>` is always the input file's stem. `KR1h0004-en.xml` and
`KR1h0004-en-588d9aad.xml` produce two coexisting bundles under
`translations/KR1h0004/en/`; no de-duplication. Different languages of
the same source live side-by-side under `translations/<text-id>/`.
Translator-coded variants land alongside their plain-lang siblings
(`KR5e0001-en-ge-79d65648/` and `KR5e0001-en-ds-…/` both under
`translations/KR5e0001/en/`); Japanese-kuntenized files
(`CH2a1436-ja-ku-96965668/`) group under `translations/<text-id>/ja/`
since `ku` is part of the bundle id rather than the lang grouping.

### Bundle entry-point (`<bundle-id>.md`)

YAML front-matter carries the full manifest; the body is a
human-readable juan TOC. Example header:

```yaml
canonical_identifier: bkk:translation/<bundle-id>/v1
canonical_location: ''
source:
  canonical_identifier: bkk:krp/<source-text-id>/v1
  hash: sha256:…                 # copied from the source bundle's manifest;
                                 # null + stderr warning if the source bundle
                                 # is not yet present under <out>/<source-id>/
language: en
title: …
original_title: …
responsibility:
  - {role: translator, name: …}
  - {role: creator, name: …}
publication: {…}
license: …
date: …
juan:
  - {seq: 1, label: '001', file: <bundle-id>_001.md, hash: sha256:…}
  - {seq: 2, label: '002', file: <bundle-id>_002.md, hash: sha256:…}
hash: sha256:…
```

Body shape:

```markdown
# <title>

## Juan

- [001](<bundle-id>_001.md)
- [002](<bundle-id>_002.md)
…
```

The TOC is purely human-facing; hashing and machine consumption use the
front-matter `juan:` list. The `# <title>` heading is skipped when the
bundle has no `title`.

### Per-juan file (`<bundle-id>_NNN.md`)

YAML front-matter carries juan-level metadata only — not a copy of the
bundle manifest. The body is a sequence of Pandoc-style attribute spans,
one per non-empty segment, one line each:

```markdown
---
canonical_identifier: bkk:translation/<bundle-id>/v1#juan/<label>
bundle: bkk:translation/<bundle-id>/v1
juan_seq: 1
juan_label: '002'
hash: sha256:…
markers:
- {ref: 002-1a.3, corresp: [002-1a.3], resp: CH, modified: '2024-07-20T16:46:45.958+09:00'}
- {ref: [002-1a.4, 002-1a.5], corresp: [002-1a.4, 002-1a.5], resp: CH, modified: '2024-07-20T16:48:01.214+09:00'}
- {ref: 002-1a.4-2, corresp: [002-1a.4], resp: CH, modified: '2024-07-20T16:50:11.002+09:00'}
---
[Le Maître a dit :]{@002-1a.3}
[Qui …]{@002-1a.4 @002-1a.5}
[Et donc …]{@002-1a.4-2}
```

Header fields:

- `canonical_identifier` carries the juan-local fragment `#juan/<label>`,
  making each juan separately addressable.
- `bundle` back-references the entry point.
- `juan_seq`/`juan_label` mirror the matching `juan:` entry in the
  bundle manifest.
- `hash` equals that entry's `hash` (single source of truth: edit a
  juan's body, the hash changes in one place, both files stay in sync).
- `markers:` — one entry per body span, in body order. Each entry holds
  `ref` (body-side pairing key; a single string for single-corresp segs,
  a list for multi-corresp), `corresp` (always a list of raw source
  location strings), and, when set, `lang` (only when it differs from
  the bundle's language, same omission rule as before), `resp`,
  `modified`.

Body shape:

- Single-corresp seg → `[text]{@<loc>}`.
- Multi-corresp seg → `[text]{@<loc1> @<loc2> …}` (matches the list
  shape of `ref` in the header entry).
- Collision (a corresp already used as a body `ref` in the same juan) →
  the second/third/… occurrence is suffixed `-2`/`-3`/… so every body
  ref token is unique within the juan. Suffix lands on `ref` only; the
  original location is preserved verbatim in `corresp`.
- Empty `<seg .../>` elements are dropped silently per the spec
  (paragraph polish is a human pass).
- Attribute values containing `[`, `]`, `\`, or newlines are escaped in
  the rendered span text.

Round-trip from a per-juan file back to a span's full attribute set is a
positional pair: read `markers:` in order, pair entry `i` with body
line `i`.

## Hashing

Three-tier chain, all under `sha256_jcs` (JCS → SHA-256):

1. **Per-juan hash** — JCS over `{segments: [<canonical seg> …]}`. The
   canonical seg form drops storage-only fields (e.g. the body `ref`
   shorthand and `markers` storage) and applies the same `lang`
   omission rule. Lands in the per-juan file's `hash` and the bundle
   manifest's `juan[i].hash`.
2. **Bundle hash** — JCS over `{<manifest-without-hash>, segments:
   [<flat canonical segs across juans, juan-seq order>]}`. Matches the
   spec's "parsed canonical form: header + ordered list of segments".
3. The result patches into the manifest's `hash` field before the
   manifest is serialized into the entry-point file's front-matter.

The `markers:` list is storage-form only — it does **not** participate
in the canonical hash. Reformatting a `.md` (whitespace, key order,
flow vs block YAML) does not change either hash.

## Juan grouping

Segments are bucketed by the leading digit run of their first
`corresp` location — `001-2a.3` → juan `001`. Numeric labels are
zero-padded to 3 digits in the filename, the manifest's `juan[].label`,
and each per-juan file's `juan_label`. Segs whose corresp doesn't fit
the canonical marker-id shape go into a synthetic `_unknown` bucket and
are surfaced in a stderr warning.

## Source-bundle hash resolution

After writing, the importer looks at
`<out-root>/<source-text-id>/<source-text-id>.manifest.yaml` (or the
`--by-section`-aware equivalent — i.e. the same path the KRP/TLS
writers would have produced). If present, its `hash` is copied into
`source.hash`. If absent, the field is `null` and a stderr warning
fires — the translation imports cleanly and a later run after the
source bundle exists fills the hash on re-import.

## Conflict / re-import

Translation bundles have no cross-source merge complexity. Re-running
the importer overwrites `<out>/translations/<text-id>/<lang>/<bundle-id>/`
after the bulk-confirm prompt (or unconditionally with `--yes`). Output
is byte-stable across runs given identical input.

Pass `--on-exists skip` to leave any bundle directory that already
exists on disk untouched. In bulk discovery the existence check runs
before the confirmation prompt, so the prompt lists only the bundles
that will actually be (re)written; a one-line `skipped N bundle(s)`
report precedes it. The default is `--on-exists overwrite` (today's
behavior).

## Reader notes (`module/bkk/importer/read/translation.py`)

- Independent of `read/tls.py`. TLS source-text files carry divs,
  juans, and annotations; translations carry only a flat seg list.
  Sharing code would force concept conflation.
- Validates `TEI/@type == "transl"`; rejects otherwise.
- `language_hint` and `bundle_id_hint` (derived from the filename) take
  precedence over what the file declares. Several TLS files carry
  mislabelled per-seg `xml:lang`; the filename is the source of truth.
- `<creation>` carries a free-form "Initially created: <date> by <name>"
  string and a `@resp` marker. The reader extracts the `by <name>` tail
  (falling back to the resp value) and records it as a `creator`
  responsibility entry — `@resp` is a marker, not a role name.

## Critical files

- [module/bkk/importer/cli.py](../module/bkk/importer/cli.py) —
  `_run_translation`, `_resolve_translation_targets`, `_import_one_translation`,
  `--format translation`, `--lang`, `_TRANSLATION_STEM_RE`.
- [module/bkk/importer/ir.py](../module/bkk/importer/ir.py) —
  `TranslationSegment`, `TranslationBundle`.
- [module/bkk/importer/read/translation.py](../module/bkk/importer/read/translation.py)
  — TEI parser, marker-id splitter, header lift.
- [module/bkk/importer/write/translation.py](../module/bkk/importer/write/translation.py)
  — bundle entry-point + per-juan render, marker collision logic,
  manifest build, JCS hash.
- [module/tests/test_translation_import.py](../module/tests/test_translation_import.py)
  — end-to-end coverage against the four samples under
  [module/samples/translations/](../module/samples/translations/).

## Tests

```
cd module && python -m pytest tests/test_translation_import.py -v
```

Covers single-file import, empty-seg drop, per-juan grouping, hash
reproducibility, snapshot coexistence, license/responsibility
extraction, conditional `lang` emission, source-hash resolution when
the source bundle is present, per-juan header shape, body-ref/header
correspondence, and bundle-hash sensitivity to segment edits.

## Live translation contributions (`org.bunkankun.translation.segment`)

Translations posted to Bluesky carry their text inline in the record (see
[`lexicons/org.bunkankun.translation.segment.json`](../lexicons/org.bunkankun.translation.segment.json)).
The record references a target bundle via `translation_id` — matching the
`<bundle-id>` produced above — so a harvested segment knows which bundle
it belongs in. The shape parallels annotations: each record anchors one
span in the source text and supplies the translated `text`, `lang`, and
optional `title` / `note`.

Today, `bkk annotations harvest` writes harvested translation records as
JSONL under `<translations_root>/<translation_id>/<text-id>_NNN.tr.jsonl`
(see [`docs/bkk-annotations/README.md`](bkk-annotations/README.md#translation-segment-archive)).
Folding those JSONL segments into the matching bundle's juan markdown
files — the inverse of the TLS importer above — is a separate task. The
shape needed for that fold:

- Bundle id resolution: `translation_id` → `<translations_root>/<source-text-id>/<lang>/<bundle-id>/`.
- Juan resolution: parse `NNN` from the marker id (same logic the
  harvester already uses) → `<bundle-id>_NNN.md`.
- Segment placement: each harvested record becomes one `<seg corresp="…">`-
  equivalent block in the per-juan markdown body; ordering by
  `(bucket, bucket_offset)` matches the bundle's existing convention.
- Bundle-doesn't-exist: drop with a warning. Creating the bundle from
  scratch is a separate operation (a translator first needs the bundle
  manifest, license, etc.).
