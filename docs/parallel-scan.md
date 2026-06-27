# Parallel-passage scan

Finds every text span of length ≥ `min_length` that appears verbatim in two or
more places in the corpus, without holding the whole corpus or all candidate
pairs in RAM. Implemented in
[`module/bkk/index/parallel_scan.py`](../module/bkk/index/parallel_scan.py);
exposed as `bkk index parallel-scan` and reused by `bkk index duplications`.

The trigram/seed finder in [`parallel.py`](../module/bkk/index/parallel.py) is
the right tool when you have a known seed term and a small index. The scanner
described here is the corpus-wide version: external-memory, no seed required.

## Goal and approach

The output is a list of `ParallelCluster`s: each cluster is one piece of text
plus every location (textid, juan_seq, bucket, start, end) where it occurs.

The pipeline runs in three phases:

1. **Fingerprint** every bucket; partition fingerprints across 256 files on
   disk by hash.
2. **Process one partition at a time**: group fingerprints by hash, enumerate
   pairs within each group, extend each pair to its maximal exact match.
3. **Cluster** identical extended spans into one `ParallelCluster` per repeated
   passage.

## Phase 1 — fingerprint every bucket

Function: [`_write_anchor_partitions`](../module/bkk/index/parallel_scan.py#L199),
helper: [`_winnowed_anchors`](../module/bkk/index/parallel_scan.py#L245).

Stream each bucket's text from sqlite. For every bucket:

1. Slide an `anchor_length`-character window (default 12) over the text. For
   every position, hash that substring with BLAKE2b (8 bytes) — call the
   result an *anchor*.
2. **Winnow** the anchors: within every window of
   `min_length − anchor_length + 1` consecutive anchors, keep only the one
   with the smallest hash. The remaining anchors are the bucket's
   *fingerprints*.
3. Append each kept fingerprint to one of `partitions` (default 256) files on
   disk. The partition is chosen by the first 8 hex digits of the hash modulo
   `partitions`. Each line is `hash \t bucket_id \t position`.

**Why winnowing works.** If two regions of length ≥ `min_length` are
*identical*, they contain the same sequence of `anchor_length`-char windows
in the same order, so winnowing them produces the same minimum and they end
up with at least one common fingerprint. Same hash → same partition file →
the pair gets compared in phase 2. The fingerprint set is much smaller than
"every position", which is what makes the corpus-scale scan tractable.

## Phase 2 — process partitions one at a time

Function: [`_process_partitions`](../module/bkk/index/parallel_scan.py#L280).

For each of the partition files:

1. Load that file's fingerprints into a temporary sqlite table
   `anchor_occurrence(hash, bucket_id, position)`. Only this partition lives
   in memory at once.
2. Group rows by `hash` and keep groups with ≥ 2 occurrences. Skip groups
   with more than `max_anchor_occurrences` rows (default 200) — those are
   stop-phrase-like and would explode pairwise enumeration.
3. For each surviving group, enumerate all (i, j) pairs of occurrences and
   call [`_maximal_pair_span`](../module/bkk/index/parallel.py#L397) (reused
   from the trigram finder). Both sides start from an `anchor_length` exact
   match at the fingerprint position; the function extends left and right
   character-by-character as long as both texts agree. If the resulting
   span is ≥ `min_length`, insert the pair
   `(bucket_a, start_a, end_a, bucket_b, start_b, end_b)` into the
   `candidate_span` work table.

After this phase every long-enough exact repeat in the corpus is represented
as one or more pairwise span records.

## Phase 3 — cluster the pairs

Function:
[`_clusters_from_work_spans`](../module/bkk/index/parallel_scan.py#L396).

1. Walk `candidate_span` in descending length order. Read side A's actual
   text (`info_a.text[start:end]`) and key it by `(sha256(text), length)`.
   All sides — both `A` and `B` of every pair — that share the same exact
   text get added to the same cluster's `spans` set.
2. Keep clusters with ≥ `min_occurrences` distinct spans (default 2).
3. Unless `include_contained=True`, drop clusters whose every span sits
   inside a longer cluster's span
   ([`_remove_contained_clusters`](../module/bkk/index/parallel.py#L871)).
4. Materialise each cluster as a `ParallelCluster` with full
   `ParallelLocation` records (textid, juan_seq, bucket, start, end,
   toc_label, left/right context).

## Memory shape

- Phase 1 holds open `partitions` file handles plus one bucket's text at a
  time.
- Phase 2 holds one partition's fingerprints in sqlite plus, transiently,
  the postings of one hash group.
- Phase 3 groups by exact span text. The work table lives on disk; the
  in-memory grouping dict is proportional to the *number of clusters*, not
  to the corpus size.

## Tuning knobs

| flag | default | effect |
|---|---|---|
| `--min-length` | 24 | minimum reported span length |
| `--anchor-length` | 12 | fingerprint window size; must be ≤ `min_length` |
| `--min-occurrences` | 2 | minimum locations per cluster |
| `--max-anchor-occurrences` | 200 | drop fingerprint groups bigger than this (stop-phrase guard) |
| `--partitions` | 256 | number of on-disk anchor partitions |
| `--bucket` | `body` | restrict to `front` / `body` / `back`, or `all` |
| `--include-contained` | off | keep clusters wholly inside longer ones |

Larger `min_length` means a wider winnow window, fewer fingerprints, and a
faster scan. Lowering `max_anchor_occurrences` skips highly repetitive
boilerplate (formulaic chapter openings, common honorifics) cheaply.

## Downstream: `bkk index duplications`

[`bkk index duplications`](../module/bkk/index/duplications.py) is a thin
post-processor over this scan. It runs the scan with a high default
`--min-length` (200) and aggregates clusters into one row per
(juan_a, juan_b), merging overlapping spans into unique covered positions per
side so a juan that shares a 1000-char block with another juan rises to the
top of the report.
