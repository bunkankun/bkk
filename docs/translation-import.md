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
`<in>/tls-data/translations/` recursively for files whose stem matches
`^(KR\w+?)-([a-z]{2,3}(?:-(?:[A-Z]{2,}|[0-9]{3,4}))?)(?:-([0-9a-f]{4,}))?$`
— `<text-id>-<lang>[-<rev>]`. `--text-id` and `--lang` filter the
resolved set; without filters, every matching file is imported. The
existing `_confirm_bulk` prompt fires when more than one file is
resolved (suppressed by `--yes` or `global.skip_confirm`).

`--by-section` slots a 4-char prefix layer under `translations/`, e.g.
`<out>/translations/KR1h/KR1h0004-en/`.

## Output layout

```
<out-root>/translations/<bundle-id>/
  <bundle-id>.manifest.yaml      # YAML header + per-juan list (the manifest)
  <bundle-id>_001.md             # one Markdown file per source juan
  <bundle-id>_002.md
  ...
  <bundle-id>.source.yaml        # raw teiHeader sidecar (round-trip)
```

`<bundle-id>` is always the input file's stem. `KR1h0004-en.xml` and
`KR1h0004-en-588d9aad.xml` produce two coexisting bundles
(`KR1h0004-en/` and `KR1h0004-en-588d9aad/`); no de-duplication.

### Manifest

```yaml
canonical_identifier: bkk:translation/<bundle-id>/v1
canonical_location: ""
source:
  canonical_identifier: bkk:krp/<source-text-id>/v1
  hash: sha256:…                 # copied from the source bundle's manifest;
                                 # null + stderr warning if the source bundle
                                 # is not yet present under <out>/<source-id>/
language: en
title: …
original_title: …
responsibility:
  - { role: translator, name: … }
  - { role: creator, name: … }
publication: { … }
license: …
date: …
juan:
  - { seq: 1, label: "001", file: <bundle-id>_001.md, hash: sha256:… }
  - { seq: 2, label: "002", file: <bundle-id>_002.md, hash: sha256:… }
hash: sha256:…
```

### Per-juan `.md`

One Pandoc-style attribute span per non-empty segment, one segment per
line:

```markdown
[Le Maître a dit :]{corresp=002-1a.3 lang=en resp=CH modified=2024-07-20T16:46:45.958+09:00}
[Qui gouverne le peuple par l'exemple de sa vertu]{corresp=002-1a.4 resp=CH modified=…}
```

- `corresp` carries the location component only — the `<text-id>_<edition>_`
  prefix is stripped because the bundle's `source` pin makes both implicit.
- `lang` is emitted only when the per-seg `xml:lang` differs from the
  bundle language, so the common case stays terse. A mismatch is
  preserved verbatim (some TLS files carry `xml:lang="en"` on every seg
  even in a French translation — see `KR1h0004-fr`).
- Attribute values containing spaces / quotes / equals are quoted.
- Empty `<seg .../>` elements are dropped silently per the spec
  (paragraph polish is a human pass).

## Hashing

Hashes are over a **parsed, JCS-canonicalized form**, not the storage
text — reformatting the `.md` does not change the hash, the same way
juan YAML formatting does not change juan hashes. Both the per-juan
hash and the manifest hash flow through the existing `sha256_jcs` /
`manifest_hash` helpers in [`bkk/importer/hashing.py`](../module/bkk/importer/hashing.py).

The canonical segment form mirrors the writer's attribute-omission rules
(same-language `lang` is omitted in both) so the rendered Markdown and
the hash agree.

## Juan grouping

Segments are bucketed by the leading digit run of their first
`corresp` location — `001-2a.3` → juan `001`. Numeric labels are
zero-padded to 3 digits in both the filename and the manifest's
`label`. Segs whose corresp doesn't fit the canonical marker-id shape
go into a synthetic `_unknown` bucket and are surfaced in a stderr
warning.

## Source-bundle hash resolution

After writing the bundle, the importer looks at
`<out-root>/<source-text-id>/<source-text-id>.manifest.yaml`. If
present, its `hash` is copied into `source.hash`. If absent, the field
is `null` and a stderr warning fires — the translation imports cleanly
and a later run after the source bundle exists fills the hash on
re-import.

## Conflict / re-import

Translation bundles have no cross-source merge complexity. Re-running
the importer overwrites `<out>/translations/<bundle-id>/` after the
bulk-confirm prompt (or unconditionally with `--yes`). Output is
byte-stable across runs given identical input.

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
  — per-juan Markdown render, manifest build, JCS hash.
- [module/tests/test_translation_import.py](../module/tests/test_translation_import.py)
  — end-to-end coverage against the four samples under
  [module/samples/translations/](../module/samples/translations/).

## Tests

```
cd module && python -m pytest tests/test_translation_import.py -v
```

Covers single-file import, empty-seg drop, per-juan grouping, hash
reproducibility, snapshot coexistence, license/responsibility
extraction, conditional `lang` emission, and source-hash resolution
when the source bundle is present.
