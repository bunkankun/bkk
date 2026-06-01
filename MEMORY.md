# Project memory

Durable notes about this project that are awkward to re-derive each session.

## The Kanripo source corpus at `/home/Shared/krpdev/gh`

This is the working corpus we are surveying to define `bkk-cjk-v1`,
the first canonical character set under bunkankun.md §Canonicalization.

CBETA source is defined in .bkkrc, it sits at '/home/chris/src/xml-p5'

### Tree shape

- ~94 top-level directories named `KR1a` … `KR6v`, one per Kanripo
  category (also a handful of utility files: `*.config`, `*.sh`,
  `properties.txt`, `KR-Catalog`, `KR-Workspace`, etc.).
- Each category dir holds bundle dirs, e.g. `KR1a/KR1a0001/`. ~9,260
  bundles total across the tree.
- Each bundle dir holds:
  - numbered juan files `<bundle>_NNN.txt` (~105K `.txt` files corpus-wide)
  - one `Readme.org` index file
  - a `.git/` repo (per-bundle history; dominates the 14 GB tree size)
  - sometimes a `.gitignore`
- Total `.txt` content is small (~25 MB); the size budget for the
  corpus is the per-bundle git histories, not the text itself.

### File encoding & format

- All `.txt` files are UTF-8 (no exceptions found in survey).
- Each `.txt` opens with org-mode-style header lines, in this shape:

  ```
  # -*- mode: mandoku-view -*-
  #+TITLE: 周易
  #+DATE: 2016-08-10 10:17:03
  #+PROPERTY: ID KR1a0001
  #+PROPERTY: BASEEDITION tls
  #+PROPERTY: JUAN 0
  #+PROPERTY: CAT 1pre-han,經學典籍,易經類
  ```

  Headers are highly consistent across the corpus. The first non-`#`
  line begins the body.

### Body content

- Body is dominated by CJK Unified Ideographs, CJK Symbols and
  Punctuation, and Halfwidth/Fullwidth Forms.
- Yijing hexagram codepoints (U+4DC0..U+4DFF) appear in classics like
  KR1a0001 (周易) — small but real.
- Inline markup is narrow:
  - `<pb:KR1a0001_tls_001-1a>` — page-break tags. ~5M occurrences
    corpus-wide. The only non-entity inline tag family found.
  - `¶` (U+00B6) — paragraph marker, often paired with `<pb:…>`.
  - **No** TEI `<g/>` elements or other XML tag families.
- Org-mode markup that surfaces in bodies:
  - `**` (and longer `***+`) at line start followed by a space —
    org-mode headline / register-divider markup. Strip the leading
    asterisks; the text after is real content (e.g. `** 《乾第一》`).
  - **`#`-prefixed comment lines inside the body**, not just at file
    top. Examples:
    ```
    # src: XC 1.2, ed. Lou Yulie p. 535, tr. Lynn p. 47
    # dating: 8110
    ```
    These are editorial annotations / metadata, not text content.
    The right rule is to strip every line beginning with `#`,
    anywhere in the file — not just the contiguous header block.
    A naive "drop leading header" pass leaves these in and pollutes
    the codepoint table with Latin letters.

### Entity references

Two namespaces, both bridges to characters not (yet) in Unicode:

- `&KRxxxx;` — Kanripo glyph identifiers (~23K distinct forms).
- `&CH-0Fxxxxxx;` — CHISE-derived PUA identifiers (~500 distinct).

Per the spec, these will expand to PUA codepoints (SPUA-A, U+F0000+)
during the entity-expansion step of canonicalization, governed by an
**entity encoding** asset.

### PUA

- Literal PUA codepoints in body text: **not seen**. PUA only appears
  via entity references. Anything decoded as a PUA codepoint in the
  raw stream would be a surprise worth investigating.

### What to skip when processing

- `.git/` directories (every bundle has one).
- Any dotfile (`.gitignore`, etc.).
- `Readme.org` per bundle — bundle index, not text content.
- Top-level `*.config`, `*.sh`, `*.txt` (`cb2md-shell-2016-06.txt`,
  `cl-shell.txt`) — these are sibling utility files, not bundles.

### Survey tooling

- [tools/char_survey.py](tools/char_survey.py) walks the corpus and
  emits frequency tables to `tools/survey-out/`.
- Outputs: `codepoints.tsv`, `blocks.tsv`, `categories.tsv`,
  `entities.tsv`, `markup.tsv`, `summary.txt`.
- Smoke test: `--root /home/Shared/krpdev/gh/KR1a/KR1a0001` (~70 files,
  finishes in <1 s). Useful for any future change to the tokenizer.

## Variant-pair analysis (Unihan_Variants ↔ corpus)

Built on top of the corpus codepoint table. Source data is the Unicode
Unihan files under [data/](data/) — `Unihan_Variants.txt` and
`Unihan_IRGSources.txt` (UCD 17.0.0).

Goal: identify candidate substitution mappings for canonicalization
(spec: bunkankun.md §Substitution mappings). For each Unihan variant
relation `{A, B}` we ask whether both members appear in the corpus,
how often, and whether the IRG treats both as legitimately distinct
encodings.

### Vocabulary used in the analysis

- **major / minor**: of an unordered variant pair, the member with the
  higher / lower corpus count. The strategy is to canonicalize toward
  the major.
- **Extended T-set** (the "Taiwan / IICore-T" set): codepoints whose
  `kIRG_TSource` value begins `T1-` or `T2-`, **or** whose `kIICore`
  value contains the letter `T`. Size: 13,158 codepoints. Heuristic
  rationale: if the IRG's Taiwan body has explicitly assigned a source
  reference, the character is treated as a legitimate distinct encoding
  worth preserving.
- **Exclusion rules** (pairs we will *not* normalize):
  - **(a)** both members of the pair are in the extended T-set,
    regardless of variant type. 964 pairs in the corpus match.
  - **(b)** the pair is tagged `kSpecializedSemanticVariant` (the
    relation indicates a meaning-distinct split, e.g. 才/財, 坐/座).
    294 pairs match.
  - Union is 1,064 pairs. These belong to no substitution mapping.

### Outputs in `tools/survey-out/`

The survey-output directory holds two layered groups of files:

**Corpus survey** — produced by `char_survey.py` from the raw .txt files:
- `codepoints.tsv` — per-codepoint frequency table for the residue
  (after stripping headers, layout markup, and entity references).
  This is the canonical input for everything downstream.
- `blocks.tsv`, `categories.tsv` — Unicode-block / general-category
  aggregates derived from `codepoints.tsv`.
- `entities.tsv` — distinct `&KRxxxx;` and `&CH-0Fxxxxxx;` references
  with corpus counts.
- `markup.tsv` — `<pb:…>` total + samples and any other angle-bracket
  markup encountered.
- `summary.txt` — file count, decode errors, NFC parity, top-N tables.

**Variant analysis** — produced by `variant_overlap.py`,
`variant_pairs.py`, plus inline filtering scripts whose logic is
captured in this section:

- `variant_overlap.tsv` / `variant_overlap.txt` — for each Unihan
  variant type, how many corpus codepoints appear in column 1 ("source
  side", has a variant defined) vs column 3 ("target side", named as
  someone's variant). First-cut overlap report; superseded by the
  pair-level views below for normalization decisions.
- `variant_pairs.tsv` — **3,644 unordered pairs** where both members
  appear in the corpus. One row per pair: cp_major, char_major,
  count_major, cp_minor, char_minor, count_minor, ratio, types.
  This is the master list everything downstream filters from.
- `variant_pairs_simp_trad.tsv` — subset of `variant_pairs.tsv`
  carrying both `kSimplifiedVariant` and `kTraditionalVariant`. **1,578
  pairs**, sorted by ratio desc. View, not used directly as exclusion
  criterion any more (subsumed by rule (a) once we generalized it).
- `variant_pairs_simp_trad_T1T2.tsv` — `variant_pairs_simp_trad.tsv`
  further restricted to pairs where both members are in the extended
  T-set. **170 pairs**. Historical artifact from when rule (a) was
  defined more narrowly; kept as a categorisation view.
- `variant_pairs_excluded.tsv` — **1,064 pairs**: union of rules (a)
  and (b). Pairs we will *not* normalize. Each row carries the same
  columns as `variant_pairs.tsv`.

**Decision lists** — the two views the user reviews when deciding the
canonical-set substitution mappings. Both carry the augmented schema:

```
replace  cp_major  char_major  count_major  cp_minor  char_minor
count_minor  ratio  types  major_sources  minor_sources
```

`replace` is `yes` by default and `no` when either side is in the
dedicated radical blocks (U+2E80–U+2EFF Kangxi Radicals Supplement,
U+2F00–U+2FDF Kangxi Radicals). Currently no rows fire under that
strict definition; the column is a placeholder for hand-marking
positional radical variants that live in CJK Unified blocks.

`major_sources` / `minor_sources` is a compact string assembled per
codepoint from `Unihan_IRGSources.txt`: the IRG source labels present
(G, H, J, Kp, K, M, S, Tn, Uk, U, V — where `Tn` is the T-source
prefix like `T1`, `T2`, `T3`, `TF`) followed by `/<kIICore value>` if
present, e.g. `GHJKpKT1/AGTHKMP`.

The two decision lists, both sorted by ratio descending:

- **`T1T2_codepoints_replaced.tsv`** — **252 pairs**. Pairs surviving
  the exclusion (rules a∨b) where the **minor** is in the extended
  T-set. These are the cases where a T-flagged codepoint would be
  *normalized away* in favour of a more-frequent non-T-flagged major.
  Worth eyeballing case-by-case before committing — that's why this
  list exists. Examples at the top: 别/彆, 鰌/魷, 氷/冫.
- **`T1T2_codepoints_kept.tsv`** — **2,103 pairs**. Pairs surviving
  the exclusion where the **major** is in the extended T-set and the
  **minor** is not. The standard, lower-controversy case: a T-flagged
  codepoint absorbs a non-T-flagged variant. Top of the list is
  dominated by traditional-vs-simplified pairs where the simplified
  G-source-only minor occurs 1-6 times in the corpus (e.g. 軍/军,
  發/发, 觀/观, 馬/马).

### How the analysis connects to the spec

Each row in the two decision lists, once approved, becomes one entry
in a substitution mapping (bunkankun.md §Substitution mappings).
Applying that mapping during canonicalization step 5 emits a
`substitution` marker with `reason: not-in-canonical-set` (or
`scribal-variant-collapsed` where applicable) and pins the mapping
identifier and entry id. Reversibility is preserved through the
marker; the canonical set itself need only contain the major side.
