# Parallel-passage lookup structure (point queries over the trigram index)

## Summary
Add a **sidecar SQLite artifact** (`_corpus.bkkp`, one per `.bkkx`) that
precomputes fuzzy parallel-passage clusters and indexes every occurrence by
`(bucket_id, start, end)`, enabling fast **point queries**: "given a location
(textid, juan, bucket, offset), return parallel passages elsewhere in the
corpus," filtered at runtime by `min_length`, `max_edits`, and
`min_occurrences`. The `.bkkx` schema is left unchanged; the sidecar is keyed to
the index hash/signature and hydrates text/context/TOC labels from the `.bkkx`
at query time.

One important semantic consequence of "precompute fuzzy + runtime
`min_length`/`max_edits`": both filters are monotonic against a single fuzzy
build. Precompute once at a generous budget (`max_edits=E_max`, small
`min_length=L_floor`); at query time keep clusters with `length >= min_length`
and occurrences with `edit_distance <= max_edits`. An occurrence with
`edit_distance == 0` is byte-identical to the representative over its full
length, so a `max_edits=0` query yields truly-exact parallels — no runtime
re-extension needed.

## Data model (new `module/bkk/index/parallel_lookup.py` + schema constants)
Aux DB tables:
- `meta(key,value)` — `schema_version`, source `index_path`, `index_hash`,
  `index_signature`, build params (`bucket`, `min_length` floor,
  `anchor_length`, `max_edits` E_max, `max_anchor_occurrences`,
  `min_occurrences` build floor), and `status`.
- `pcluster(cluster_id INTEGER PK, length INTEGER, rep_bucket_id INTEGER,
  rep_start INTEGER, rep_end INTEGER, occurrence_count INTEGER,
  max_edits INTEGER)`.
- `poccurrence(cluster_id INTEGER, bucket_id INTEGER, start INTEGER,
  end INTEGER, edit_distance INTEGER)`.
- Indexes: `idx_poccurrence_loc ON poccurrence(bucket_id, start, end)`,
  `idx_poccurrence_cluster ON poccurrence(cluster_id)`.

Optional Stage 0 tables (present only when the sketch pre-filter is enabled):
- `psketch(bucket_id INTEGER PRIMARY KEY, sketch BLOB NOT NULL)` — compact
  per-bucket MinHash/SimHash signature.
- `plsh_band(band_hash TEXT NOT NULL, bucket_id INTEGER NOT NULL)` — LSH band
  postings for candidate-bucket shortlisting.
- Index: `idx_plsh_band ON plsh_band(band_hash, bucket_id)`.

No text is stored in the sidecar; representative/occurrence text, `left`/`right`
context, and `toc_label` are read from the `.bkkx` via the existing
`_BucketCache`/`_make_location`/`_toc_label`.

## Build pipeline (`build_parallel_lookup(...)`, reuses existing engines)
0. **(Optional) MinHash/LSH pre-filter** (Stage 0): compute a small per-bucket
   MinHash (or SimHash) signature over each bucket's k-gram set and store it in
   a sidecar table (dozens of ints per bucket, not one row per position). Query
   it via LSH banding to shortlist "which other buckets are plausibly similar"
   before any span work, so Stages 1-2 only run on candidate bucket pairs
   instead of the full anchor cross-product. This is a candidate-narrowing
   optimization, not a correctness requirement; it is most valuable when the
   `max_anchor_occurrences` cap (Stage 1) would otherwise skip high-frequency
   anchors and lose recall. Ship it as an opt-in build flag.
1. **Exact spans**: run `discover_parallel_passages_scan` (external-memory,
   partitioned, `--work-db` reusable) to get maximal exact `candidate_span`
   rows. Its `scan_meta` hash/signature keying is inherited as the sidecar's
   cache key.

   **Anchor selectivity note.** Candidate generation - not span extension - is
   the dominant cost, because a single Han trigram can occur thousands of times
   and drives a quadratic pair explosion inside a posting list. The scan already
   mitigates this with **winnowed 12-char `blake2b` anchors** (default
   `anchor_length=12`), which is strictly better than a dense higher-order
   ("trigram of trigrams" / 5-gram) index on both storage and fuzzy recall. Do
   **not** add a nested/dense k-gram index. Instead, expose and tune
   `anchor_length` + winnowing as build parameters, and revisit the
   `max_anchor_occurrences` skip (currently a silent recall leak on frequent
   anchors): longer, more selective anchors shrink posting lists and reduce the
   number of skipped groups. Note the recall/selectivity trade-off - longer
   exact anchors are more fragile to edits, so keep `anchor_length` tunable
   rather than maximal, since fuzzy matches are a first-class goal.
2. **Fuzzy extend**: stream each `candidate_span`, seed
   `_maximal_pair_span_fuzzy(cache, a, start_a, b, start_b, exact_len, E_max)`
   to extend past the exact core with up to `E_max` edits, and persist fuzzy
   pair spans (with `edits`) — done per scan partition and merged, mirroring the
   existing partition/merge structure to bound memory.
3. **Cluster + persist**: cluster pair spans with the existing union-find
   (`_clusters_from_spans_fuzzy` logic), writing `pcluster`/`poccurrence` rows
   (representative = longest member; `edit_distance` = distance to
   representative). Clustering is performed per representative-core group to keep
   union-find bounded at corpus scale; `include_contained=False` by default via
   the existing containment filter.

## Query API (`ParallelLookup` class)
- `ParallelLookup(index_path, lookup_path)` opens both DBs read-only, validates
  the sidecar's `index_hash`/`signature` against the `.bkkx`, and raises a
  rebuild hint on mismatch (mirrors `test_index_rejects_old_schema`).
- `find_at(textid, juan_seq, bucket="body", offset, *, min_length, max_edits=0,
  min_occurrences=2, context=20, mode="overlap", include_self=False) ->
  list[ParallelCluster]`:
  1. resolve `bucket_id` from `juan`+`bucket`;
  2. `SELECT cluster_id, edit_distance FROM poccurrence WHERE bucket_id=? AND
     start<=? AND end>?` (overlap; `mode="cover"` requires `start<=offset` and
     `end>=offset` full containment);
  3. keep clusters with `pcluster.length >= min_length` and hit
     `edit_distance <= max_edits`;
  4. load sibling occurrences filtered by `edit_distance <= max_edits`; require
     `>= min_occurrences`;
  5. hydrate via `_make_location` (+ `_align_ops` diffs vs. representative), sort
     by `(-length, edits, cluster_id)`, optionally drop the query's own
     occurrence.
- Reuses `ParallelCluster`/`ParallelLocation`; exported from
  `bkk/index/__init__.py`.

## CLI + serve (fits existing patterns)
- `python -m bkk.index parallel-lookup-build <bkkx> [--out PATH] [--bucket]
  [--min-length L_floor] [--anchor-length] [--max-edits E_max]
  [--max-anchor-occurrences] [--work-db] [--jobs] [--quiet]`.
  - Stage 0 (opt-in) flags: `--enable-sketch-prefilter`, `--sketch-k-gram`,
    `--sketch-size`, `--lsh-bands`. `--anchor-length` is the primary exact-anchor
    selectivity knob.
- `python -m bkk.index parallel-lookup-at <bkkx> <textid> <juan_seq> <bucket>
  <offset> [--min-length] [--max-edits] [--min-occurrences] [--context]
  [--format jsonl|tsv]` reusing `write_parallel_report`.
- Optional serve endpoint `GET /search/parallel-at` returning the existing
  `ParallelClusterModel` shape; gated on the sidecar being present. The endpoint
  queries only the finished sidecar and is agnostic to whether Stage 0 was used
  at build time.

## Test Plan
- **Build/query roundtrip**: two texts sharing a passage → `find_at` at an
  offset inside the shared span returns the sibling occurrence; offset outside
  returns empty (extend `_write_bundle`/`_merge` fixtures in
  `test_index_parallel.py`).
- **Runtime filters**: precompute with `E_max=4`; `max_edits=0` returns only
  exact occurrences, higher values add fuzzy ones; `min_length` above the
  cluster length drops it, below still matches; `min_occurrences` gates cluster
  inclusion.
- **Overlap vs. cover mode**, and **self-exclusion** (`include_self`).
- **Staleness**: sidecar built against one `.bkkx`, opened against a modified
  index → raises rebuild error (hash/signature mismatch).
- **Contained-cluster suppression** parity with existing behavior; **CLI**
  writes JSONL/TSV.
- Determinism: sorted, stable `cluster_id`s across rebuilds of identical input.
- **Anchor tuning**: changing `anchor_length` changes candidate/span generation
  behavior but preserves deterministic query output on a small fixture.
- **Stage 0 pre-filter**: enabling `--enable-sketch-prefilter` reduces the
  candidate bucket-pair set while preserving expected parallels on a controlled
  fixture.
- **Param metadata/staleness**: sidecars built with different sketch or anchor
  params are rejected or rebuilt rather than silently reused (params recorded in
  `meta`).

## Assumptions & Defaults
- Point query requires a `bucket` kind (default `body`) plus a bucket-local
  `offset`; results are other occurrences of the same passage (overlap
  semantics by default).
- Single fuzzy build at `E_max=4`, `min_length` floor `8`, `min_occurrences`
  floor `2`; runtime `min_length`/`max_edits`/`min_occurrences` may only
  tighten. Requests below the build floors are rejected with a clear error.
- `edit_distance` is distance to the cluster representative (consistent with
  existing fuzzy clustering); this is the runtime `max_edits` metric.
- Sidecar stores structure only; text/context/labels hydrate from the `.bkkx`,
  so the two files must stay paired (validated by hash).
- Corpus-scale fuzzy precompute is the accepted cost of "precompute fuzzy";
  build reuses the scan's partitioned external-memory path and a reusable
  `--work-db`, and clusters per representative-core to bound union-find memory.
  If build size/time proves prohibitive at 12,500 texts, the fallback is to
  precompute exact spans only and extend fuzzily on the query shortlist (out of
  scope here).
- Stage 0 (MinHash/LSH sketch pre-filter) is **disabled by default** for v1 and
  is an optimization, not a correctness dependency. Default sketch params are
  implementation defaults only (not public guarantees): `sketch_k_gram=5`,
  `sketch_size=128`, `lsh_bands=16`.
- `anchor_length` remains tunable: longer anchors improve selectivity but reduce
  fuzzy recall, so it is exposed rather than fixed at a maximal value.
