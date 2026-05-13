# Bunkankun

## Design objectives for the Bunkankun project

There are three parts of the project:

- A vision for the shape of data for the texts and other digital objects accessible to the project.
- A definition for middleware that acts as a bridge between the data and the interfaces.
- Clients that provide access to the texts for the users.

All of these have their own additional parts and requirements. There are also additional aspects, such as the fact that deliverables of all three of these parts will be made available with a CC-BY-SA license; any contributions are expected to use the same license.

## A tale of two editions

Texts might be present in multiple editions.  As in the Kanripo, the BKK texts can be declaring itself to be either **documentary** or **interpretative**. 

### Documentary editions

These are digital artifacts that try to emulate as faithful as possible the textual features manifested in a specific edition, which it 'documents'. There might still be deviations, due to the imperfections of the digital medium or the path thereto.  In the Kanripo, this type of edition is clearly marked with the shorthand that refers to it, which may only contain uppercase letters, such as WYG, SBCK (hyphens, underscores etc. are allowed).

### Interpretative editions

Editorial judgements are interpretations that manifests itself as emendations to the received text. The purpose here is not to produce a faithful copy, but a better text, at least in the eye of the editor. We mark them with shorthand codes that contain only lowercase letters, such as `master`.  The 'master' edition plays a special role here, because that is the only one that has to be present for all texts, since it serves as the default entry point for the text and in the default view other editions might be seen through the filter of differences to this edition.  

## Shape of the data: The BKK Bundle

We want a data format that can be validated and audited and fits well with current distributed infrastructure for the distribution of digital data. Its coherence should be verifiable and its changes traceable.

We strive for a format that holds the basic artifacts in a verifiable and modular manner and can be easily shared.

In addition to the **archival format**, there is a **recipe format** that composes one or more bundles into a useful assembly. By analogy, a recipe is to bundles what a `docker-compose` file is to container images: it does not produce content of its own; it enumerates components and pins each by canonical identifier and hash so that the assembly is reproducible. A recipe may, for example, bring together a particular edition of a base text, a translation, and a glossary, so that a consumer can fetch and verify the whole composition from a single entry point. Output format and rendering are concerns of the consumer, not the recipe.

### Archival format

The archival format is mainly for text data of premodern Chinese. Texts used to be transmitted in scrolls, *juan* in Chinese; this remains a useful subdivision and is still in use today. In our format, each juan is a separate file. A **manifest** plus a **table of contents** and additional metadata pertain to the whole text and point into the juan files. All `text` fields, no matter their location or type, are accompanied by a `hash` field whose value audits the content.

A **juan** file has a `front`, a `body`, and a `back`; only the body must be non-empty, the others are optional and need not be present if empty. Additional metadata fields are available. The text elements of the body and back may be subdivided where appropriate. A typical front contains an opening line that locates the juan in a larger collection, the title of the text, the sequential number of the juan, and an attribution naming persons and roles with respect to the body. The back contains a closing line. The placement of prefaces, postfaces, colophons, and similar paratextual material is open: such material may go into the body or be separated out into front or back, at the discretion of the project applying the format.

The body has one text element that holds the canonical character content of the whole juan. Space characters, punctuation, line breaks, and similar content are not present in this stream — they are extracted into a **markers** object that follows the text element. A marker has at minimum a **type** and an **offset**; further fields are optional and typically include **id**, **content**, and additional structured information appropriate to the marker's type. The set of marker types is open; a small core vocabulary is defined separately.

One text can be represented in several editions, they can be made accessible through the 'master' edition of the text or a recipe can adress them directly. 

### Canonicalization

The text element of any field is a sequence of characters drawn from a defined **canonical character set**. Source material is brought into this canonical form through a deterministic procedure; every step that alters the source is recorded as a marker, so that the source can be reconstructed exactly from the canonical text and its markers.

The procedure is applied in order:

1. **Source.** Input is treated as Unicode encoded in UTF-8.
2. **Entity expansion.** Entity references in the source — for example `&KRxxxx;` style references in Kanripo material, or TEI `<g/>` elements — that point to characters not available in current Unicode are expanded to codepoints in the Supplementary Private Use Area (SPUA-A, U+F0000–U+FFFFD) using the bundle's declared **entity encoding** (see Entity encodings, under Reference assets). The expansion is deterministic and produces no markers: the PUA codepoint, taken together with the declared encoding, is sufficient to recover the entity reference. The PUA codepoints produced this way are first-class members of the canonical text stream and participate in offsets, the text hash, and downstream marker logic like any other character.
3. **Unicode normalization.** NFC is applied so that base characters are in precomposed form.
4. **Extraction of layout features.** Characters carrying layout, structural, or paratextual information — whitespace, punctuation, indent characters, page and line breaks, register dividers, and similar — are removed from the text element and emitted as markers, each with an offset into the remaining text stream.
5. **Substitution to canonical characters.** Any character in the post-extraction stream that is not a member of the canonical character set is replaced by its canonical equivalent. Each such substitution is emitted as a marker recording the offset, the character or sequence replaced, and the reason for the substitution.
6. **Hash.** The resulting text stream, encoded as UTF-8, is the input to the **hash** field that accompanies the text element.

**Offsets** count Unicode codepoints into the post-substitution text stream. Variation selectors and similar combining characters are not independent offset targets; they remain attached to the preceding base character.

**Reversibility.** Because every layout feature and every substitution is recorded as a marker, and because entity expansion is reversible through the declared entity encoding, an exact reconstruction of the source can be produced by applying the markers to the canonical text in reverse order and then de-expanding any PUA codepoints assigned by the encoding. The hash audits the canonical form; the markers and the entity encoding together carry the residue that distinguishes one transcription of a source from another.

**Canonicalization is not editorial work.** Variant characters (異体字) that are themselves members of the canonical character set are preserved as written. Replacing a variant with a standard form, or a standard form with a variant, is an editorial decision, not a canonicalization step, and produces a different hash. The canonicalization procedure substitutes only characters that fall outside the canonical set.

**Entity expansion is not substitution.** A substitution replaces a source character with a different character that is itself a member of the canonical character set, and records the replacement as a marker so the source remains reconstructible. Entity expansion produces a PUA codepoint that is a stand-in for a character with no Unicode equivalent — there is no canonical Unicode character to substitute *to*. The PUA codepoint is therefore a member of the canonical text stream in its own right, and reversibility is guaranteed by the entity encoding rather than by markers. Source PUA characters that *do* have a defined canonical-set replacement continue to be handled by `pua-resolution` substitutions and their associated mappings.

### Reference assets

Several elements of the archival format depend on data that does not belong inside any single juan file but must be referenced from juan files in a stable, auditable way: the **canonical character set** that defines what characters a text element may contain, the **substitution mappings** that drive systematic replacements during canonicalization, the **entity encodings** that assign PUA codepoints to characters absent from current Unicode, and the **witness tables** that resolve citation keys to descriptions of consulted sources.

These are collectively termed **reference assets**. A reference asset is an addressable bundle asset in its own right, with a canonical identifier, a hash, and a version. Reference assets are immutable per version: any correction or extension produces a new version with a new identifier, leaving juan files canonicalized against the older version unaffected. A bundle that relies on one or more reference assets declares them in its manifest, which pins each by identifier and hash. The same reference asset can be shared across many bundles; this is, in fact, the expected case for the canonical character set.

The four kinds of reference asset currently defined are described in the following subsections. The category is open: future kinds of reference asset can be introduced under the same pattern.

#### The canonical character set

The canonical character set is a finite collection of Unicode characters admitted as legal content in a text element after canonicalization. Its purpose is twofold: it bounds what a downstream consumer must be prepared to render, and it makes hashes meaningful — two text streams canonicalized against the same set whose hashes match are guaranteed to contain the same characters in the same order.

The set is defined in a separately distributed document with its own canonical identifier and hash, and is **versioned**. Each version is immutable; additions, removals, or rule changes produce a new version with a new identifier. A manifest declares the exact version against which its juan files were canonicalized, e.g. `canonical_set: bkk-cjk-v1`. The declaration lives in the manifest rather than in each juan file: a bundle is canonicalized as a whole, against a single set version.

The contents of a version are defined by **inclusion rules** rather than by exhaustive enumeration, because Unicode itself evolves. A typical version will include:

- the CJK Unified Ideographs block and a stated list of CJK Extension blocks;
- variation selectors used with members of the included blocks (treated as attached to a base character, not as independent characters);
- a small number of non-ideographic characters required for legitimate textual content, such as specific repetition marks.

Characters that fall outside the declared set are not forbidden in a source — they are replaced during canonicalization and the replacement is recorded as a substitution marker. Common reasons for exclusion include compatibility characters with a documented preferred form, deprecated codepoints, ad-hoc Private Use Area assignments from third-party encoding schemes, and blocks deliberately not yet supported.

When a bundle declares an **entity encoding**, the PUA codepoints assigned by that encoding are, for the purposes of this rule, treated as members of the canonical character set. They are not "outside" the set and do not produce substitution markers; they are part of the canonical text stream by virtue of the entity-encoding declaration in the manifest. See the section on entity encodings, below.

Successor versions of a canonical set are expected to be **additive**: `bkk-cjk-v2` should be a superset of `bkk-cjk-v1`, except where Unicode itself reclassifies characters. A juan canonicalized against an earlier version is therefore re-canonicalizable against a later one without information loss; re-canonicalization produces a new juan file with new hashes, while the original is preserved unchanged.

#### Substitution mappings

A substitution mapping is a table that defines how a class of source characters is replaced during canonicalization. It is the data behind reasons such as `pua-resolution` and `ids-collapse`: the canonicalization procedure consults the mapping to determine the replacement, and the resulting substitution marker pins both the mapping and the entry within it.

Mappings are distributed through the same substrate-agnostic mechanism as juan files. A bundle that relies on one or more mappings declares them in its manifest.

A mapping has a defined **scope** — typically a Unicode block, a coherent third-party encoding (e.g., a CHISE-derived PUA table), or a recognized class of expressions (e.g., IDS sequences for a defined glyph set). Within that scope each entry carries:

- a stable **entry identifier** within the mapping;
- the **source** character or sequence as it appears after Unicode normalization;
- the **canonical replacement**, valid against a stated canonical character set version;
- an optional **note** documenting the basis for the equivalence.

A substitution marker that draws on a mapping carries both the mapping's canonical identifier with hash (pinning the version) and the entry identifier within the mapping. This guarantees that the meaning of an individual substitution does not drift even if a later version of the mapping revises the replacement.

A mapping version may declare which canonical character set versions it is valid against, since a mapping that produces replacements in `bkk-cjk-v1` may need to be revised, or extended, before it is valid against `bkk-cjk-v2`.

Substitution mappings handle source PUA characters that have a defined replacement within the canonical character set. They do not handle PUA codepoints produced by entity expansion: those are governed by an **entity encoding** (described in the next subsection) and are members of the canonical text stream rather than substituted-away characters.

#### Entity encodings

An **entity encoding** is a reference asset that assigns Supplementary Private Use Area (SPUA-A, U+F0000–U+FFFFD) codepoints to characters that have no representation in current Unicode but appear in source material as entity references — for example `&KRxxxx;` style references in Kanripo source files, or TEI `<g/>` elements. The encoding is consulted during the **entity expansion** step of canonicalization; the resulting PUA codepoints are first-class members of the canonical text stream.

An entity encoding is structurally parallel to a substitution mapping: distributed through the same substrate-agnostic mechanism, addressable, hashed, and versioned per the reference-asset pattern. Within an encoding each entry carries:

- the **PUA codepoint** assigned to the entity. This is the primary key within the encoding.
- one or more **source references** that resolve to this codepoint — typically a list of `(namespace, identifier)` pairs, where the namespace identifies the source format (e.g., `kr` for `&KRxxxx;` references, `tei-g` for TEI `<g/>` elements) and the identifier is the entity reference within that namespace. Multiple source references may map to the same codepoint when several source formats describe the same glyph.
- an optional **external_reference** field pointing to a richer description of the character — typically an entry in a project-maintained or third-party glyph database, where descriptive material such as IDS expressions, glyph images, and rendering hints is held. The encoding deliberately keeps internal metadata slim; rich descriptions are expected to live in the external resource.

The formula by which a particular middleware assigns a PUA codepoint to a given entity reference at import time is **not** part of the format. Different importers, working with different source materials, may legitimately assign different codepoints to the same entity. Once an encoding is declared in a bundle's manifest, however, its assignments are fixed for that bundle: every PUA codepoint produced by entity expansion in any juan of the bundle resolves through that encoding.

A new entity reference encountered during conversion is added in a successor version of the encoding, which is then declared by any bundle that needs the new assignment. Successor versions of an entity encoding are expected to be **additive** in the same sense as the canonical character set: existing assignments are preserved, new ones are appended.

##### Receiver contract

A receiver that encounters PUA codepoints in a bundle whose manifest declares an entity encoding should interpret those codepoints through the declared encoding. Resolution is via the middleware, which fetches the encoding by canonical identifier and hash and exposes lookups from PUA codepoint to source references and external reference.

A receiver may then choose any rendering strategy appropriate to its environment: a glyph from a PUA-aware font (such as HanaMin or a project-specific font), a fetched glyph image referenced from the external description, an IDS-rendered composite, or a typographic placeholder annotated with the entity name. A receiver that does not consult the encoding must at minimum signal to the user that unresolved PUA characters are present, rather than rendering them silently as missing-glyph boxes.

#### Witness tables

A **witness** is a source consulted in the production of a juan file — typically when an editorial decision (a substitution, a correction, a reading choice) is recorded in a marker. Witness references make those decisions traceable.

A witness reference is a string identifying the source. Two forms are recognized:

- a **canonical identifier** of another addressable asset, paired with its hash. This is the strongest form, used when the witness is itself a bundle asset — another juan, another edition's manifest, a translation, a scanned page image referenced via IIIF, or a comparable resolvable resource.
- a **citation key** resolving through a witness table, used when the witness is a source the project does not (yet) hold as an addressable asset — a printed edition, a manuscript with a shelfmark, a scholar's communication.

A witness table is a reference asset that maps citation keys to fuller descriptions. Each entry carries at minimum a stable key, a human-readable description, and — where applicable — a canonical identifier and hash of the source if the source is itself an addressable asset. A juan that uses citation-key witnesses declares the witness table version in its manifest.

A marker that names a witness does not include the witness's content. The reference is enough: a consumer can resolve it through the middleware if the witness is addressable, or treat it as documentation if it is not. Witness references are never free-form opaque strings; if a project genuinely needs to record a witness it cannot identify more precisely, this is recorded in a `note` field on the marker, not in the witness reference itself.

### Markers

Markers are the mechanism by which information that has been removed from the text stream during canonicalization, or that annotates the text stream from outside it, is reattached to specific positions in the canonical text.

Markers come in two shapes. **Point markers** apply at a single position; they carry an `offset` alone. **Range markers** apply to a slice of the text stream; they carry an additional `length` field, and apply to the slice `[offset, offset + length)`. The shape is fixed by the marker type. The `length` field mirrors the codepoint-slice form already used in the recipe-selection vocabulary (§"Selection").

Every marker carries the following required fields:

- **type:** the kind of marker.
- **offset:** the codepoint position in the canonical text stream where the marker begins.
- **length:** the codepoint span of the marker. Present on range-typed markers, absent on point-typed.

Optional fields are defined per marker type and may include **id**, **content**, **note**, **responds-to** (an id referring to another marker, used by markers that comment on or extend another marker), and structured fields specific to the type. A marker carrying an `id` may be referenced from elsewhere; ids are local to the juan unless qualified.

The set of marker types is open. A small **core vocabulary** is defined for layout, structural, voicing, and substitution markers: page break, line break, indent, punctuation, paragraph break, register divider, head, comment, and substitution are point-typed; voice (§"Voices") is range-typed. Projects may extend the set for their own purposes; a marker type that is not in the core vocabulary should be namespaced by the project introducing it.

#### Substitution markers

A substitution marker records that a character or character sequence in the source was replaced during canonicalization with a member of the canonical character set. It is the mechanism by which the canonical text remains reversible to its source.

Every substitution marker carries the required marker fields together with:

- **type:** the literal value `substitution`.
- **original:** the character or sequence that was replaced, as it appeared in the source after Unicode normalization.
- **replacement:** the character or sequence now occupying the offset.
- **reason:** the cause of the substitution, drawn from a defined enumeration.

The **reason** field is constrained to a small fixed enumeration:

- `not-in-canonical-set` — the source character is not a member of the declared set; the replacement is the set's defined preferred form.
- `compatibility-decomposition` — the source character is a Unicode compatibility character; the replacement is the preferred form of its canonical decomposition.
- `pua-resolution` — the source character is in a Private Use Area and has been resolved via a named project mapping.
- `ids-collapse` — the source contained an Ideographic Description Sequence; the replacement is a single character identified as the intended glyph.
- `scribal-variant-collapsed` — the source character is a recognized scribal variant of the replacement and the project's editorial policy collapses it.
- `ocr-correction` — the source character is the output of an OCR process and has been corrected against another witness or by editorial judgment.
- `extension` — a slot for project-defined reasons, requiring an additional **extension** field that names the project and the reason within that project's namespace.

A substitution marker may carry optional fields including **id**, **note**, **witness**, and **mapping** (a reference, with hash, to the named substitution table that produced the replacement, used for `pua-resolution` and `ids-collapse`).

The reason enumeration is intended to be stable: new categorical reasons that generalize beyond a single project should accumulate in this list rather than be hidden inside the `extension` slot.

#### Voices

A **voice** marker records that a slice of the canonical text stream belongs to a named textual layer. It is the mechanism by which interleaved threads — a root text and its commentaries, for example — are disentangled so that a consumer can extract any one layer, present several in parallel, or reproduce the interleaved source as published.

A voice marker is range-typed. It carries the required marker fields together with:

- **type:** the literal value `voice`.
- **length:** the slice length, in codepoints.
- **name:** the textual layer this slice belongs to.

The **name** vocabulary is open. A small recognized set is defined so that cross-project work stays legible:

- `root` — the primary, commented-upon text.
- `commentary` — a layer of comment on a root text.
- `subcommentary` — a layer of comment on a commentary.
- `gloss` — a short interlinear annotation (typical layout: between the lines, in smaller characters).
- `note` — a bracketed span whose specific role (commentary, gloss, alternate reading, citation, …) is left unresolved. Used when the only available signal is a punctuation fence such as `(`…`)` and the deriver can't claim anything stronger.

Projects extend by prefixing — `kr:apparatus`, `tls:swl`, and so on — consistent with the rest of the marker vocabulary.

A voice marker may carry optional fields including **id**, **note**, and **responds-to** — the id of another voice marker that this voice answers to. A `subcommentary` voice typically `responds-to` a `commentary` voice; a `commentary` voice `responds-to` a `root` voice. The relationship is many-to-one: a single root span may attract several commentary spans, each of which names that root span as its `responds-to` target.

Voices coexist with the single text stream of the juan; there is no separate per-voice text element. The juan's canonical text contains every voice's content in source order, and voice markers carve it into named slices. A consumer extracting "only the root" filters by `name=root` and reads the slices in offset order; one reproducing the interleaved form walks all voice markers in offset order. Both presentations are derivations of the same canonical text and the same hash.

The lighter-weight point markers `comment` and `head` (which carry out-of-stream `content`) remain available for sparse interlinear material — short editor's notes, occasional headings — where promoting the inserted text into a first-class slice of the text stream would be overkill. Voices are the right tool when the inner layer is dense enough that it needs to be canonicalized, hashed, and marker-able in its own right.

**Example.** A short commentary passage alternates root lines and commentary on those lines. The canonical text of the juan, rendered as a single stream, reads:

```
色不異空謂色相虛幻不離空性空不異色謂空性遍周不離色相
```

The juan carries four voice markers:

```yaml
markers:
  - {type: voice, offset: 0,  length: 4, name: root,       id: r1}
  - {type: voice, offset: 4,  length: 9, name: commentary, responds-to: r1}
  - {type: voice, offset: 13, length: 4, name: root,       id: r2}
  - {type: voice, offset: 17, length: 9, name: commentary, responds-to: r2}
```

A consumer extracting `name=root` walks the marker list in offset order and concatenates the slices `[0,4)` and `[13,17)`, yielding `色不異空空不異色`. A consumer reproducing the interleaved source walks every voice marker in offset order and emits the slices in sequence, producing the original stream. Both presentations are derivations of the same canonical text and the same hash; neither presentation requires altering the underlying juan.

### Manifest

The **manifest** is the entry point for a bundle. It identifies the bundle as a whole, lists the assets it contains, and declares the reference assets against which those assets were canonicalized. A consumer who is given a manifest, together with the means to resolve canonical identifiers, has everything required to verify and use the bundle.

A manifest carries the following **required** fields:

- **canonical_identifier:** the bundle's stable, location-independent identifier.
- **canonical_location:** an indication of where the bundle is normatively published. Other locations may serve copies, but this is the location of record.
- **canonical_set:** the identifier and hash of the canonical character set version against which the bundle's juan files were canonicalized.
- **juan:** an ordered list of the included juan files, each entry carrying the juan's filename within the bundle, its sequence number, and its hash.
- **hash:** a hash that covers the manifest as a whole, including the hashes of all listed juan files and reference assets.

A manifest carries the following **optional** fields, used when applicable:

- **mappings:** substitution mappings used during canonicalization, each with its canonical identifier and hash.
- **entity_encoding:** the entity encoding used during canonicalization, with its canonical identifier and hash. Required if any juan in the bundle contains entity-derived PUA codepoints; omitted otherwise.
- **witness_tables:** witness tables used by markers in the bundle, each with its canonical identifier and hash.
- **table_of_contents:** a structured listing that points into the juan files, used for navigation.
- **metadata:** descriptive metadata pertaining to the whole text — title, attributions, dates, relationships to other works. These fields are not structural and are not used for resolution or verification, but they *are* read by catalogs to support browsing (see §"Catalog browsing"). A project's consistency in field naming and shape across its manifests directly determines how useful catalog queries against its bundles will be.
- **other_assets:** assets that are part of the bundle but are neither juan files nor reference assets — scanned page images, alignment data, supplementary apparatus. Each entry carries an identifier, a type, and a hash.

The manifest is the only place in a bundle where the bundle is named as a whole. Juan files identify themselves by position and content; reference assets identify themselves by their own identifiers. The manifest binds them.

A manifest is itself addressable and may be referenced from other bundles — most notably from recipe files, which pin a specific manifest by identifier and hash in order to compose content drawn from it.

### Overlay bundles

An **overlay bundle** is a bundle that contributes additional markers to another bundle's text stream without modifying it. Its manifest declares a **target** — the canonical identifier and hash of a target bundle — and its juan files carry marker collections that address offsets in the corresponding juans of the target. The target is unaffected: its hash does not change, and existing pins to it remain valid. The overlay is independently addressable, hashed, and versioned per the usual bundle pattern.

The motivation is straightforward. Useful markers can be authored after a bundle has been published, by parties who do not own the target, for purposes its original author may not have considered: segmentation introduced for the sake of a translation, modern punctuation added to a classical source, voice markers added to disentangle a layout the source did not mark machine-readably, scholarly annotation distributed independently. A consumer composes the target with whichever overlays they want via a recipe.

#### Overlay manifest

An overlay manifest is a regular bundle manifest with one additional required field:

- **target:** a sub-mapping carrying the target bundle's `canonical_identifier` and `hash`.

The other manifest fields keep their usual meanings, with two natural specializations:

- **juan:** the overlay's juan files. Each entry names a file in the overlay and the sequence number of the **target** juan it pertains to. An overlay need not cover every juan of the target; uncovered juans simply contribute no overlay markers.
- **canonical_set:** the overlay's own declaration, against which any marker `content` text in the overlay is canonicalized. Typically the same version the target was canonicalized against; a different version is allowed but should be noted.

An overlay's juan file has the same shape as a regular juan file but its **body text is the empty string** by construction: the overlay carries no text of its own. The body still has a hash (over the empty string); the substance is the `markers` collection, whose offsets address into the target juan's text stream. This is the one relaxation of the "body must be non-empty" rule given in §"Archival format".

#### Use cases

- **Segmentation overlay** — adds seg markers (`tls:seg` or equivalent) to a source that has no segmentation. Translation work against unsegmented sources is the primary motivation (see §"Translations").
- **Voicing overlay** — adds `voice` markers to a source whose typesetting interleaves root and commentary without explicit machine-readable threading.
- **Punctuation overlay** — adds modern punctuation as an opt-in layer over an unpunctuated classical source.
- **Annotation overlay** — adds notes, glosses, or apparatus from a specific scholar, distributed independently of the source.

#### Composition and merge

A recipe pinning a target plus one or more overlays is fulfilled by the middleware as follows: each pinned bundle is resolved and verified; the overlays' markers are merged into the target's per-juan marker collections; the merged result is returned to the consumer. **No marker is dropped** — the middleware preserves every marker from every pinned bundle. Within a merged collection, markers are ordered by the rules already defined in §"Hash and integrity model" (sort by `offset`, then by `priority` default zero, then by stable comparison on `type`).

Conflicting *semantics* — two overlays asserting overlapping `voice.name` ranges, for example, or two segmentation overlays whose seg boundaries disagree — are surfaced to the consumer as overlapping or contradictory markers; the renderer decides whether to display both, prefer one, or warn the user. The format does not arbitrate. Archival fidelity favours preservation; rendering policy is a consumer concern, consistent with the recipe format's silence about output (§"What a recipe is silent about").

A target bundle's hash is independent of any overlays that exist for it. An overlay's hash covers the overlay alone. Recipe hashes cover the pinned set, and so transitively cover the composition.

#### Worked examples

**Translation against an unsegmented source.** A KRP source `bkk:krp/KR6c0101/v1` carries page-breaks and line-breaks but no seg markers. A segmentation overlay `bkk:overlay/KR6c0101-segs-acme/v1` declares the target and contributes seg markers per juan; its juan 1 file is essentially empty body plus a marker collection:

```yaml
target:
  canonical_identifier: bkk:krp/KR6c0101/v1
  hash: sha256:1af8…
canonical_set: bkk:charset/cjk-v1
seq: 1
body: ""
markers:
  - {type: 'tls:seg', offset: 0,   id: 001-1a.1}
  - {type: 'tls:seg', offset: 47,  id: 001-1a.2}
  - {type: 'tls:seg', offset: 132, id: 001-1a.3}
  # …
```

A translation `bkk:translation/KR6c0101-en-smith/v1` references those seg ids directly in its spans:

```markdown
[The discourse opens with…]{corresp=001-1a.1}
[The first exposition turns on…]{corresp=001-1a.2}
```

The composing recipe pins all three:

```yaml
pins:
  - role: base
    canonical_identifier: bkk:krp/KR6c0101/v1
    hash: sha256:1af8…
  - role: overlay
    canonical_identifier: bkk:overlay/KR6c0101-segs-acme/v1
    hash: sha256:2c91…
  - role: translation
    canonical_identifier: bkk:translation/KR6c0101-en-smith/v1
    hash: sha256:4f2e…
```

When fulfilled, the middleware merges the overlay's seg markers into the target's per-juan marker collection; the translation's `corresp` values resolve against those merged ids exactly as they would against natively-authored segs. The source bundle's hash and existing pins are unaffected.

**Post-publication annotation overlay.** A bundle `bkk:krp/KR6q0053/v1` has been published for years. A scholar later authors a set of glosses and short apparatus notes against it and distributes them as `bkk:overlay/KR6q0053-glosses-tanaka/v1`. The overlay's manifest pins the target by hash; each juan file in the overlay carries `comment` point markers and `gloss`-voice range markers at the relevant offsets, with empty body text. Readers compose the two via a recipe pinning the source and the overlay. The scholar can publish revisions as new overlay versions, each with its own hash; a reader pinning a specific overlay version sees exactly the annotations as the scholar published them at that moment. The source's hash, identifier, and existing pins remain valid through every revision of the overlay.

### Recipe format

Where the archival format describes a coherent text as published — a single bundle, internally bound by its manifest — the **recipe format** describes a *composition* of one or more bundles. By analogy, a recipe is to bundles what a `docker-compose` file is to container images: it enumerates components and pins each by canonical identifier and hash, but produces no content of its own.

Typical uses include a teacher's reading list pinning a specific edition together with a translation and a glossary; a scholar's citation set capturing the exact versions of texts consulted during the writing of an article; a derivative edition pinning a primary text plus a critical apparatus drawn from several witnesses; or a personal reading state pinning a base text plus the reader's own annotation overlay.

A recipe is itself an addressable, hashed, versioned asset, with a canonical identifier of its own. A recipe may pin other recipes, allowing one composition to extend or recompose another.

#### What a recipe pins

A recipe pins **manifests** — and optionally other recipes — never juan files or reference assets directly. The manifest is a bundle's entry point and its hash boundary; pinning a juan file in isolation would bypass the canonical-set declaration and the reference-asset bindings that give the juan its meaning. Reference assets are likewise resolved through the manifests that declare them, not pinned independently.

Each pin carries:

- a **role** within this recipe — for example `base`, `translation`, `commentary`, `glossary`. The vocabulary of roles is open and is interpreted by the consumer; recipes do not prescribe rendering.
- the pinned manifest's (or recipe's) **canonical identifier** and **hash**. The hash is required in a published recipe and pins the version exactly; in a request recipe submitted to the middleware, the hash may be omitted to mean "current version" (see "Recipe as request" below).
- an optional **selection** narrowing the pin to a part of the bundle (see below).
- an optional **note** documenting why the asset is part of the composition.

#### Selection

A pin without a selection is the entire bundle. A pin with a selection narrows the scope; four forms are recognized:

- `{ juan: <seq> }` — one whole juan.
- `{ juan: <seq>, from: <marker-id>, to: <marker-id> }` — bounded by named markers, typically page-side markers such as `002-3a` … `002-7b`. This is expected to be the common form, since premodern texts are conventionally cited by page-and-side and these markers are stable across re-canonicalization.
- `{ juan: <seq>, offset: <n>, length: <m> }` — explicit codepoint slice, used when no marker boundary fits.
- `{ toc: <key> }` — resolves through the pinned manifest's `table_of_contents`.

Selections are evaluated against the pinned manifest only; they do not cross pins.

#### Alignment between pins

A recipe does **not** define an alignment language between its pins. If a translation in one pin is aligned to a base text in another pin, the alignment data lives in the translation bundle — as markers in the translation that reference offsets in the base text. The recipe merely declares that both bundles are part of the composition; alignment is the responsibility of whichever asset has the alignment data.

This keeps the recipe format minimal and pushes alignment into the asset best placed to carry it. The same recipe can therefore be re-rendered with different alignment-aware tooling without any change to the recipe itself.

#### Composition recipes and render recipes

Recipes have two valid levels. A **composition recipe** pins inputs and, optionally, selections. It is the minimal form used for fetching, citation, and reproducible assembly. A **render recipe** extends a composition recipe with named datasets and a template that tells a client how to stitch the resolved material into an output document.

This keeps the fetch boundary explicit while allowing common scholarly and maintenance workflows — reading lists, inspection reports, teaching handouts, formatted quotation sheets — to be described in the same reproducible object that names their sources.

In a render recipe, pins may carry a **name** in addition to their role. The name is local to the recipe and is used by datasets and templates. The role remains the semantic place of the asset in the composition; the name is the handle by which the recipe refers to that pin.

A render recipe may declare **datasets**. A dataset is extracted from one named pin after the pin has been resolved, verified, and selected. The initial dataset vocabulary is deliberately small: a recipe may collect markers from a pin, filter them by marker fields, and optionally include the text covered by range markers together with surrounding context.

Rendering is expressed with a constrained Jinja-style template. The template receives only the resolved pins, declared datasets, non-fatal errors, and the resolved recipe. It does not receive filesystem, shell, import, or host-language access. Output formats are an open vocabulary, but Markdown is the first defined target.

Example:

```yaml
kind: bkk.recipe/v1
pins:
  - name: text
    role: base
    textid: KR3a0001
    selection:
      juan: 1

datasets:
  voices:
    from: text
    collect: markers
    where:
      type: voice
    include_text: true
    context: 12

render:
  format: markdown
  template: |
    # Voices in {{ pins.text.label }}

    {% for v in datasets.voices %}
    ## {{ v.name }} {{ v.id }}

    - location: {{ v.textid }} {{ v.juan_seq }}/{{ v.bucket }} @{{ v.offset }}+{{ v.length }}
    - responds to: {{ v.responds_to or "—" }}

    `{{ v.left }}【{{ v.text }}】{{ v.right }}`

    {% endfor %}
```

The dataset and rendering vocabularies are intentionally client-facing. They do not alter the pinned assets, their hashes, or the semantics of marker data; they describe a derived presentation of resolved content.

#### Recipe as request

The recipe format serves a second use beyond publication: it is also the format a client uses to **request** content from the middleware. A client constructs a recipe describing the assembly it needs, submits the recipe to the middleware, and receives the assembled components back. There is no separate request format; an ad-hoc recipe written for a single fetch has the same shape as a recipe published as a shareable asset.

The middleware fulfils a recipe by walking each pin, resolving the canonical identifier through its configured resolvers, fetching the asset, verifying its hash, applying any selection, and returning the assembled set. Unfulfillable pins — missing assets, hash mismatches, unresolvable identifiers — are reported as errors; the middleware does not silently substitute.

A request recipe and an asset recipe differ only in their lifecycle: a request recipe is typically constructed ad hoc, used once, and discarded; an asset recipe is published with a canonical identifier of its own and may be cited and pinned by others. The middleware processes both identically.

A pin in a request recipe may omit its **hash**. An omitted hash means "give me the current version of this identifier"; a present hash pins the version exactly. When the middleware fulfils a request recipe, it resolves any unhashed pins to current versions and returns the **resolved recipe** alongside the assembled content. The client can discard it, save it as a reproducible record, or pass it on. A resolved recipe is itself a valid recipe; submitting it back to the middleware fetches the same content again, exactly.

Recipes also flow in the opposite direction. The middleware may **return** recipes to a client as the result of a query — for example, a search response in which each match is described as a recipe the client can then submit to fetch the underlying content. The recipe is in this way the unit of description that travels in both directions between client and middleware: the client expresses what it wants as a recipe, and the middleware describes what it has in recipes too.

#### Recipes for citation

A recipe is also a vehicle for **scholarly citation**. A scholar writing about a text typically consults specific versions: a particular edition of the base text, a particular translation, particular reference assets. Recording those choices as a recipe captures the exact state of the consulted material — every component pinned by canonical identifier and hash — so that a reader of the resulting article can later resolve the recipe and see precisely what the author saw.

This matters because the substrate evolves. By the time an article is read, the texts cited may have been re-canonicalized, corrected, or re-released; their current hashes will differ from what the author worked with. A recipe pinned at the time of writing preserves the exact versions regardless of what is current at the time of reading. The middleware resolves the pinned hashes against the substrate's archived state; the citation remains stable.

Used this way, the same recipe can drive multiple parts of a scholarly workflow:

- **Quotation insertion.** An author quoting a passage identifies it by `(pin role, juan, marker-id range)` within their working recipe; tooling fetches the exact characters from the pinned version, both at writing time and again at typesetting or publication time, with a guarantee that the content has not drifted.
- **Bibliography generation.** Each pin in the recipe yields one entry in the bibliography, carrying the canonical identifier, the pinned hash, and a human-readable description drawn from the pinned manifest's metadata.
- **Replication.** A reader following up on the article submits the same recipe to a middleware to retrieve byte-for-byte the same materials the author worked with — not "approximately the same edition" but the same content.

A recipe used this way is normally published as an asset in its own right, with its own canonical identifier, alongside the article that depends on it. The article cites the recipe; the recipe pins everything else. A reader has one entry point into the article's full bibliographic apparatus.

#### Recipe and manifest compared

The two top-level asset kinds play complementary roles:

- A **manifest** is *bundle-internal binding*: it declares that a set of juan files and reference assets constitute a coherent text as published.
- A **recipe** is *bundle-external composition*: it declares that a set of manifests (and possibly other recipes) constitute a useful assembly.

Both are addressable, hashed, and versioned. A consumer that is given a recipe, together with the means to resolve canonical identifiers, has everything required to verify and assemble the composition.

### Hash and integrity model

Every text field, every juan file, every reference asset, every manifest, and every recipe carries a hash. Together these form a directed acyclic graph of content-addressed objects: a manifest's hash transitively covers every component of the bundle, and a recipe's hash transitively covers every manifest and recipe it pins.

**Algorithm.** All hashes are SHA-256, encoded as a lowercase hexadecimal string.

**Hash inputs.** Hashes are taken over a deterministic byte sequence, defined per kind of object:

- A **text field hash** is taken over the UTF-8 byte sequence of its post-canonicalization text stream.
- A **juan hash** is taken over the canonical serialization of the juan file as a whole, including its front, body, back, all text fields (with their own hashes), all marker collections, and its metadata.
- A **reference asset hash** is taken over the canonical serialization of the asset.
- A **manifest hash** is taken over the canonical serialization of the manifest, which by construction includes the hashes of every juan file and every referenced reference asset.
- A **recipe hash** is taken over the canonical serialization of the recipe, which by construction includes the hashes of every pinned manifest and every pinned recipe.

**Canonical serialization.** Files in the format are stored in a human-readable form (typically YAML), but hashes are computed over a defined canonical serialization, not over the storage form. The canonical serialization is RFC 8785 JSON Canonicalization Scheme (JCS): the data structure is converted to canonical JSON, and the UTF-8 bytes of that JSON are the hash input. This decouples the hash from incidental formatting choices in the storage form.

**Marker ordering.** Within a marker collection, markers are sorted by `offset` ascending; ties are broken first by a `priority` field (lower first, default zero) and then by a stable comparison on `type`. Two semantically identical marker collections produced by different tools therefore canonicalize to the same byte sequence.

**Bundle identity.** The manifest hash is the bundle's content-level identity. Two bundles whose manifest hashes match are byte-equivalent at every level; two bundles that share a manifest hash but diverge anywhere in their components cannot exist.

**Verification.** A consumer verifies a bundle by re-hashing the canonical serialization of the manifest and checking it against an expected value, then iterating over each referenced hash and verifying that the fetched object hashes to the declared value. Verification proceeds top-down, fails fast on any mismatch, and requires no trust in the substrate from which assets were retrieved.

**Integrity of markers.** Markers are not separately hashed; they are covered transitively by the juan hash, which hashes the juan as a whole. Because text and markers are sibling elements within the body, both are bound by the same juan hash; substituting one without the other is detectable.

**Sharing.** Reference assets are referenced by hash, not by copy. Two bundles that use the same canonical character set version cite the same hash; the asset is fetched and verified once. This is what makes the structure a DAG rather than a tree.

## Middleware

All requests for texts are routed through the middleware, a software library that provides access to texts in an abstracted form that does not need to care about where the texts are physically located.

The middleware accepts canonical identifiers and resolves them through configured resolvers (local cache, Git remote, HTTP, IPFS, or other substrates). Once an asset is fetched, the middleware verifies it against its declared hash before returning it to the caller. Trust comes from the hash, not from the source.

Beyond resolving individual identifiers, the middleware also fulfils **recipes**. A client constructs a recipe describing the composition it needs and submits it to the middleware; the middleware walks each pin, resolves and verifies each pinned asset, applies any selection, and returns the assembled set. This is the standard way for a client to obtain a multi-component composition from the project's repositories. The recipe a client submits as a request and the recipe a teacher publishes as a reading list share the same format; what differs is their lifecycle, not their shape.

### bunkanlib

`bunkanlib` is the reference implementation of the middleware. The first form is a **Python library** that clients import in-process; wire-protocol shapes (local daemon, remote service) are deferred and may be added later as the project's needs become clearer.

A guiding principle: bunkanlib is **thin**. Its job is to fetch and verify; the bundle (or the assembled set, for a recipe) is then handed to the client, which is expected to interpret, slice, format, and render it. The library does not aim to be a one-stop reading stack.

#### Resolvers

Resolution — turning a canonical identifier into a verified asset — happens through a chain of pluggable **resolvers**. Each resolver knows how to fetch from one substrate. The anticipated set:

- **local cache** — content-addressed store on disk; populated by every successful fetch. Usually first in the chain.
- **Git remote** — a repository following a documented layout for manifests, juan files, and reference assets.
- **HTTP(S)** — base-URL-rooted fetches against a documented layout.
- **IPFS** — fetches by content hash, natively compatible with the project's addressing model.

Resolvers are tried in configured order. A resolver returning an asset that fails verification is logged and skipped; the next is tried. The first resolver returning a verifying asset wins.

#### Fulfilling a recipe

For each pin in a recipe, bunkanlib:

1. Resolves the pinned canonical identifier.
2. If the pin declares a hash, verifies against it; otherwise records the resolved hash for inclusion in the response.
3. Recursively fetches and verifies any assets the pinned object itself declares — a manifest's reference assets, or a pinned recipe's own pins.
4. Applies the selection, if the pin specifies one. With no selection, the whole bundle (or the whole pinned recipe) is returned to the client unsliced; selection is opt-in, consistent with the thin-library principle. The client may also ask for a slice explicitly through the API, independent of what the recipe says.
5. Tags the result with the pin's role and adds it to the assembled response.

The response carries the per-pin assembled content plus, where relevant, a resolved recipe with all hashes filled in.

#### Search and discovery

A client that does not yet know a canonical identifier finds content by **search** rather than by direct resolution. Three mechanisms are anticipated; all three layer on top of bunkanlib rather than living inside it, since none is required for resolution itself.

- **Catalog browsing.** Aggregation of manifest metadata, browseable by criteria. Specified in §"Catalog browsing" below.
- **Full-text search.** An indexed search service accepts queries over indexed text and metadata and returns matches as **recipes**. The client submits any of those recipes back to bunkanlib to fetch the underlying content. This is the "recipes flow back from middleware" pattern described in §"Recipe as request".
- **Out-of-band discovery.** New resources not yet in any known repository may surface through external channels — for instance, a `#bunkankun` hashtag on GitHub or a similar convention — by which authors announce a freshly published bundle and clients pick up its canonical identifier from the announcement. This is a social rather than a technical mechanism; bunkanlib's job is to resolve whatever identifier the client provides, regardless of how the client learned of it.

#### Catalog browsing

A **catalog** is an aggregation of manifests, browseable by metadata. Catalog browsing is the primary discovery mechanism for clients that do not yet know a canonical identifier, and is the first such mechanism that bunkanlib will support.

Architectural commitments:

- **Manifests are the ground truth.** Every fact a catalog exposes about a bundle is sourced from that bundle's manifest. The catalog does not originate metadata; it aggregates what manifests already declare. A catalog can go stale, but it cannot drift into a separate truth.
- **Static or dynamic delivery.** A static catalog is a pre-aggregated snapshot, published as an addressable BKK asset and refreshed periodically. A dynamic catalog is computed on request from a known set of manifests. A client need not care which it is getting; the response shape is the same.
- **Indexable metadata is the manifest's metadata block.** No separate schema. The fields in use within this project follow the convention demonstrated in [`sample/manifest.yaml`](sample/manifest.yaml): `title`, `alt_titles`, `krp_id`, `krp_category`, `authors[]` (each with `name`, `role`, `dates`), `edition` and `base_edition` (each with `name`, `short`), `composition_period`, and `source`. The format does not require any particular field; consistency across a project's manifests is what makes browsing useful, and that consistency is the project's responsibility, not the format's.
- **Results are recipes.** Per the recipe-as-result pattern, a browse response is a recipe whose pins are the matched manifests, with role `match`. The client iterates the pins and submits any of them back to bunkanlib to fetch the underlying content.

The query API itself — operators, composition — is deferred. The starting form is field-equality filters over the indexable metadata (e.g., "all bundles with `krp_category: KR1a`", "all bundles whose `authors[].name` includes `朱震`"); richer expressions can layer on top once the basic shape is in use.

#### Out of scope

Authoring — producing bundles from source materials, computing canonical hashes, assembling manifests — is a separate tooling concern and is not part of bunkanlib. The write-side workflow will be discussed and specified in its own pass.

## Clients

*To be drafted.*

## Translations

A translation is, in BKK terms, a bundle in its own right. It has a canonical identifier, a hash, a manifest, and is composable with other bundles through recipes. It is *not* an annotation on a source bundle and does not live inside one. What links a translation to its source is a recipe pin — `role: base` for the source, `role: translation` for the translation — together with per-segment references inside the translation that point back into the source by marker id.

A translation must be readable on its own. A reader who picks up only the translation bundle, without ever resolving the source, should encounter a complete, coherent text in the target language. References to source segments are present so that source and translation can be presented in parallel when both are available, but they are not load-bearing for reading the translation in isolation. This is a deliberate departure from the current TLS-style translation format, where segment-by-segment alignment dictates the reading order and the translation is unreadable without the source alongside.

### Archival format

The archival format is **Markdown with a YAML header**. The header carries the manifest-style metadata that ties the translation to its source and identifies it as an addressable bundle. The body is the translation's prose, in the order a reader is meant to read it, with source-segment alignment recorded inline via Pandoc-style attribute spans.

#### YAML header

The header is delimited by `---` lines at the start of the file and carries at least:

- **canonical_identifier:** the bundle's stable identifier, as for any BKK bundle.
- **canonical_location:** the normative publication location.
- **source:** a sub-mapping pinning the translated source by `canonical_identifier` and `hash`. A translation pins exactly one source bundle; multi-source compositions are expressed through recipes, not by stacking pins inside a translation.
- **language:** the target language as a BCP-47 tag (`fr`, `en`, `de-1996`, `zh-Hant`).
- **title:** the translation's title in the target language.
- **responsibility:** an ordered list of `{role, name}` entries — translator, editor, reviser, annotator. Roles draw on a small recognized vocabulary and may be extended where needed.
- **license:** the licence under which the translation is distributed. Required, since translations carry their own copyright independently of the source.
- **hash:** the bundle hash, computed as described in §"Canonicalization", below.

Optional header fields, used where applicable, include `original_title`, `publication` (publisher, year, place — when the translation has a prior print existence), `date` (the date of this version of the translation), `note`, `table_of_contents` (target-language headings that index into the body), and `juan` (analogous to the source-bundle juan list when the translation is split across multiple files).

Because the target language is rarely CJK, **`canonical_set`** does not apply. Unicode normalization to NFC is still performed; the canonical-character-set machinery is otherwise inert for translation bundles. A future translation into a CJK target language may re-introduce a canonical set declaration; the slot is reserved.

#### Body

The body is ordinary Markdown. Headings, paragraphs, lists, footnotes, and inline emphasis all work as elsewhere. The translator is free to introduce headings, section breaks, and prefatory material that have no counterpart in the source — this is what makes the translation readable on its own.

Source alignment is recorded with **Pandoc-style attribute spans**. A span surrounding a piece of translated prose declares which source segment(s) it corresponds to:

```markdown
[Le Maître a dit :]{corresp=002-1a.3}
[Qui gouverne le peuple par l'exemple de sa vertu]{corresp=002-1a.4}
[est comme l'étoile polaire.]{corresp=002-1a.5}
```

The `corresp` value is a **source marker id**, given in its relative form — the source `text-id` and edition are pinned by the bundle's `source` header field, so `002-1a.3` is unambiguous. Where multiple source segments collapse into one translation segment, `corresp` is space-separated:

```markdown
[The Master's combined remark.]{corresp="002-1a.3 002-1a.4 002-1a.5"}
```

Where a single source segment is split across multiple translation segments, the same `corresp` is repeated on each:

```markdown
[The first half of the remark,]{corresp=002-1a.3}
[and the second half.]{corresp=002-1a.3}
```

A span may carry additional attributes alongside `corresp` — `resp` (the responsible editor for that segment), `modified` (an ISO 8601 timestamp), and free-form `note` strings — all in the same braces:

```markdown
[Le Maître a dit :]{corresp=002-1a.3 resp=CH modified=2024-07-20T16:46:45.958+09:00}
```

When the source bundle has **no seg markers** — many KRP sources carry only page-breaks and line-breaks — the recommended path is to pin a **segmentation overlay** (§"Overlay bundles") for that source. The translation's composing recipe pins the source, the overlay, and the translation; the overlay contributes seg ids that the translation references in `corresp`, exactly as it would for a natively-segmented source. The translation file does not need to know that the seg ids come from an overlay rather than the target — `corresp` syntax is unchanged.

Where authoring a segmentation overlay is not worth the effort, two coarser shapes are available, drawn from the same selection vocabulary the recipe format already uses (§"Selection"):

- **Marker-bounded ranges** — `corresp-from=<marker-id> corresp-to=<marker-id>` pins the translation span to the slice of source text bounded by two named markers, typically page-breaks. Suitable for citation-style translation where page-side granularity is the working unit.
- **Codepoint slices** — `corresp-juan=<seq> corresp-offset=<n> corresp-length=<m>` pins to an explicit slice of a source juan. The escape hatch for cases where no marker boundary fits.

All three shapes — seg id, marker-bounded range, codepoint slice — may coexist within a single translation, span by span.

Prose outside any span is **untethered content** — translator's preface, section headings, footnotes, bridging text, anything the translator authored without a source-side anchor. Untethered content is first-class translation material; it is preserved through canonicalization and carries no `corresp`. Markdown footnotes are the natural carrier for translator's notes; they may themselves contain spans if the note quotes a passage that is aligned.

A translation need not be exhaustive: source segments with no covering span are simply unmapped. A reader-side tool may present these as gaps when both bundles are available; in standalone reading they are invisible.

#### File structure

A short translation may live in a single Markdown file. A longer one mirrors the source's juan structure: one Markdown file per juan, listed in the manifest's `juan` field. The translation is free to choose its own file split — a translation may, for instance, organize itself by translator-chosen chapters that do not align to source juans — but the conventional choice of one-file-per-source-juan keeps parallel rendering uncomplicated.

### Canonicalization and hashing

The Markdown form is the **storage form**. Hashing operates over a **canonical form** derived by parsing: spans are extracted into an ordered list of segments, each carrying its `corresp`, attributes, and text content; untethered prose is preserved as text-only segments without `corresp`; the YAML header is parsed and re-emitted in canonical key order. The resulting structure is serialized using RFC 8785 JSON Canonicalization Scheme (JCS), and the SHA-256 of those bytes is the bundle hash. This is the same separation of storage form from canonical serialization used by juan files (§"Hash and integrity model").

Two consequences:

- Reformatting the Markdown — line breaks, whitespace inside spans, ordering of attributes within a brace — does not change the hash.
- Reordering segments in the reading order *does* change the hash. The reading order is part of the translation's identity.

### Composition via recipes

A translation is composed with its source through a recipe with two pins:

```yaml
pins:
  - role: base
    canonical_identifier: bkk:krp/KR1h0004/v1
    hash: sha256:…
  - role: translation
    canonical_identifier: bkk:translation/KR1h0004-fr-levi/v1
    hash: sha256:…
```

A client that fulfils this recipe receives both bundles and can render them in parallel by walking the translation's spans and resolving each `corresp` against the base. The alignment data lives entirely in the translation; the recipe carries no alignment language of its own, consistent with §"Alignment between pins".

A translation bundle may be pinned standalone — without a `base` companion — for readers who only want the translation. The translation has all the metadata required to be presented on its own.

### Migration from the TLS translation format

Existing translations in the TLS toolchain (see `samples/translations/`) carry a `<seg corresp="…">` for each source segment in source order. They typically omit translator-authored structure — headings, paragraph breaks, prefatory material — because the source provided that structure visually and the reader was assumed to have it available. They are not, in their current form, readable in isolation.

Migration is therefore not a mechanical reshape. A conversion tool can:

- Extract each TEI `<seg>` into a Pandoc attribute span with `corresp` carrying the marker id (stripping any leading `#`).
- Carry over `resp`, `modified`, and `xml:lang` as span attributes.
- Lift the `<teiHeader>` into the YAML header — `<titleStmt>` to `title`/`responsibility`, `<publicationStmt>` to `publication`/`license`, `<sourceDesc>` to `source`.
- Detect empty segs (`<seg .../>` with no content) and drop them from the output rather than emit empty spans.

What the tool *cannot* do automatically, and what a human pass must do after, is:

- Introduce paragraph and section breaks where the source's page-side structure was implicitly carrying them.
- Add translator's headings, chapter titles, and any prefatory material needed for the translation to stand on its own.
- Restore translator's footnotes that were inlined into segment content (visible in the French sample, where a footnote's full text is concatenated with the translation of the segment it annotates) and split them out into proper Markdown footnotes.
- Reorder where the source-driven sequence produced an unreadable target-language sequence, leaving the `corresp` values intact.

A migrated bundle is therefore expected to go through a **standalone-reading review** before it is hashed and published. The hash of a migrated translation reflects the corrected form, not the verbatim TLS export. Earlier verbatim exports may be retained as separate bundle versions if a project wishes to preserve them for provenance, but they are not the canonical archival representation.

### Purpose-built editor

The Markdown-plus-spans format is editable in any UTF-8 text editor and renderable through any Pandoc-aware tool (Quarto, RStudio, VS Code with the Quarto extension). For routine prose work that is sufficient. What generic Markdown tooling does *not* surface — and what a purpose-built editor should — is alignment-aware authoring.

The editor's responsibilities, listed in roughly increasing order of project specificity:

- **Source resolution through bunkanlib.** The editor reads the bundle header, resolves the `source` pin through the middleware, verifies the source's hash, and displays the source text alongside the translation.
- **Parallel view.** A two-pane layout — source on one side, translation on the other — with the two synchronized by `corresp`. Selecting a segment in either pane highlights its counterpart in the other.
- **Alignment status overlay.** Visual indicators for each source segment: translated, untranslated, multiply translated (more than one span carries this `corresp`), and for each translation span: aligned, untethered, dangling (`corresp` does not resolve in the source).
- **Span management.** Selecting a stretch of translated prose and pressing a key wraps it in a span; selecting a source segment and clicking "assign" sets the `corresp` of the currently active translation span. Attribute editing is form-driven rather than by hand-typing braces.
- **Overlay awareness.** When the recipe pins a segmentation overlay (§"Overlay bundles") alongside the source, the editor surfaces the overlay's seg markers as the default granularity for `corresp` assignment. When no segmentation overlay is available, the editor falls back to marker-bounded ranges — typically page-breaks or line-breaks — and offers an explicit codepoint-slice option for arbitrary character boundaries. The choice of granularity is per-translation, not global: a translator may pin a segmentation overlay for one source and use coarser fallbacks for another.
- **Source-side navigation aids.** Per-character dictionary lookup, lookup of recognized phrases, marker-id-aware search, jump to next/previous segment, jump to a referenced page-side.
- **Validation.** On save and on demand: every `corresp` resolves in the source; every span is well-formed; the YAML header validates; `responsibility` and `license` are present; the bundle re-hashes consistently.
- **History per segment.** The `modified` and `resp` attributes are maintained automatically — every edit to a span's text updates both. A diff view per segment supports review by an editor who is not the translator.
- **Round-trip stability.** The editor never silently rewrites Markdown. Files written by the editor and files written by hand round-trip through the canonicalizer to the same hash.
- **Reference-asset awareness.** PUA codepoints in the source are rendered through the source bundle's declared entity encoding (§"Entity encodings"), so the translator sees the intended glyph rather than a missing-glyph box.
- **Migration assistance.** A mode that loads a TLS-format translation, runs the conversion described above, and presents the result for the standalone-reading review pass — flagging segments with no surrounding paragraph structure, empty segs, inlined footnotes, and other artefacts that need a human decision.

The editor is a client of bunkanlib; it does not have its own resolution model. It writes the storage-form Markdown; the canonicalizer that produces the hash is shared with the rest of the toolchain.

A first-cut editor that delivers parallel view, alignment status, and span management is enough to begin authoring. The remaining items can be added incrementally without breaking the format.

## References

Projects and documents that have informed the design and may guide further work:

- Distributed Text Services — https://dtsapi.org/specifications/
- Text Encoding Initiative
- IIIF
- CTS / CITE (Homer Multitext)
- CHISE character database
- Docker — https://en.wikipedia.org/wiki/Docker_%28software%29 (for the manifest-and-layers analogy)
- IPFS / content addressing (for the substrate-agnostic identity model)
- RFC 8785 — JSON Canonicalization Scheme
