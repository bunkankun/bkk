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

Every marker carries the following required fields:

- **type:** the kind of marker.
- **offset:** the codepoint position in the canonical text stream to which the marker applies.

Optional fields are defined per marker type and may include **id**, **content**, **note**, and structured fields specific to the type.

The set of marker types is open. A small **core vocabulary** is defined for layout, structural, and substitution markers — page break, line break, indent, punctuation, paragraph break, register divider, substitution — and projects may extend the set for their own purposes. A marker type that is not in the core vocabulary should be namespaced by the project introducing it.

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

#### What a recipe is silent about

A recipe carries no information about:

- **Output format.** PDF, plain text, EPUB, interactive web view — all are render-time concerns of the consumer. A recipe carries no templates and no formatting instructions.
- **Authored prose.** A recipe is a composition asset, not a document. Discussion, introductions, and editorial commentary belong in their own bundles, which can then be pinned alongside the texts they discuss. The same recipe is reusable across rendering environments; the same prose is re-pinnable by other recipes.
- **Rendering hints.** Intent within a composition is expressed by the role string. A consumer may use the role to choose a rendering, but the recipe does not instruct it.

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
