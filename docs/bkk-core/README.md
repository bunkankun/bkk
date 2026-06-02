# BKK Core Knowledge Notes

This document describes the Markdown/YAML data format currently produced by
the core importers. It is intended as a contract for frontend rendering,
navigation, and index construction.

The core knowledge layer is a set of local Markdown notes with YAML
frontmatter. Every note is addressable by a prefixless UUID, stored in a
collection directory, and sharded by the first hexadecimal character of the
UUID.

## Common Model

### Output Root

The importer `--out` argument points at the core root. Importers create their
own collection directories under that root.

Example:

```text
core/
  bibliography/
  concepts/
  graphs/
  semantic-features/
  super-entries/
  syntactic-functions/
  words/
```

### UUIDs

Source XML often uses IDs such as `uuid-3eb2...`. Core Markdown removes the
leading `uuid-` everywhere:

- filenames
- frontmatter `uuid` fields
- link paths
- relation metadata

Example:

```yaml
uuid: 3eb2c600-e234-4c6b-bb79-40e8eff9ee14
```

### Sharding

All first-class notes use:

```text
<collection>/<first-hex>/<uuid>.md
```

Examples:

```text
concepts/3/3eb2c600-e234-4c6b-bb79-40e8eff9ee14.md
bibliography/6/60d39cc0-d76b-4275-8490-886ace4204be.md
words/d/d57eebf9-7218-46d5-95bc-4ac4591b81ed.md
```

### Links

UUID references are rendered as standard relative Markdown filesystem links.
This keeps notes locally editable and navigable without requiring a resolver.

The frontend/index should still treat UUID frontmatter as the canonical
identity. Paths are the current storage policy, not the only possible identity
mechanism.

Examples:

```markdown
[DELIGHT](../../concepts/1/1c7bf322-c905-41e0-9145-7d4b01da86a1.md)
[FOGUANG](../../bibliography/2/2389c812-8053-4187-8f7a-19f6e856050f.md)
[nab.t](../../syntactic-functions/d/d128d787-1ecb-4c4f-8e89-5dd3edea91d1.md)
```

### Common Frontmatter Fields

Every note has:

```yaml
uuid: <prefixless uuid>
type: <record type>
```

Current `type` values:

- `bibliography`
- `concept`
- `graph`
- `semantic-feature`
- `super-entry`
- `syntactic-function`
- `word`

Most importers also include `source`, usually with a source filename:

```yaml
source:
  source_file: semantic-features.xml
```

## Collections

### Concepts

Path:

```text
concepts/<hex>/<uuid>.md
```

Type:

```yaml
type: concept
```

Concept notes represent TLS concept records.

Frontmatter shape:

```yaml
uuid: 3eb2c600-e234-4c6b-bb79-40e8eff9ee14
type: concept
concept: ABLE
labels:
- CAPABLE OF
zh: 能夠
och: 能
```

Body shape:

```markdown
# Concept: ABLE
# Definition
...
# Criteria and general notes
## Old Chinese Criteria
...
# Ontology
## Antonym
- [UNABLE](../d/deb3cd81-03bc-4c7c-9125-a2a8837202c9.md)
# Bibliography
- [BUCK 1988](../../bibliography/6/60d39cc0-d76b-4275-8490-886ace4204be.md)
**A Dictionary ...** page 1008
# Words
```

Important behavior:

- Ontology links target `concepts`.
- Bibliography links target `bibliography`.
- Old Chinese criteria may contain name wikilinks for unmarked Chinese terms,
  for example `néng [[能]]`.
- `# Words` is always present, even when empty.

Index hints:

- Use `concept` as the primary display label.
- `labels`, `zh`, and `och` are alternate labels/search fields.
- Body sections should be indexed as text, but relation targets are most
  reliable in rendered Markdown links and structured importer data is not yet
  duplicated into concept YAML.

### Bibliography

Path:

```text
bibliography/<hex>/<uuid>.md
```

Type:

```yaml
type: bibliography
```

Bibliography notes represent MODS records.

Frontmatter shape:

```yaml
uuid: 60d39cc0-d76b-4275-8490-886ace4204be
type: bibliography
citation_label: BUCK 1988
ref_usage: '1008'
resource_type: text
genres:
- value: book
  authority: marcgt
titles:
- title: A Dictionary of Selected Synonyms...
  lang: eng
  script: Latn
contributors:
- given: Carl Darling
  family: BUCK
  roles:
  - author
origin:
  place: Chicago
  publisher: The University of Chicago Press
  date_issued: '1988'
notes:
- type: general
  text: Indispensable standard handbook.
source:
  format: MODS
  version: '3.6'
```

Chinese and transliterated names are preserved as variants:

```yaml
contributors:
- given: Fengpeng
  family: Lu
  script: Latn
  names:
  - script: Latn
    transliteration: chinese/ala-lc
    given: Fengpeng
    family: Lu
  - script: Hant
    given: 鳳鵬
    family: 盧
```

Body shape:

```markdown
# BUCK 1988

## Title
**A Dictionary ...**

## Contributors
- Carl Darling BUCK, author

## Publication
Chicago: The University of Chicago Press, 1988.

## Notes
Indispensable standard handbook.
```

Index hints:

- `citation_label` is the primary short display label.
- Index all title variants and contributor name variants.
- For Chinese contributors, index both romanized and character forms.

### Graphs

Path:

```text
graphs/<hex>/<uuid>.md
```

Type:

```yaml
type: graph
```

Graph notes represent Chinese graph records.

Frontmatter shape:

```yaml
uuid: f35bd989-7850-4240-9751-87ca014d77b1
type: graph
graphs:
  attested: 閑
  unemended:
  emended:
  standardised:
gloss: 闌也防也禦也大也法也習也睱也戸間切九
xiaoyun:
  headword: 閑
  graph_count: 9
fanqie:
  shangzi:
    attested: 戶
    standard:
  xiazi:
    attested: 閒
    standard:
ids:
  guangyun_jiaoshi_id: '4981'
  pan_wuyun_id: '5025'
locations:
  guangyun_location: '129.15'
pronunciation:
  mandarin:
    jin: xián
  middle_chinese:
    categories:
      聲: 匣
  old_chinese:
    pan_wuyun:
      oc: ɢreen
```

Body shape:

```markdown
# 閑

## Fanqie
戶閒

## Mandarin
xián
```

If no attested graph exists, the standardized form becomes the display graph:

```markdown
# 舔 (standardized)
```

Index hints:

- Most graph data is in frontmatter.
- Body intentionally exposes only display graph, fanqie, and Mandarin/Jin.
- Index `graphs.attested`, `graphs.standardised`, fanqie components,
  external IDs, locations, and pronunciation fields.

### Syntactic Functions

Path:

```text
syntactic-functions/<hex>/<uuid>.md
```

Type:

```yaml
type: syntactic-function
```

Syntactic functions are parsed from `<div type="syn-func">` records in a
single TEI source file.

Frontmatter shape:

```yaml
uuid: e81e5db1-7207-4450-a18d-27a597c5fd67
type: syntactic-function
code: npro.adNab
relations:
- type: taxonymy
  refs:
  - uuid: 8694d163-4347-4386-b028-e99017c8995b
    label: npro.adNPab{S}
source:
  source_file: syntactic-functions.xml
```

Descriptions and notes are body-only:

```markdown
# npro.adNab

## Description
pronoun preceding and modifying an abstract nominal

## Notes
Most abstract nominals ...

## Links
### Taxonomy
- [npro.adNPab{S}](../8/8694d163-4347-4386-b028-e99017c8995b.md)
```

Index hints:

- `code` is the primary display and lookup field.
- `relations` are structured enough for graph navigation.
- Do not expect descriptions/notes in YAML.

### Semantic Features

Path:

```text
semantic-features/<hex>/<uuid>.md
```

Type:

```yaml
type: semantic-feature
```

Semantic features are parsed from `<div type="sem-feat">` records in a single
TEI source file.

Frontmatter shape:

```yaml
uuid: 667a2e02-a4e1-4484-ae80-1382510681be
type: semantic-feature
code: imp
relations:
- type: source-references
  target_type: bibliography
  refs:
  - uuid: 574fc47b-68e2-4f99-a5c9-692ef8338357
    label: BROWN 2005
    title: Encyclopedia of Language and Linguistics. Second Edition
    scope: '565'
    scope_unit: page
source:
  source_file: semantic-features.xml
```

Descriptions and notes are body-only:

```markdown
# imp

## Description
Imperative use of a verb...

## Links
### Source References
- [BROWN 2005](../../bibliography/5/574fc47b-68e2-4f99-a5c9-692ef8338357.md) - Encyclopedia ...; page 565
```

Index hints:

- `code` is the primary display and lookup field.
- `relations[].target_type` distinguishes same-type semantic-feature links
  from bibliography links.
- Do not expect descriptions/notes in YAML.

### Super-Entries

Path:

```text
super-entries/<hex>/<uuid>.md
```

Type:

```yaml
type: super-entry
```

Super-entry notes are top-level word-family/index records parsed from TEI
`superEntry`. They are not the editable word records. Actual concept-scoped
word records live in `words/`.

Frontmatter shape:

```yaml
uuid: 703886f9-eb81-4985-b886-f9eb81598567
type: super-entry
orth: 喜
n: '4'
forms:
- orth: 喜
- orth: 喜
  graph_uuid: c4711853-e554-4934-bdf2-97e5b33fbc53
  pronunciations:
  - lang: zh-Latn-x-pinyin
    value: xǐ
  - lang: zh-x-oc
    value: qhɯʔ
  - lang: zh-x-mc
    value: hɨ
entries:
- uuid: d57eebf9-7218-46d5-95bc-4ac4591b81ed
  sense_count: 16
  concept: DELIGHT
  concept_uuid: 1c7bf322-c905-41e0-9145-7d4b01da86a1
  n: '74'
source:
  source_file: uuid-703886f9-eb81-4985-b886-f9eb81598567.xml
```

Body shape:

```markdown
# Super-entry: 喜

## Forms
- Orth: 喜
- Orth: [喜](../../graphs/c/c4711853-e554-4934-bdf2-97e5b33fbc53.md)
  - Pinyin: xǐ
  - Old Chinese: qhɯʔ
  - Middle Chinese: hɨ

## Words
- [CUSTOM](../../words/0/044ecd60-1d2f-40b2-a902-3c1384f4b2ca.md) (1 sense, n=3)
- [DELIGHT](../../words/d/d57eebf9-7218-46d5-95bc-4ac4591b81ed.md) (16 senses, n=74)
```

Important behavior:

- Word links are sorted alphabetically by concept label.
- `entries` frontmatter is an index only. Full word details are in the
  linked `words/` notes.
- The same graph may appear in multiple form variants.

Index hints:

- Use `orth` as the display label.
- Index `forms[].graph_uuid` and pronunciation variants.
- Use `entries[]` to build a word-family to word-record relation.

### Words

Path:

```text
words/<hex>/<uuid>.md
```

Type:

```yaml
type: word
```

Word notes are concept-scoped lexical entries parsed from child `<entry>`
elements inside a TEI `superEntry`.

Frontmatter shape:

```yaml
uuid: d57eebf9-7218-46d5-95bc-4ac4591b81ed
type: word
super_entry_uuid: 703886f9-eb81-4985-b886-f9eb81598567
super_entry_orth: 喜
concept: DELIGHT
concept_uuid: 1c7bf322-c905-41e0-9145-7d4b01da86a1
n: '74'
form:
  orth: 喜
  graph_uuid: c4711853-e554-4934-bdf2-97e5b33fbc53
  pronunciations:
  - lang: zh-Latn-x-pinyin
    value: xǐ
  - lang: zh-x-oc
    value: qhɯʔ
  - lang: zh-x-mc
    value: hɨ
bibliography:
- uuid: 2389c812-8053-4187-8f7a-19f6e856050f
  label: FOGUANG
  title: 佛光大辭典 Fóguāng dàcídiǎn The Foguang Dictionary of Buddhism
  scope: 4899b
  scope_unit: page
senses:
- uuid: 45ddee60-d2a7-4973-9289-b93f0f921ac4
  body_number: 1
  n: '2'
  pos: N
  syntactic_functions:
  - label: nab.t
    uuid: d128d787-1ecb-4c4f-8e89-5dd3edea91d1
  semantic_features:
  - label: psych
    uuid: 98e7674b-b362-466f-9568-d0c14470282a
  usages:
  - value: '3'
    type: warring-states-currency
source:
  source_file: uuid-703886f9-eb81-4985-b886-f9eb81598567.xml
```

Body shape:

```markdown
# 喜: DELIGHT

- Super-entry: [喜](../../super-entries/7/703886f9-eb81-4985-b886-f9eb81598567.md)
- Concept: [DELIGHT](../../concepts/1/1c7bf322-c905-41e0-9145-7d4b01da86a1.md)

## Form
- Orth: [喜](../../graphs/c/c4711853-e554-4934-bdf2-97e5b33fbc53.md)
  - Pinyin: xǐ
  - Old Chinese: qhɯʔ
  - Middle Chinese: hɨ

## Definition
Xǐ 喜 ... is openly manifested delight...

## Bibliography
- [FOGUANG](../../bibliography/2/2389c812-8053-4187-8f7a-19f6e856050f.md) - 佛光大辭典 ...; page 4899b

## Senses
1. **[nab.t](../../syntactic-functions/d/d128d787-1ecb-4c4f-8e89-5dd3edea91d1.md)** *[psych](../../semantic-features/9/98e7674b-b362-466f-9568-d0c14470282a.md)* delight (in someone N), joy about (something N) **2 Attributions**
   - Usage: warring-states-currency: 3
```

Important behavior:

- Entry-level and sense definitions are body-only.
- Sense UUIDs are not exposed in body text.
- `senses[].body_number` maps a frontmatter sense UUID to the numbered body
  item.
- POS is frontmatter-only and is not repeated in the body.
- Sense syntax is rendered inline in bold before the definition.
- Sense semantic features are rendered inline in italics after syntax.
- If the sense has an `n` value, the body line ends with `**<n> Attributions**`.

Index hints:

- Use `form.orth` plus `concept` as the primary display pair.
- Use `super_entry_uuid` to group words by top-level graph/word family.
- Use `concept_uuid` to connect words to concepts.
- Use `senses[].body_number` for frontend deep navigation to a numbered sense.
- Index `senses[].syntactic_functions[]` and `senses[].semantic_features[]`
  as typed outgoing links.
- Index body definitions as searchable lexical text.

## Import Commands

All core importers use `--in` for the source XML directory and `--out` for the
core output root.

```bash
bkk import concepts --in module/input/core/concepts --out module/output/core --yes
bkk import bibliography --in module/input/core/bibliography --out module/output/core --yes
bkk import graphs --in module/input/core/graphs --out module/output/core --yes
bkk import syntactic-functions --in module/input/core/syntactic-functions --out module/output/core --yes
bkk import semantic-features --in module/input/core/semantic-features --out module/output/core --yes
bkk import words --in module/input/core/words --out module/output/core --yes
```

The explicit form also works:

```bash
bkk import --format words --in module/input/core/words --out module/output/core --yes
```

Current `--text-id` filtering:

- concepts: source filename stem, UUID, or concept name where supported by the
  reader/importer path
- bibliography and graphs: source filename stem or UUID
- syntactic-functions: UUID or code
- semantic-features: UUID or code
- words: super-entry UUID, source filename stem, orthograph, word-entry UUID,
  or concept name

`--on-exists skip` leaves existing Markdown files unchanged.

## Index Construction Guidance

### Primary Keys

Use `(type, uuid)` as the logical primary key. Paths are useful storage
addresses, but UUIDs should drive identity.

For `type: word`, the UUID is the word-entry UUID, not the super-entry UUID.

### Suggested Tables or Index Buckets

- `notes`: uuid, type, path, title/display label, source file.
- `labels`: uuid, type, label, label_type.
- `links`: source_uuid, source_type, target_uuid, target_type, label, path.
- `frontmatter`: raw parsed YAML or typed per-collection projections.
- `body_text`: note UUID, section heading, text, offset/line metadata if useful.
- `word_senses`: word_entry_uuid, sense_uuid, body_number, pos, source n, usage.

### Link Extraction

Extract links from both places:

- YAML frontmatter relation fields where present.
- Markdown body links for local navigation and links not duplicated in YAML.

The frontend should resolve links by path for local navigation, but the index
should normalize them back to target UUID and type where possible.

### Collection and Type Caveats

- Collection names are plural path names: `concepts`, `graphs`,
  `syntactic-functions`, `semantic-features`, `super-entries`, `words`.
- Type names are singular logical names, except `super-entry`:
  `concept`, `graph`, `syntactic-function`, `semantic-feature`, `word`.
- `bibliography` is both the collection and type name.

### Body Versus Frontmatter

Several note types intentionally keep prose out of YAML to avoid sync drift:

- syntactic-function descriptions and notes
- semantic-feature descriptions and notes
- word entry definitions
- word sense definitions

The frontend should render from the body for editorial text, and the index
should index body text as searchable prose.

## Current Scope

The current core format covers:

- concepts
- bibliography records
- graphs
- syntactic functions
- semantic features
- super-entries
- words

Future core record types should follow the same pattern:

```text
<collection>/<hex>/<uuid>.md
```

with prefixless UUIDs, `type` in frontmatter, and local relative Markdown links.
