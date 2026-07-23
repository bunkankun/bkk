# Parallel Passage Options

This document summarizes the main ways this codebase can find and view
parallel passages, with the algorithms explained in non-technical terms.

## Summary

| Option | Best for | What it does |
|---|---|---|
| `bkk index parallel` | Known phrase, one text, or small scans | Finds repeats around a seed term or target text |
| `bkk index parallel-scan` | Full-corpus exact repeats | Finds all long exact duplicated passages |
| `bkk index parallel-fuzzy-from-scan` | Near matches after an exact scan | Expands exact results to allow small differences |
| `parallel-lookup-build` / `parallel-lookup-at` | Fast "what parallels touch this spot?" lookup | Builds a `.bkkp` sidecar for point queries |
| Per-juan `.parallels.yaml` assets and serve endpoints | Reader-facing passage display | Stores and serves links from one juan to matching passages |
| `bkk index duplications` | Cleanup and duplicate-juan review | Ranks juan pairs by how much text they share |

## Basic Idea

Most of the parallel-passage tools follow the same pattern: the system first
looks for a small shared piece of text, called an anchor, then grows that anchor
left and right until it has found the full matching passage.

This avoids comparing every possible passage with every other possible passage.
Instead, the code first finds likely candidates, then checks only those.

## Seed Or Trigram Search

Implemented in `module/bkk/index/parallel.py`.

The `.bkkx` index stores short 3-character pieces of text, called trigrams. If
you search for a seed term, the tool finds every place that seed appears. Then,
for every pair of places, it expands the match left and right until the texts
stop matching.

In plain terms: if two passages both contain the same distinctive phrase, the
tool starts from that phrase and asks, "How much more text around this phrase is
also the same?"

This is best when you already know a phrase, or when you want to scan one
specific text or juan against the corpus.

## Corpus-Wide Exact Scan

Implemented in `module/bkk/index/parallel_scan.py` and documented in
`docs/parallel-scan.md`.

For large corpus scans, the code uses compact fingerprints. It slides a
fixed-length window, usually 12 characters, across every text bucket. Instead of
keeping every possible window, it keeps only selected fingerprints using a method
called winnowing.

Winnowing means: in each larger window, keep the fingerprint with the smallest
hash value. This gives each passage a small set of representative bookmarks.

If two long passages are exactly identical, they will share at least one of
these bookmarks. The scanner groups matching bookmarks, compares only those
candidate pairs, expands them into full exact passages, and clusters identical
passages together.

In plain terms: it gives every text a small set of bookmarks, then compares only
places that share the same bookmark.

This is the main full-corpus exact discovery tool.

## Fuzzy Matching

Implemented in `module/bkk/index/parallel.py` and
`module/bkk/index/parallel_fuzzy_from_scan.py`.

Fuzzy matching still starts from an exact shared anchor. The difference is that,
while expanding left and right, it allows a small number of character edits:

- one character may be changed;
- one character may be inserted;
- one character may be deleted.

This catches passages that are mostly the same but have minor textual variation.
The edit budget is intentionally small, capped at 4 in the core code, so the
search stays controlled.

After a fuzzy match is found, the code records how each occurrence differs from
the representative passage. This uses standard edit-distance alignment: same
characters are grouped together, and substitutions, insertions, and deletions
are recorded separately.

`bkk index parallel-fuzzy-from-scan` is a refinement pass. It reads exact
clusters produced by `parallel-scan`, resolves their locations back into the
`.bkkx` index, and tries to extend those candidates fuzzily. It does not search
for every possible fuzzy match from scratch; it can only find fuzzy parallels
around exact anchors that were already discovered.

## Clustering

Exact matches are clustered by the repeated text itself. If the same passage
appears in several places, those locations become one cluster.

Fuzzy matches are clustered differently. The code uses a grouping method called
union-find. In plain terms, if passage A is close to passage B, and passage B is
close to passage C, all three can be grouped together. The longest member is
used as the representative passage.

Shorter clusters that sit entirely inside longer clusters are usually hidden,
because they are often just fragments of a larger, more useful match. Passing
`--include-contained` keeps those smaller contained clusters.

## Fast Point Lookup

Implemented in `module/bkk/index/parallel_lookup.py` and described in
`docs/plans/parallel-passage-lookup.md`.

`parallel-lookup-build` precomputes parallel clusters into a sidecar SQLite
file, usually named `_corpus.bkkp`. The sidecar stores cluster IDs and source
offsets. It does not store the full passage text; text and context are read from
the matching `.bkkx` index when needed.

After the sidecar exists, `parallel-lookup-at` can answer a focused question:
"At this text ID, juan, bucket, and offset, what parallel passages overlap this
point?"

This is the fast reader-facing lookup path. Expensive discovery happens once
during the build. Later queries are quick.

The server exposes the same idea through:

```text
/api/search/parallel-at
```

## Per-Juan Parallel Assets

Implemented in `module/bkk/index/parallel_assets.py` and served by
`module/bkk/serve/routers/parallels.py`.

The code can write YAML marker files such as:

```text
KRxxxx_001.corpus.parallels.yaml
```

Each marker says: at this local offset and length, there is a parallel passage
at another text, juan, bucket, and offset. These markers are directed from the
local juan to remote passages.

The server can load these marker files, filter them, paginate them, and hydrate
the actual passage text from the `.bkkx` index or source files.

The main endpoints are:

```text
/api/bundles/{textid}/juan/{seq}/parallels/status
/api/bundles/{textid}/juan/{seq}/parallels/generate
/api/bundles/{textid}/juan/{seq}/parallels
```

On-demand generation uses the targeted trigram search. This lets a juan get its
own stored parallel markers without requiring a whole-corpus report first.

## Duplication Reports

Implemented in `module/bkk/index/duplications.py`.

`bkk index duplications` reuses the corpus-wide exact scanner, but instead of
reporting every passage cluster directly, it aggregates results by juan pair.

For each pair of juan, it measures how many characters are duplicated, merges
overlapping spans, records the longest shared span, and sorts the largest
duplications first.

This is useful for editorial cleanup: it helps identify texts or juan that may
be duplicates, near-duplicates, or contain very large copied blocks.

## Rule Of Thumb

Use `bkk index parallel` for targeted discovery, `parallel-scan` for complete
exact corpus discovery, `parallel-fuzzy-from-scan` for near matches after an
exact scan, `.bkkp` lookup for fast offset-based reader interactions,
`.parallels.yaml` assets for stored per-juan display, and `duplications` for
editorial review of heavily duplicated juan pairs.
