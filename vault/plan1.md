# BKK Core data format overhaul plan

This note reconsiders the bkk-core storage format in light of the first web
editing work. The current Markdown/frontmatter format is pleasant to browse in
Git, but it makes the interface recover structured data from rendered notes.
Going forward, every core record type needs reliable field-level editing,
fast retrieval, relation navigation, validation, and clean review diffs.

## Recommendation

Keep Git and text files as the auditable source of truth, but make the
canonical files typed YAML records. Markdown should become a generated view,
not the data contract.

In other words:

- YAML is canonical for structured data, relations, labels, prose fields, and
  provenance.
- Markdown is allowed inside prose fields, such as definitions and notes.
- Full rendered Markdown notes may still be generated for human browsing.
- The SQLite index is built from typed records, not by reverse-engineering
  rendered Markdown links and list syntax.

This keeps the useful parts of the current system: local files, Git history,
UUID identity, sharded paths, and readable diffs. It removes the brittle part:
using presentation Markdown as relation storage and parser input.

## Problems in the current format

The current format mixes three jobs in one `.md` file:

- Data storage: frontmatter fields, nested word senses, bibliography metadata.
- Presentation: rendered headings, bullets, relative links, display order.
- Index input: body links, sense definitions, wikilinks, and section text.

That creates several maintenance problems:

- Important data is body-only for words, senses, syntactic functions, semantic
  features, and many concept sections.
- The index has to parse Markdown structure to recover links and definitions.
- Some relations exist in YAML, some only in body links, and some in both.
- The web editor can only offer a generic frontmatter/body editor instead of
  proper relation pickers and field controls.
- Adding schema fields is awkward because the current edit path preserves and
  validates the old frontmatter shape.

The biggest pressure point is `sense`. Senses are already stable annotation
targets with UUIDs, but their editable definition is embedded in a numbered
Markdown list while their grammar metadata is nested in word frontmatter.

## Target repository layout

Proposed canonical layout:

```text
core/
  records/
    bibliography/<hex>/<uuid>.yaml
    concepts/<hex>/<uuid>.yaml
    graphs/<hex>/<uuid>.yaml
    semantic-features/<hex>/<uuid>.yaml
    super-entries/<hex>/<uuid>.yaml
    syntactic-functions/<hex>/<uuid>.yaml
    words/<hex>/<uuid>.yaml
  generated/
    markdown/
      ...
  schemas/
    bibliography.schema.json
    concept.schema.json
    graph.schema.json
    semantic-feature.schema.json
    super-entry.schema.json
    syntactic-function.schema.json
    word.schema.json
  _core.bkki
```

`records/` is the source of truth. `generated/markdown/` is optional and
rebuildable. It can be committed if human GitHub browsing is important, or
ignored if the web app becomes the primary reader.

Paths remain sharded by UUID for large collections. UUIDs remain prefixless in
canonical files.

## Core design rules

1. UUIDs are canonical identities; paths are storage addresses.
2. Relations are typed UUID references, never inferred from Markdown links.
3. Prose is stored in named fields, usually as Markdown strings.
4. Generated Markdown may contain relative links, but those links are not
   authoritative.
5. Each record has one canonical YAML document.
6. Nested entities can be logical first-class records in the index even when
   physically stored inside a parent file.
7. The web edit API accepts typed records or patches, validates them, and
   serializes YAML.

## Common record envelope

Every canonical record should share a small envelope:

```yaml
schema_version: 2
uuid: 3eb2c600-e234-4c6b-bb79-40e8eff9ee14
type: concept
labels:
  display: ABLE
source:
  source_file: concepts.xml
provenance:
  imported_at:
  imported_from:
```

The exact `provenance` fields can grow later. The important point is to keep
identity, display labels, source metadata, and type information predictable
across all collections.

## References and relations

Use compact typed references:

```yaml
concept:
  uuid: 1c7bf322-c905-41e0-9145-7d4b01da86a1
  label: DELIGHT
```

For multi-edge relation lists:

```yaml
relations:
  - type: antonymy
    target_type: concept
    target:
      uuid: deb3cd81-03bc-4c7c-9125-a2a8837202c9
      label: UNABLE
  - type: bibliography
    target_type: bibliography
    target:
      uuid: 60d39cc0-d76b-4275-8490-886ace4204be
      label: BUCK 1988
    scope: "1008"
    scope_unit: page
```

Rules:

- `uuid` is authoritative.
- `label` is a denormalized convenience for review diffs and offline reading.
- Validators should warn when labels drift from target display labels.
- Relation `type` uses stable machine names, not rendered section headings.

## Word and sense model

Words stay physically grouped as word records because the editor needs the
whole lexical entry in context. Senses become logical first-class entities in
the index and API.

Illustrative word record:

```yaml
schema_version: 2
uuid: d57eebf9-7218-46d5-95bc-4ac4591b81ed
type: word
labels:
  display: "喜: DELIGHT"
  orth: 喜
  concept: DELIGHT
super_entry:
  uuid: 703886f9-eb81-4985-b886-f9eb81598567
  label: 喜
concept:
  uuid: 1c7bf322-c905-41e0-9145-7d4b01da86a1
  label: DELIGHT
n: "74"
form:
  orth: 喜
  graph:
    uuid: c4711853-e554-4934-bdf2-97e5b33fbc53
    label: 喜
  pronunciations:
    - lang: zh-Latn-x-pinyin
      value: xǐ
    - lang: zh-x-oc
      value: qhɯʔ
    - lang: zh-x-mc
      value: hɨ
definition:
  markdown: "Xǐ 喜 ... is openly manifested delight..."
bibliography:
  - target:
      uuid: 2389c812-8053-4187-8f7a-19f6e856050f
      label: FOGUANG
    title: 佛光大辭典 Fóguāng dàcídiǎn The Foguang Dictionary of Buddhism
    scope: 4899b
    scope_unit: page
senses:
  - uuid: 45ddee60-d2a7-4973-9289-b93f0f921ac4
    body_number: 1
    n: "2"
    pos: N
    syntactic_functions:
      - uuid: d128d787-1ecb-4c4f-8e89-5dd3edea91d1
        label: nab.t
    semantic_features:
      - uuid: 98e7674b-b362-466f-9568-d0c14470282a
        label: psych
    definition:
      markdown: "delight (in someone N), joy about (something N)"
    usages:
      - type: warring-states-currency
        value: "3"
```

Index/API implications:

- `sense.uuid` gets its own `senses` row.
- Annotation targets should point to `sense.uuid`.
- The API can expose `GET /core/senses/{uuid}` even though storage lives in
  the parent word file.
- Sense editing can be implemented as a patch to the parent word YAML.

## Concepts

Concepts should stop storing ontology and bibliography only in rendered
Markdown sections. A concept record should have structured labels, prose
sections, and relations:

```yaml
schema_version: 2
uuid: 3eb2c600-e234-4c6b-bb79-40e8eff9ee14
type: concept
labels:
  display: ABLE
  alternate:
    - CAPABLE OF
  zh: 能夠
  och: 能
definition:
  markdown: "..."
notes:
  - type: old-chinese-criteria
    title: Old Chinese Criteria
    markdown: "néng [[能]] ..."
relations:
  - type: antonymy
    target_type: concept
    target:
      uuid: deb3cd81-03bc-4c7c-9125-a2a8837202c9
      label: UNABLE
bibliography:
  - target:
      uuid: 60d39cc0-d76b-4275-8490-886ace4204be
      label: BUCK 1988
    scope: "1008"
    scope_unit: page
```

`[[能]]` can remain as lightweight prose markup, but the index should treat it
as optional text markup, not as the only relation source.

## Atomic table-like records

The brainstorming note identifies graphs, syntactic functions, and semantic
features as "atomic data nuggets." That is a useful category.

These records should be optimized for table editing:

- `graph`: mostly structured fields plus a small notes/provenance area.
- `syntactic-function`: code, description, notes, hierarchy relations.
- `semantic-feature`: code, description, notes, hierarchy/source relations.

Descriptions and notes should move from body-only Markdown into typed fields:

```yaml
schema_version: 2
uuid: e81e5db1-7207-4450-a18d-27a597c5fd67
type: syntactic-function
code: npro.adNab
labels:
  display: npro.adNab
description:
  markdown: "pronoun preceding and modifying an abstract nominal"
notes:
  markdown: "Most abstract nominals ..."
relations:
  - type: taxonymy
    target_type: syntactic-function
    target:
      uuid: 8694d163-4347-4386-b028-e99017c8995b
      label: npro.adNPab{S}
```

## Composite and hierarchy records

The model should explicitly support:

- Concepts as composite records with labels, prose, ontology, bibliography,
  and generated word backlinks.
- Rhetorical devices as future composite records.
- Word relations as either typed relations between words/senses or their own
  records if they need provenance and commentary.
- Hierarchies as relation edges, not special body sections.

Hierarchy rule:

```yaml
relations:
  - type: broader
    target_type: concept
    target: { uuid: "...", label: "..." }
  - type: narrower
    target_type: concept
    target: { uuid: "...", label: "..." }
```

The index can materialize transitive hierarchy tables later if needed, but the
canonical source should stay as explicit edges.

## Index design

The `.bkki` file should become a typed retrieval layer. Suggested tables:

- `records`: uuid, type, collection, path, display_label, source_file,
  content_hash.
- `labels`: uuid, label, label_type, label_search.
- `relations`: source_uuid, source_type, target_uuid, target_type, relation,
  ord, metadata_json.
- `prose`: owner_uuid, owner_type, field, markdown, plain_text.
- `senses`: uuid, word_uuid, body_number, pos, n, def_plain.
- `word_forms`: word_uuid, orth, graph_uuid.
- `pronunciations`: owner_uuid, owner_type, lang, value, resp.
- `bibliography_refs`: source_uuid, target_uuid, scope, scope_unit, title.
- FTS5 tables over labels, prose, sense definitions, bibliography titles, and
  graph fields.

The index builder should parse YAML into typed objects, validate references,
then populate projections. It should not inspect generated Markdown for
relations.

## Web editing model

The web editor should move from raw frontmatter/body editing to typed editing.

Proposed API flow:

1. `GET /core/{collection}/{uuid}/edit-model`
   returns typed JSON plus schema/editor hints.
2. The UI renders collection-specific controls:
   relation picker, bibliography picker, prose field, sense table, hierarchy
   editor, pronunciation list, etc.
3. `PATCH /core/{collection}/{uuid}`
   accepts typed JSON or JSON Patch.
4. The backend validates with Pydantic or JSON Schema.
5. The backend serializes canonical YAML and commits it to the user's fork.
6. The backend returns preview data and generated Markdown.
7. The user opens a PR as today.

This keeps the current GitHub fork/branch/PR workflow but changes the edited
payload from "frontmatter plus raw body" to "typed record."

## Generated Markdown

Generated Markdown still has value:

- GitHub browsing.
- Human review of record presentation.
- Export to static documents.
- Local grep-friendly inspection.

But generated Markdown should be treated like a build artifact. It should be
deterministic and reproducible from YAML.

Possible generated path:

```text
generated/markdown/words/d/d57eebf9-7218-46d5-95bc-4ac4591b81ed.md
```

Generated notes can include relative links, headings, and pretty rendering.
The index should ignore them.

## Validation

Minimum validators:

- UUID shape and uniqueness.
- Path shard matches UUID.
- `type` matches collection.
- Required display label exists.
- Relation target exists.
- Relation target type matches declared type.
- Sense UUIDs are unique globally.
- Word sense order is stable and `body_number` is consistent.
- Denormalized labels on references either match current target labels or
  produce warnings.
- Generated Markdown is up to date if generated files are committed.

These validators should run in CI for bkk-core PRs and locally through a CLI.

## Migration plan

1. Freeze the v2 schema vocabulary.
2. Implement typed Python models for every current core type.
3. Build a v1 reader that converts existing Markdown/frontmatter records into
   typed v2 objects.
4. Write the v2 YAML serializer.
5. Write a deterministic Markdown renderer from v2 objects.
6. Rebuild the SQLite index from v2 objects only.
7. Add validators and CI checks.
8. Add typed read APIs while keeping current read APIs temporarily.
9. Replace the web editor with typed edit models.
10. Migrate bkk-core repository files from `.md` to canonical `.yaml`.
11. Keep a legacy importer for one transition window.
12. Retire Markdown parsing from the index builder.

## Open decisions

- Commit generated Markdown or generate it only locally/on demand?
- Use JSON Schema, Pydantic JSON schema, or both for web editor hints?
- Keep all senses nested under words, or allow optional physical
  `senses/<hex>/<uuid>.yaml` files later?
- How strict should denormalized reference labels be: warnings only, or
  auto-updated by formatter?
- Should hierarchy edges use TLS names such as `hypernymy/taxonymy`, generic
  names such as `broader/narrower`, or both with normalized aliases?

## Strong design position

Do not preserve "body versus frontmatter" as a long-term design principle.
That split was useful when the primary artifact was a readable note. The new
primary artifact is a maintained knowledge base with a web editor.

The replacement principle is:

> Everything canonical is typed. Prose is still prose, but it lives in named
> fields. Presentation is generated.
