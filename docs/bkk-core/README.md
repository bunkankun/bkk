# BKK Core Knowledge Notes

This document describes the pure-YAML data format produced by the core
importers. It is the contract for frontend rendering, navigation, editing,
and index construction.

The core knowledge layer is a set of structured YAML records. Every record
is addressable by a prefixless UUID, stored in a collection directory, and
sharded by the first hexadecimal character of the UUID. There is no
Markdown body; prose lives in named string fields inside the record.

## Common model

### Output root

The importer `--out` argument points at the core root. Importers create
their own collection directories under that root.

```text
core/
  bibliography/
  concepts/
  graphs/
  rhetorical-devices/
  semantic-features/
  senses/
  super-entries/
  syntactic-functions/
  word-relations/
  words/
```

### File shape

All records use:

```text
<collection>/<first-hex>/<uuid>.yml
```

Each file is a single YAML document. There is **no `---` fence** and no
Markdown body — the file is pure YAML from the first line.

Examples:

```text
concepts/3/3eb2c600-e234-4c6b-bb79-40e8eff9ee14.yml
bibliography/6/60d39cc0-d76b-4275-8490-886ace4204be.yml
words/d/d57eebf9-7218-46d5-95bc-4ac4591b81ed.yml
senses/4/45ddee60-d2a7-4973-9289-b93f0f921ac4.yml
```

### UUIDs

Source XML often uses IDs such as `uuid-3eb2…`. Core records drop the
leading `uuid-` everywhere — filenames, the `uuid` field, every relation
list. Each record carries:

```yaml
uuid: <prefixless uuid>
type: <record type>
```

`type` values:

- `bibliography`
- `concept`
- `graph`
- `rhetorical-device`
- `semantic-feature`
- `sense`
- `super-entry`
- `syntactic-function`
- `word`
- `word-relation`

### Relations

Relations are **bare UUID strings** (or lists of bare UUIDs). Display
labels are never denormalized into a record — the indexer resolves them
at query time via JOIN against the target record's primary label.

```yaml
antonyms:
- deb3cd81-03bc-4c7c-9125-a2a8837202c9
hyponyms:
- 4ba683a6-974f-4812-a94a-6b5ae8818e19
- 11c629f4-b7c5-4617-a97d-a2d76292def6
```

Structured cross-collection refs (typically bibliography) carry a typed
key plus scope/note metadata:

```yaml
bibliography:
- bibliography_uuid: 60d39cc0-d76b-4275-8490-886ace4204be
  scope: '1008'
  scope_unit: page
- bibliography_uuid: ab831347-9626-498d-aa1e-eb43eae72d05
  notes:
  - CAN
  - posse refers to an ability as a consequence of power…
```

### Prose fields

Prose lives in named string fields. Two prose conventions:

- `[[X]]` — wikilink to a CJK super-entry by orthograph. Resolved by the
  indexer against the super-entry orth map.
- bare Markdown links — anything the author wants outside the structured
  relation lists.

There are no `{{REF:…}}` macros. Prose fields are freeform Markdown.

Prose-bearing fields by record type:

- `concept.definition`, `concept.criteria[].text`
- `syntactic-function.description`, `syntactic-function.notes`
- `semantic-feature.description`, `semantic-feature.notes`
- `rhetorical-device.description`, `rhetorical-device.notes`,
  `rhetorical-device.location`
- `word.definition`
- `sense.definition`

### Source provenance

Most importers include a `source` block recording the originating XML
file:

```yaml
source:
  source_file: semantic-features.xml
```

## Collections

### Concepts

Path: `concepts/<hex>/<uuid>.yml` · Type: `concept`

```yaml
uuid: 3eb2c600-e234-4c6b-bb79-40e8eff9ee14
type: concept
concept: ABLE
alt_labels:
- CAPABLE OF
- COMPETENT TO
zh: 能夠
och: 能
definition: HAVE FEATURES one NEEDS in SELF:oneself FOR ACHIEVING something.
criteria:
- type: old-chinese-criteria
  text: |
    1. The commonest word is néng [[能]] "have an inherent capacity for…"
    2. Kě yǐ [[可以]] "be in an objective position to…"
- type: modern-chinese-criteria
  text: |
    能夠
    能
    會
antonyms:
- deb3cd81-03bc-4c7c-9125-a2a8837202c9
hypernyms:
- fb02970d-7e8c-43ca-b0fd-fddc6055d130
hyponyms: []
see_also:
- 297bd4cc-f53e-42a5-b51f-5150aa0f4795
bibliography:
- bibliography_uuid: 60d39cc0-d76b-4275-8490-886ace4204be
  scope: '9.95'
  scope_unit: page
source:
  source_file: concepts.xml
```

Index hints:

- `concept` is the primary display label.
- `alt_labels`, `zh`, `och` are alternate labels / search fields.
- `criteria[].text` may contain `[[X]]` wikilinks to super-entries.

### Bibliography

Path: `bibliography/<hex>/<uuid>.yml` · Type: `bibliography`

```yaml
uuid: 0009ccda-306e-47bb-97e2-7da0c80b3302
type: bibliography
citation_label: LU FENGPENG 1997
ref_usage: '0'
resource_type: text
genres:
- value: article
  authority: marcgt
titles:
- title: 段玉裁的轉注論及其運用
  script: Hant
- title: Duan Yucai de zhuan zhu lun ji qi lian yong
  type: translated
  script: Latn
contributors:
- type: personal
  roles: [author]
  given: Fengpeng
  family: Lu
  script: Latn
  names:
  - {script: Latn, given: Fengpeng, family: Lu, transliteration: chinese/ala-lc}
  - {script: Hant, given: 鳳鵬, family: 盧}
source:
  format: MODS
  version: '3.6'
```

Index hints:

- `citation_label` is the primary short display label.
- Index all title and contributor name variants; both romanized and CJK
  forms for Chinese contributors.

### Graphs

Path: `graphs/<hex>/<uuid>.yml` · Type: `graph`

```yaml
uuid: f35bd989-7850-4240-9751-87ca014d77b1
type: graph
graphs:
  attested: 閑
  unemended: null
  emended: null
  standardised: null
gloss: 闌也防也禦也大也法也習也睱也戸間切九
xiaoyun: {headword: 閑, graph_count: 9}
fanqie:
  shangzi: {attested: 戶, standard: null}
  xiazi:  {attested: 閒, standard: null}
ids:       {guangyun_jiaoshi_id: '4981', pan_wuyun_id: '5025'}
locations: {guangyun_location: '129.15'}
pronunciation:
  mandarin: {jin: xián}
  middle_chinese:
    categories: {聲: 匣, 等: 二, 呼: 開, 韻部: 山, 調: 平, 攝: 山}
  old_chinese:
    pan_wuyun: {oc: ɢreen, yunbu: 元2, phonetic: 閑}
```

Index hints:

- Display label: `graphs.attested`, falling back to `graphs.standardised`.
- Index `graphs.*`, fanqie components, external IDs, locations, and
  every pronunciation field.

### Syntactic functions

Path: `syntactic-functions/<hex>/<uuid>.yml` · Type: `syntactic-function`

Parsed from `<div type="syn-func">` records in
`core/syntactic-functions.xml`.

```yaml
uuid: d128d787-1ecb-4c4f-8e89-5dd3edea91d1
type: syntactic-function
code: nab.t
description: transitive abstract noun, i.e. an abstract, typically deverbal noun…
notes: |
  Action nouns are often semantically as transitive as the verbs they derive from…
taxonomy_parents:
- 0b9195a6-7aa5-4f97-b489-54e635423cdd
- d76e92fd-a62d-4b70-82ca-dabb844acc6c
source:
  source_file: syntactic-functions.xml
```

Index hints:

- `code` is the primary display and lookup field.
- `taxonomy_parents` is a bare-UUID list of same-collection links.

### Semantic features

Path: `semantic-features/<hex>/<uuid>.yml` · Type: `semantic-feature`

Parsed from `<div type="sem-feat">` records in
`core/semantic-features.xml`.

```yaml
uuid: 98e7674b-b362-466f-9568-d0c14470282a
type: semantic-feature
code: psych
description: mental/psychological
notes: ''
source_references:
- bibliography_uuid: 574fc47b-68e2-4f99-a5c9-692ef8338357
  scope: '565'
  scope_unit: page
source:
  source_file: semantic-features.xml
```

Index hints:

- `code` is the primary display and lookup field.
- `source_references` is the bibliography pointer list (parallel to
  `concept.bibliography`).

### Rhetorical devices

Path: `rhetorical-devices/<hex>/<uuid>.yml` · Type: `rhetorical-device`

Parsed from `<div type="rhet-dev">` records in
`core/rhetorical-devices.xml`. Each record names one rhetorical device
(epiphonema, hendiadys, refocalisation, …); attestations in actual texts
live in the annotations layer as `rhetorical-device-attestation` records
(see `bkk-annotations/README.md`).

```yaml
uuid: afa1ba57-b81d-4da6-a201-b701fec3f01b
type: rhetorical-device
code: ACCLAMATIO
translations:
  zh: 總結法
description: |
  插入總結感嘆法 METALINGUISTIC COMMENT in the form of a summary…

  Greek: Epiphonema.
notes: |
  REF: Lausberg 879…
location: |
  …optional prose locus marker from <div type="rhet-dev-loc">…
hypernyms:
- fea50057-3cb0-4289-beef-3dedf0185d61
hyponyms:
- cbbf1df7-8a4f-4a4c-9e40-460cfc0c8ff5
antonyms:
- aff6c751-db90-43c2-9f36-5042e0336d79
source_references:
- bibliography_uuid: 60d39cc0-d76b-4275-8490-886ace4204be
  scope: '9.95'
  scope_unit: page
source:
  source_file: rhetorical-devices.xml
  resp: '#CH'
  date: '2024-02-14T19:25:38.413+09:00'
```

Notes:

- `code` is the upper-case device label from `<head>` (HENDIADYS,
  ACCLAMATIO, …) — primary display and lookup field.
- `translations` is the top-level `<list type="translations">`
  (`xml:lang` → text). The pointer-section `translations` list is folded
  into the same dict.
- Source-XML `<list type="taxonymy">` becomes `hyponyms` (taxonymy items
  are sub-devices of the head, matching the `concepts` convention rather
  than `syntactic-functions`' `taxonomy_parents`).
- `notes` filters out `<p>undefined</p>` placeholder paragraphs.

Index hints:

- Display label: `code`.
- Index `translations.*` as alternate labels.
- Treat `hypernyms` / `hyponyms` / `antonyms` as typed outgoing links
  inside the collection.

### Super-entries

Path: `super-entries/<hex>/<uuid>.yml` · Type: `super-entry`

Top-level word-family / index records parsed from TEI `superEntry`.
Their child word-entries are split out into the `words/` collection.

```yaml
uuid: 703886f9-eb81-4985-b886-f9eb81598567
type: super-entry
orth: 喜
n: '4'
forms:
- orth: 喜
- orth: 喜
  graph_uuids: [c4711853-e554-4934-bdf2-97e5b33fbc53]
  pronunciations:
  - {lang: zh-Latn-x-pinyin, value: xǐ}
  - {lang: zh-x-oc,          value: qhɯʔ}
  - {lang: zh-x-mc,          value: hɨ}
word_uuids:
- 044ecd60-1d2f-40b2-a902-3c1384f4b2ca
- d57eebf9-7218-46d5-95bc-4ac4591b81ed
- 57102a8f-2ac7-483e-b9a2-d966689bbf86
- 338ddf66-a845-41e8-9101-64aa32a68ea3
source:
  source_file: uuid-703886f9-eb81-4985-b886-f9eb81598567.xml
```

Notes:

- `word_uuids` is the bare-UUID list of child word records. The
  denormalized `entries[]` cache used by the old format is gone — the
  indexer derives concept/sense-count summaries via JOIN against the
  word records.
- The same graph may appear in multiple form variants.

Index hints:

- Display label: `orth`.
- Index `forms[].graph_uuids` and every pronunciation variant.

### Words

Path: `words/<hex>/<uuid>.yml` · Type: `word`

Concept-scoped lexical entries parsed from child `<entry>` elements
inside a TEI `superEntry`.

```yaml
uuid: d57eebf9-7218-46d5-95bc-4ac4591b81ed
type: word
super_entry_uuid: 703886f9-eb81-4985-b886-f9eb81598567
concept_uuid: 1c7bf322-c905-41e0-9145-7d4b01da86a1
n: '74'
form:
  orth: 喜
  graph_uuids: [c4711853-e554-4934-bdf2-97e5b33fbc53]
  pronunciations:
  - {lang: zh-Latn-x-pinyin, value: xǐ}
  - {lang: zh-x-oc,          value: qhɯʔ}
  - {lang: zh-x-mc,          value: hɨ}
definition: |
  Xǐ 喜 (ant. yōu 憂 "worry") is openly manifested delight…
bibliography:
- bibliography_uuid: 2389c812-8053-4187-8f7a-19f6e856050f
  scope: 4899b
  scope_unit: page
sense_uuids:
- 45ddee60-d2a7-4973-9289-b93f0f921ac4
- 7e95214c-9f48-4227-b809-0432fa83a101
- 58b4a3ba-a1b5-4b50-9a36-81d2fb17577f
source:
  source_file: uuid-703886f9-eb81-4985-b886-f9eb81598567.xml
```

Notes:

- `super_entry_orth` and `concept` label denormalizations are gone — use
  `super_entry_uuid` / `concept_uuid` and JOIN.
- `sense_uuids` is the **ordered** list. The frontend numbers displayed
  senses from list index. There is no `body_number` field.
- `definition` may contain `[[X]]` wikilinks to super-entries.

Index hints:

- Use `form.orth` plus the JOIN-resolved concept label as the display
  pair.
- Group by `super_entry_uuid` (word family) and `concept_uuid`.
- Index `definition` as searchable lexical text.

### Word relations

Path: `word-relations/<hex>/<uuid>.yml` · Type: `word-relation`

Parsed from `<div type="word-rel-ref">` records in
`core/word-relations.xml`. Each record names one binary semantic relation
(antonym, converse, hypernym, …) between two word entries. Some refs are
abstract type-level pairs (two word UUIDs, no textual locus); others are
attestations carrying the source-text title, line UUID, line id, textline,
offset, and range. Sibling refs that share a parent `<word-rel>` carry the
same `group_uuid` so the UI can re-cluster attestations under their
abstract pair.

```yaml
uuid: 417a6c62-c1c9-441e-9aa2-e0addaac81d3
type: word-relation
group_uuid: 07e7c251-10dc-5baa-b41d-c4a5a5cf2dc4
rel_type: Conv
rel_type_uuid: a49c5878-7e4e-46ac-817e-87ca64e150c6
rel_label: converse (與 - 受)
left:
  word_uuid: a1a7c269-70b0-4055-b4fa-df9f30e1c6dd
  text: 乞
  concept: BEG
  concept_uuid: 87c1fbcd-a34c-47c5-b855-01de94c5d74c
  attestation:                 # only on attested refs
    text_title: 孟子
    line_uuid: bee23927-358d-4624-b47c-722ed3e5aa8b
    line_id: KR1h0001_tls_001-64a.16
    textline: 必使仰足以事父母
    offset: 6
    range: 1
right:
  word_uuid: 7f71f0e6-36b5-4a51-b10e-17b21462e025
  text: 與予
  concept: GIVE
  concept_uuid: ed1aeb49-abff-4167-a18e-60c0f2202d63
source_references:
- bibliography_uuid: 7bc81713-191b-46bd-9b25-89a9fc359a86
  title: 左傳詞彙研究
source:
  source_file: word-relations.xml
```

Notes:

- `rel_type` is the short relation-type label from the parent
  `<word-rel-type>`'s `<head>` (`Conv`, `Ant`, `Synon`, `Assoc`, …).
- `rel_label` is the prose label from the `<word-rels>` container (often
  a translation/gloss like `converse (與 - 受)`).
- `group_uuid` is the parent `<word-rel>`'s `xml:id` when present; else
  a deterministic UUIDv5 derived from the sorted pair of word UUIDs and
  the `rel_type_uuid`; else the first child ref's UUID.
- Each participating word YAML gains a top-level `word_relations:` list
  pointing back at the relation UUIDs it appears in (rebuilt on every
  import so deletions in source XML propagate).

Index hints:

- Display: render `rel_type` + the two `left.text` / `right.text` plus
  JOIN-resolved word labels.
- Treat `left.word_uuid` / `right.word_uuid` as typed outgoing links.
- Group records by `group_uuid` to fold attestations under the abstract
  pair.

### Senses

Path: `senses/<hex>/<uuid>.yml` · Type: `sense`

Each sense of a word is its own top-level record. Senses back-reference
their parent word; word records list their senses in order via
`sense_uuids`.

```yaml
uuid: 45ddee60-d2a7-4973-9289-b93f0f921ac4
type: sense
word_uuid: d57eebf9-7218-46d5-95bc-4ac4591b81ed
n: '2'
pos: N
syntactic_function_uuids:
- d128d787-1ecb-4c4f-8e89-5dd3edea91d1
semantic_feature_uuids:
- 98e7674b-b362-466f-9568-d0c14470282a
definition: delight (in someone N), joy about (something N)
usages:
- {value: '3', type: warring-states-currency}
source:
  source_file: uuid-703886f9-eb81-4985-b886-f9eb81598567.xml
```

Notes:

- `n` is the attestation count. The frontend uses `n` + the sense `uuid`
  directly to render the attribution toggle; there is no body anchor.
- `pos` lives on the sense, not the word.
- Sense order is given by the parent word's `sense_uuids` list. The
  index materializes this as a `sense_ord` column so queries can return
  senses in declaration order without re-reading the word file.

Index hints:

- Display number = position in parent word's `sense_uuids` + 1.
- Index `definition` as searchable lexical text.
- Treat `syntactic_function_uuids` and `semantic_feature_uuids` as typed
  outgoing links.

## Import commands

All bkk-core data ships in one TLS repository tree (`tls-data/`) under
conventional subdirs and files. The consolidated `core` format imports
every collection in a single invocation:

```bash
bkk import --format core --in <tls-data-root> --out module/output/core --yes
```

It dispatches to each sub-importer with the conventional source path:

| Format | Source under `--in` |
| --- | --- |
| `concepts`            | `concepts/`                       |
| `bibliography`        | `bibliography/`                   |
| `graphs`              | `guangyun/`                       |
| `words`               | `words/`                          |
| `syntactic-functions` | `core/syntactic-functions.xml`    |
| `semantic-features`   | `core/semantic-features.xml`      |
| `rhetorical-devices`  | `core/rhetorical-devices.xml`     |
| `word-relations`      | `core/word-relations.xml`         |

Individual sub-formats still work for incremental re-imports:

```bash
bkk import concepts            --in <tls-data>/concepts                     --out module/output/core --yes
bkk import bibliography        --in <tls-data>/bibliography                 --out module/output/core --yes
bkk import graphs              --in <tls-data>/guangyun                     --out module/output/core --yes
bkk import syntactic-functions --in <tls-data>/core/syntactic-functions.xml --out module/output/core --yes
bkk import semantic-features   --in <tls-data>/core/semantic-features.xml   --out module/output/core --yes
bkk import rhetorical-devices  --in <tls-data>/core/rhetorical-devices.xml  --out module/output/core --yes
bkk import words               --in <tls-data>/words                        --out module/output/core --yes
bkk import word-relations      --in <tls-data>/core/word-relations.xml      --out module/output/core --yes
```

`word-relations` must run **after** `words` because it rewrites the
participating word YAMLs to add their `word_relations:` back-reference
list.

`--text-id` filtering by sub-format:

- concepts, bibliography, graphs: source filename stem or UUID.
- syntactic-functions, semantic-features, rhetorical-devices: UUID or
  code.
- words: super-entry UUID, source filename stem, orthograph, word-entry
  UUID, or concept name.
- word-relations: word-rel-ref UUID, parent group UUID.

`--on-exists skip` leaves existing `.yml` files unchanged.

## Editing model

Records are edited as whole-file YAML through the
`PATCH /core/<collection>/<uuid>` endpoint, which writes via the
GitHub fork-and-PR flow.

Request shape:

```json
{
  "data": { /* full typed record */ },
  "parent_sha": "…",
  "branch": "edit/…",
  "message": "…",
  "extra_files": [
    { "path": "senses/4/45ddee60-….yml", "data": { /* new/changed record */ }, "parent_sha": null }
  ]
}
```

- `data` is the proposed full record. The backend locks `uuid` and `type`
  to the on-disk record (auto-filling them if omitted) but otherwise
  accepts any keys the per-type schema validates.
- `extra_files` carries multi-file edits on the same branch. Set
  `data: null` to delete a file. Typical use: adding a sense (modified
  word file in `data`, new sense file in `extra_files`).
- The response returns the new `commit_sha` plus per-file `extras` with
  updated parent SHAs so the client can stack subsequent commits on the
  same branch.

## Index construction guidance

### Primary keys

Use `(type, uuid)` as the logical primary key. The index file lives at
`<out>/_core.bkki` (SQLite).

### Suggested tables

- `notes`: uuid, type, path, display label, source file.
- `labels`: uuid, type, label, label_type.
- `links`: source_uuid, source_type, target_uuid, target_type, relation.
- `senses`: uuid, word_uuid, sense_ord, n, pos, def_text.
- `frontmatter`: typed per-collection projections of record fields.

The index is a **faithful projection** of the YAML records — there is
no regex reconstruction of prose, because all structured data is already
typed in the records. Prose fields are indexed as text only.

### Link extraction

Walk the bare-UUID relation lists per record type:

- `concept`: `antonyms`, `hypernyms`, `hyponyms`, `see_also`,
  `bibliography[].bibliography_uuid`.
- `syntactic-function`: `taxonomy_parents`.
- `semantic-feature`: `source_references[].bibliography_uuid`.
- `rhetorical-device`: `hypernyms`, `hyponyms`, `antonyms`,
  `source_references[].bibliography_uuid`.
- `super-entry`: `word_uuids`, `forms[].graph_uuids`.
- `word`: `super_entry_uuid`, `concept_uuid`, `form.graph_uuids`,
  `bibliography[].bibliography_uuid`, `sense_uuids`, `word_relations`.
- `sense`: `word_uuid`, `syntactic_function_uuids`,
  `semantic_feature_uuids`.
- `word-relation`: `left.word_uuid`, `right.word_uuid`,
  `left.concept_uuid`, `right.concept_uuid`, `rel_type_uuid`,
  `group_uuid`, `source_references[].bibliography_uuid`.

Wikilinks (`[[X]]`) in prose fields are resolved against the super-entry
orth map at index time.

### Collection / type naming

- Collection names are the plural directory names: `concepts`,
  `graphs`, `rhetorical-devices`, `syntactic-functions`,
  `semantic-features`, `senses`, `super-entries`, `words`,
  `word-relations`.
- `type` values are singular except `super-entry`: `concept`, `graph`,
  `rhetorical-device`, `syntactic-function`, `semantic-feature`,
  `sense`, `word`, `word-relation`.
- `bibliography` is both the collection and type name.
