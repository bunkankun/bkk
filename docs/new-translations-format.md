# CTF-aligned translation authoring

This note describes a proposed new authoring format for BKK translations. The
format is intended to live side by side with the current Markdown/span
translation bundles and may become the preferred format for new translations
once the import, validation, and editor workflow are in place.

The central change is that a translation is authored as a readable text in its
own structure. Alignment to the source is declared at the level of Citation Tree
Fragments (CTF), usually on headings or other citable units, instead of forcing
the translated prose into the source's segment order.

## Motivation

The existing TLS-derived translation format is useful for machine alignment but
awkward for translators. It treats each translation as a dependent list of
source-side segments, so the translated text often loses its natural headings,
paragraphs, notes, and reading order.

CTF gives us a better primary alignment unit. If a translation heading
corresponds to a source CTF node, a reader or editor can place the source and
translation next to each other at the level where both texts naturally have
structure. Finer alignment can then be inferred or supplied below that level
without making it the storage format's organizing principle.

This is deliberately a high-level alignment model:

- a translated chapter, section, poem, or paragraph may correspond to one source
  CTF node;
- the translation may introduce headings, notes, or divisions that have no
  source counterpart;
- line-by-line verse alignment and paragraph/sentence prose alignment are
  authoring and editor conventions at first, not mandatory machine-readable
  data in v1.

## Relationship to existing translation bundles

The existing Markdown/span format remains valid. It records alignment directly
on translated spans with `corresp` values that usually point to source segment
markers. The format described here is a new authoring layer.

For the first implementation, Org files should be compiled into the same
translation-bundle world that BKK already understands:

- the translation remains an independent bundle with its own metadata, hash,
  license, and responsibility statements;
- the source bundle is still pinned by canonical identifier and hash;
- recipes still compose a source and translation;
- consumers that only understand the old translation index can continue to work
  from importer output.

The important distinction is that the Org source preserves the translator's
intended reading structure, while the compiled representation can expose the
alignment data needed by search and parallel display.

## Org v1 syntax

Org mode is the first concrete authoring format. Other authoring formats can be
added later if they can compile to the same internal model.

A translation unit is an Org heading. When the heading corresponds to a source
CTF node, it carries a `source_ctf` property:

```org
** AUF DEN GLIMMERBELEGTEN WANDSCHIRM EINES FREUNDES
:PROPERTIES:
:source_ctf: KR4c0022/13/14
:END:

Der Glimmer-Wandschirm in deinem Haus,
Aufgestellt hin zum verwilderten Hof,
Läßt eine Berglandschaft uns sehn,
Ganz ohne bunt bemalt zu sein.
#+BEGIN_NOTE
Dieses Gedicht schrieb Wang Wei im Alter von fünfzehn Jahren; es ist das
früheste der von ihm erhaltenen Gedichte. Es läßt schon einige der
Eigenschaften erkennen, die für den späteren großen Dichter und Maler
charakteristisch waren: seine Vorliebe für dıe »einfachen Dinge« des Lebens,
sein Gespür für die poetische Dimension des Alltäglıchen, vor allem aber auch
den für das Spiel von Licht und Schatten geschärften Blick des Malers.
#+END_NOTE
```

The `source_ctf` value is an authoring reference to a source CTF node. It should
use the citation path form, for example `KR4c0022/13/14`, not the offset-bearing
form such as `KR4c0022/13/14/@4219+97`. Offsets are an implementation detail of
the source CTF asset and are the wrong dimension for translation authoring.

During import or validation, the path reference must resolve to exactly one
concrete CTF node in the pinned source and CTF set. The resolved node may have a
span internally, but that span is not copied into the human-authored property.

Org headings without `source_ctf` are allowed. They represent untethered
translation material: introductions, translator's headings, prefaces, appendices,
or editorial bridges.

## Alignment semantics

`source_ctf` declares that the translation unit corresponds to the named source
CTF node at the same conceptual citation level. It does not by itself assert
that every line, sentence, or paragraph below the heading has an explicit
machine-readable counterpart.

For verse, the initial convention is that source and translation lines are
parallel within a `source_ctf` unit when both sides have line structure. For the
sample above, the four German lines can be presented against the four source
verse lines by the authoring tool, even though the Org file only declares the
poem-level `source_ctf`.

For prose, the initial convention is paragraph first, then sentence. An editor
can help the translator compare source paragraphs or sentences inside the CTF
unit and can warn when counts diverge. That comparison is advisory unless a
later version records subalignment explicitly.

The v1 format should permit later refinement without rewriting the document
model. Likely extensions include:

- lower-granularity CTF trees for verse lines, paragraphs, or sentences;
- explicit subalignment records attached to a heading;
- one translation heading corresponding to multiple source CTF nodes;
- multiple translation headings corresponding to one source CTF node.

## Notes and untethered content

Notes are part of the translation. In Org v1, `#+BEGIN_NOTE` blocks under a
heading are attached to that translation unit. They do not automatically align
to the source.

This distinction matters:

- a note under a `source_ctf` heading comments on the translated unit;
- a note or paragraph under a heading without `source_ctf` is untethered
  translation material;
- a future explicit note anchor may be added if a note needs to point to a
  source CTF node, source segment, or substring.

Importers should preserve notes as first-class translation content. They should
not concatenate notes into the translated prose in the way some TLS-derived
translation exports currently do.

## Canonicalization and import direction

The Org file is the first authoring format, not necessarily the long-term
archival serialization. A v1 importer should parse the Org document into a
format-neutral translation model:

- metadata for the translation bundle;
- an ordered tree or list of translation units;
- each unit's heading, body prose, notes, and optional `source_ctf`;
- source pin metadata, including canonical identifier and hash;
- enough derived alignment records for current BKK search and parallel-display
  APIs.

Hashing should be over the parsed canonical model rather than raw Org bytes.
Trivial Org formatting changes should not alter the translation identity, but
changes to reading order, heading text, translated prose, notes, or alignment
properties should.

The source pin should include the source bundle hash. If CTF assets are stored
or distributed separately from the source bundle, the importer should also
record the CTF hash or equivalent provenance so that `source_ctf` resolution is
reproducible.

## Validation requirements

An Org v1 translation is valid when:

- required bundle metadata is present;
- every `source_ctf` value is syntactically valid;
- every `source_ctf` resolves uniquely against the pinned source CTF data;
- the source bundle hash matches the declared source pin;
- notes and body content can be parsed without losing ordering;
- generated bundle/index output is stable across repeated imports.

Validation should also report non-fatal authoring diagnostics:

- source CTF nodes with no corresponding translation unit;
- translation units without `source_ctf`;
- source and translation line counts that diverge inside verse units;
- paragraph or sentence count mismatches inside prose units;
- repeated use of the same `source_ctf` value.

Repeated use is not automatically an error. It may be intentional when a source
unit is translated in several parts, quoted in a note, or represented by
parallel renderings.

## Authoring tool expectations

The format becomes much more useful with a purpose-built editor. The editor
should be a client of the BKK resolver and CTF machinery rather than carrying an
independent resolution model.

A practical editor should:

- display the source CTF tree beside the translation outline;
- assign `source_ctf` to the active Org heading without hand-editing property
  drawers;
- resolve path references such as `KR4c0022/13/14` to concrete CTF nodes;
- show translated, untranslated, repeated, and dangling alignment states;
- provide verse line and prose paragraph/sentence comparison inside the current
  CTF unit;
- preserve hand-authored Org formatting as much as possible;
- maintain metadata such as `resp` and `modified` if segment-level editing is
  later added.

Generic Org editing should remain possible. The purpose-built editor is for
alignment visibility, validation, and migration assistance, not for making the
file readable.

## Pitfalls and open work

The largest implementation risk is CTF stability. Path-level authoring
references avoid raw offsets, but they still depend on a stable citation tree.
Changes in heading derivation, source structure, or CTF generation can move or
rename nodes. Pinning source and CTF hashes is therefore part of the format, not
an optional publishing detail.

The second risk is pretending that implicit lower-level alignment is more
precise than it is. A poem-level `source_ctf` can support good authoring tools,
but it should not be exposed as verified line-level alignment until the
translation or source CTF records that information.

The third risk is migration from TLS-style translations. Existing segment lists
can seed a new Org document, but they cannot automatically recover translator
headings, paragraphing, notes, or readable order. Migrated translations need a
human review pass before they are published in this format.

The next concrete work should be:

- define the minimal translation metadata header for Org files;
- choose the compiled canonical model;
- implement an Org parser/importer for `source_ctf` headings and notes;
- add validation against pinned source CTF data;
- teach the translation indexer to consume the compiled high-level alignment.
