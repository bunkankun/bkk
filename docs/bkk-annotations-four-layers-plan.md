# BKK Annotation Layer — Architecture Plan

## Context

BKK today has four logical layers but only two address spaces in git:

- **Texts** — per-text GitHub repos (KRP, TLS, CBETA-derived bundles). Rare updates; stable. The ground truth.
- **bkk-core** — single GitHub repo of analytic observations (concepts, words/senses, graphs, syntactic functions, bibliography). Moderate update rate. The vocabulary that annotations point at.
- **Translations** — ~1100 of them, mostly partial, currently living only in an external DB (TLS-derived), anchored at sentence/punctuation-unit granularity. Have no git home today. High activity expected as users add and review.
- **Annotations** — currently *inline* in text bundles as markers (`tls:ann`, etc.), an artifact of the TLS/CBETA import pipeline. Highest update rate, and the planned transport (custom Bluesky records harvested into the backend) is a stream of immutable signed records.

Two problems to fix together:
1. Inline annotations create write amplification on the most stable layer and don't match the Bluesky stream model.
2. Translations have no canonical home at all — they exist only in a derived DB, with no git provenance and no path for community contribution.

This plan settles where both layers belong going forward, without prescribing implementation yet.

## Recommendation: four layers, each in its own address space

Texts remain edition-intrinsic. bkk-core remains the stable analytic vocabulary. Translations and annotations each get their own layer — distinct from each other, because their *shape* and *authoring model* differ even though their activity profiles are both higher than the source layers.

Why translations and annotations are *not* the same layer:

| | Translations | Annotations |
|---|---|---|
| Shape | Long structured prose (paragraphs, sometimes apparatus) | Atomic, discrete observations |
| Authoring | Drafted, revised, often by one translator across many segments | Post-and-forget, many authors, many small additions |
| Coherence unit | The translation-as-work | The individual annotation |
| Natural transport | Git (matches authoring drafts) | Bluesky stream (matches atomic posts) |
| Cardinality | Many translations per source text (translators × languages) | Many annotations per source passage |

They *share* one thing: the anchor scheme into source texts (sentence/punctuation marker IDs). That shared infrastructure is the main cross-cutting investment.

### Layer responsibilities

**Texts (per-text GH repos) — keep only edition-intrinsic markers**
- `page-break`, `line-break`, `head`, `indent`, `punctuation`, `variant`, structural `cbeta:*`
- `voice` markers (attribution/commentary/root) stay — they are part of the edition
- Remove from this layer: `tls:ann` and any future per-passage analytic markers
- See [module/bkk/importer/write/bundle.py](module/bkk/importer/write/bundle.py) for current marker writer; [module/bkk/importer/ir.py](module/bkk/importer/ir.py) for the in-memory shape

**bkk-core (single GH repo) — annotation *targets*, not annotations**
- Concepts, words/senses, graphs, syntactic functions, semantic features, bibliography
- Stable, citable, addressable by UUID
- See [docs/bkk-core/README.md](docs/bkk-core/README.md)
- **Do not** mix per-passage observations in here, even when convenient — keeping it dictionary-shaped is its value

**Translations — new layer, per-translation GH repos**

- One repo per translation: `bkk-tr-<source-text-id>-<translator-slug>` (or UUID-based, mirroring bkk-core's stability discipline)
- Repo contains a translation bundle with:
  - Frontmatter: target language, translator identity (DID or bibliography ref), source text + edition reference, coverage notes
  - Segments anchored to source via stable marker IDs (sentence/punctuation-unit granularity — matches existing TLS-aligned data)
  - Sparse coverage is normal; missing segments are simply absent, not placeholders
- Authoring is parallel to the annotation workflow: The bluesky post contains the translated text segment. 
- Working store: the same backend DB caches translations for the read path, joined to source on `(text_id, edition, anchor_id)`
- Bluesky is used to publish the translation, which can be commented on and will ultimately be harvested into the translation bundles. 
- The existing ~1100 TLS-derived translations are the seed corpus: a one-time extraction from the external DB into per-translation repos, with translator attribution preserved

**Annotations — new dedicated layer, three tiers**

1. **Live source of truth: Bluesky custom records**
   - Append-mostly, DID-signed, federated
   - Each record carries: target anchor, core reference(s), payload, optional supersedes-CID
   - Identity = `did:plc:… + record CID`

2. **Working store: backend DB**
   - Harvested from Bluesky firehose / per-DID polling
   - Indexed by `(text_id, edition, anchor_id)` and by `core_uuid`
   - Filters spam, applies curation state, joins to texts + core at read time for the web frontend
   - Lives alongside the existing serve layer; see [module/bkk/serve/routers/annotations.py](module/bkk/serve/routers/annotations.py) as the integration point

3. **Canonical archive: new GH repo `bkk-annotations`** (chosen)
   - Single repo, one address space, mirrors bkk-core's pattern
   - Periodic snapshot from the DB (cadence TBD at implementation time — likely monthly or release-tagged)
   - Citable, offline-distributable, git-versioned

## Key design decisions

### Anchor scheme: stable marker IDs + offset/length (chosen) — shared by translations and annotations

Both translations and annotations reference texts via existing marker IDs in bundles (e.g. `line-break` id, `punctuation` id) as **stable anchor points**, plus a relative offset and length to identify the actual span. Marker IDs alone are not enough: annotations and translation segments don't necessarily start *at* a marker — they cover a span of text *between* markers or partially overlapping them.

The marker provides the stable reference point; the offset and length identify the textual span relative to that anchor.

Anchor shape (illustrative):
```yaml
anchor:
  text_id: KR1a0001
  edition: CK-KZ            # short id; see Kanripo edition naming
  marker_id: <stable id from bundle>   # anchor point
  offset: <int>             # distance from anchor to start of span
  length: <int>             # length of span
  # optional, for spans crossing the next anchor:
  end_marker_id: <stable id>
  end_length: <int>         # length past end_marker_id
```

`offset` and `length` are measured in **characters** against the canonical normalized form used inside the BKK bundle (the form that BKK normalization already enforces corpus-wide). Specifically: non-standard characters are mapped to PUA codepoints on import and counted as **one character each**, even though some export/display paths re-expand them to multi-character entity references (`&KRnnnn;` and similar). Anchors are always resolved against the canonical PUA form, never against an expanded form. This is the only edge case worth calling out, and it follows the convention BKK already uses internally.

Implication for the text-side pipeline: marker IDs must be **stable across re-imports**. The three source corpora (TLS, KRP, CBETA) already carry markers at sufficiently small intervals to anchor against, but the importer also inserts additional ID-bearing markers in places where no convenient source-side marker exists, so that every annotation/translation segment can attach to a nearby anchor rather than a distant one with a long offset. The combination — inherited source IDs plus importer-inserted IDs — gives full coverage.

The remaining piece: at some point a BKK corpus version must be **declared stable and frozen as the reference going forward**. From that version on, marker IDs are immutable across re-imports, and any future text corrections must preserve the existing anchor-space (insertions get new IDs; existing IDs never move or disappear). Declaring this frozen reference version is the single biggest prerequisite for the translation and annotation layers, and should be the first piece of follow-up work.

### Versioning: immutable + supersede, full chain preserved (chosen)

- Annotations are never edited in place. Corrections create a new record that references the prior CID via `supersedes`.
- The canonical archive preserves the **full chain**, not just the current view. Rejected/superseded records remain, marked with curation state.
- Rationale: maximum auditability; matches atproto's record model; lets the DB derive both a "current" view and a full history without re-fetching from Bluesky.

Curation states (minimum): `proposed`, `accepted`, `rejected`, `superseded`. The DB owns transitions; the archive records them.

### Provenance preservation

Every annotation — in DB and in canonical archive — carries:
- `did` (author)
- `record_cid` (Bluesky record identity)
- `created_at` (record timestamp)
- `supersedes` (prior CID, if any)
- `curation_state` + curator DID + timestamp when curated

This preserves authorship and editorial history through the harvest pipeline.

### Seed corpora

Two migrations, both deferred to implementation phases:

- **Annotations:** Existing `tls:ann` markers in current bundles are the initial seed for the annotation store. Extracted on the next import iteration once the anchor scheme is in place. Treat as legacy-source annotations attributed to TLS, with synthetic CIDs and a fixed origin DID.
- **Translations:** The ~1100 TLS-derived translations in the external DB are the initial seed for the translation layer. Extracted into per-translation repos with translator attribution preserved. Segments inherit their existing punctuation-unit anchors, which align naturally with the chosen anchor scheme.

## Read path

The web frontend joins four sources at render time:

1. Text bundle (per-text repo, served by bkk module)
2. Translation segments (working store, indexed by `(text_id, edition, anchor_id)`, with translator selectable)
3. Annotation working store (DB, indexed by `(text_id, edition, anchor_id)`)
4. bkk-core entries dereferenced by `core_uuid` for annotation payloads and translator bibliography

The canonical annotation archive repo is **not** in the hot read path — it exists for citation, offline use, and disaster recovery. Translation repos *are* effectively in the read path (via DB cache), since they hold the source of truth for translation content.

## Critical files / locations

- [module/bkk/importer/write/bundle.py](module/bkk/importer/write/bundle.py) — current marker writer; the place where `tls:ann` extraction will eventually hook in
- [module/bkk/importer/ir.py](module/bkk/importer/ir.py) — `Marker`, `Annotation`, `Bundle` dataclasses; annotation type will move out of the bundle IR
- [module/bkk/serve/routers/annotations.py](module/bkk/serve/routers/annotations.py) — minimal today; becomes the read-path join point
- [docs/bkk-core/README.md](docs/bkk-core/README.md) — target-of-annotation reference; unchanged by this plan
- [bkk_github_annotation_architecture_plan.md](bkk_github_annotation_architecture_plan.md) — prior planning doc; superseded by this layering decision (its proposal-DB / Issues / PRs flow is replaced by Bluesky → DB → snapshot)
- New: `bkk-annotations` GH repo (does not yet exist; created at implementation time)
- New: `bkk-tr-*` GH repos, one per translation (do not yet exist; populated by extracting the existing TLS-derived translations from the external DB)

## What this plan deliberately does *not* decide

These belong to implementation phases, not the architecture:

- Snapshot cadence and trigger for the annotation archive (manual, scheduled, release-tagged)
- Exact on-disk shape inside `bkk-annotations` (JSONL per text? one file per annotation? sharded by hash like bkk-core?)
- Exact on-disk shape inside `bkk-tr-*` repos (single bundle file? sharded by juan?)
- Translation repo naming convention (slug vs. UUID; how to handle multi-language translations by the same translator)
- Bluesky record lexicon / NSID for annotations and for translation comments
- DB schema specifics
- Spam / rate-limit policy at harvest
- UI affordances for proposing, curating, and drafting translations
- How translation drafts are reviewed (PRs? a separate proposal flow?) — separate from the annotation curation pipeline

## Verification (for this planning deliverable)

This plan is "done" when:

- The four-layer separation (text / core / translations / annotations) is reflected in any new code or doc touching either higher layer — nothing new gets added inline to text bundles for either annotations or translations.
- The next concrete piece of follow-up work is identified: **establishing stable marker IDs in the importer**, which is the shared precondition for both the translation and annotation layers.
- The prior `bkk_github_annotation_architecture_plan.md` is either retired or explicitly marked as superseded so the two don't coexist as contradictory guidance.

No code runs as part of this plan; verification is by review of this document against the user's stated constraints (update frequencies, Bluesky transport, layer responsibilities, the existing 1100-translation corpus).
