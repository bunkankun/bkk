# BKK import module (TLS first)

## Context

[README.md](README.md) introduces an `import/` module that converts external sources into the BKK archival format defined in [../bunkankun.md](../bunkankun.md). The CLI shape will be `bkk import --format <format>`, with `tls` (TLS / HXWD XML) the focus of v1 and `krp` (Kanseki Repository) deferred. There is no existing code yet; a hand-crafted target output sample lives at [samples/KR6q0053](samples/KR6q0053). The intended outcome is a Python module that reads a TLS source bundle and writes a BKK bundle whose *shape, content, and hashes* match the sample's spirit; the sample is a witness, not a byte-precise oracle, and divergences are surfaced in a report for user review rather than chased to byte-equality.

## Decisions captured (from the planning conversation)

- **Language:** Python. Run as `python -m bkk.importer --format tls --in <tls-root> --out <out-root> --text-id <id>`. No installable CLI yet.
- **Front/body/back split:** heuristic on the `<head>` text of each top-level `<div>`. A configurable list (default `['序']`) selects divs that go to `front`; the remainder go to `body`. `back` stays empty for TLS.
- **Editions:** master (interpretive) is a byte-copy of the T (documentary) edition for now; both manifests are emitted.
- **Hashes:** full per-spec. Text and juan hashes are real. Manifest `hash:` is computed by the self-referential pattern (zero-the-field, JCS, SHA-256, patch back). `canonical_set.hash` stays zeroed until `bkk-cjk-v1` is finalized.
- **Canonicalization step 5 (substitution):** skipped for v1. NFC + layout-feature extraction only; no `substitution` markers.
- **Annotations:** one merged `<text-id>_<NNN>.ann.yaml` per juan, role `tls:ann`, sorted by offset; provenance distinction (swl vs doc) discarded for now.
- **Output path:** `import/output/<text-id>/...` mirroring the sample tree exactly.
- **Verification:** semantic-equal diff of generated tree vs `samples/KR6q0053`. The sample is hand-crafted — match the *spirit* (same shape, same content, same hashes after canonical hashing) rather than the byte-for-byte letter. Divergences from the sample are surfaced as a report for the user to review, not asserted away.

## Module layout

```
import/
  bkk/
    __init__.py
    importer/
      __init__.py
      __main__.py        # python -m bkk.importer dispatch
      cli.py             # argparse: --format, --in, --out, --text-id
      ir.py              # dataclasses: Bundle, Juan, Section, Marker, Annotation, TocEntry
      canonicalize.py    # NFC + offset-tracked extraction; sha256 over UTF-8 text
      jcs.py             # vendored RFC 8785 emitter (~60 LOC, tested against RFC vectors)
      hashing.py         # sha256_text(str), sha256_jcs(obj), manifest_hash(dict)
      classify.py        # head-text heuristic -> front/body/back bucketing
      read/
        __init__.py
        tls.py           # parse text XML + swl + doc anns -> IR (lxml.iterparse)
        krp.py           # placeholder
      write/
        __init__.py
        yaml_writer.py   # PyYAML SafeDumper + custom representers; flow-style for markers
        bundle.py        # orchestrates: IR -> juan files, ann files, T manifest, master manifest
  tests/
    test_jcs.py          # RFC 8785 vectors
    test_canonicalize.py
    test_tls_roundtrip.py # structural diff vs samples/KR6q0053; emits divergence report
    fixtures/
  pyproject.toml         # dev only: pytest, lxml, pyyaml
```

## Pipeline

1. **CLI** parses args, locates `<tls-root>/tls-texts/<text-id>.xml`, `<tls-root>/tls-data/swl/<text-id>-ann.xml`, `<tls-root>/tls-data/doc/<text-id>-ann.xml`.
2. **read/tls.py** (`read_tls(text_xml, swl_xml, doc_xml) -> Bundle`):
   - Stream the text XML with `lxml.etree.iterparse`.
   - For each top-level `<body>/<div>`, build a `Section` carrying `head_text`, `head_marker_id` (the inner `<seg xml:id>`, e.g. `…-h`, **not** the outer `…-h-h`), and an ordered child list (segments, page-breaks, paragraph-break boundaries, in-seg `<c>` punctuation).
   - Walk seg children — `<c>` can appear mid-seg (`咦<c n="！"/>擲地…`) — emitting punctuation markers at the running offset.
   - Stream both ann XMLs, build a flat list of `Annotation` records keyed by `seg_id` with `pos` retained.
3. **canonicalize.py** (`canonicalize(section_list) -> (text, markers, seg_offset_map)`):
   - For each section in order: NFC the segment text, append to a buffer, emit markers at the current buffer length.
   - Punctuation `<c>` content is stored on the marker, not in the buffer. `<pb>`, `<head>`, `paragraph-break`, `tls:seg` markers are zero-width.
   - Returns the canonical text, the marker list, and `{seg_id: start_offset}` for downstream annotation resolution.
4. **classify.py** buckets sections into front/body/back using head-text matching (default rule: head contains `序` → front).
5. **write/bundle.py**:
   - Build T-edition juan dict from `(front, body, back)` text + markers; compute `text_hash = sha256(utf8(text))` per [../bunkankun.md §Hash and integrity model](../bunkankun.md).
   - Compute `juan_file_hash = sha256(jcs(juan_dict))`.
   - Write `editions/T/<text-id>_<NNN>-T.yaml`. Byte-copy to `<text-id>_<NNN>.yaml` (master).
   - Resolve annotation offsets via `seg_offset_map[seg_id] + pos`; write `<text-id>_<NNN>.ann.yaml`.
   - Build TOC from `tls:head` markers: one entry per section; `marker_id` = the inner seg id; `span = [bucket, start_in_bucket, end_in_bucket]`. Treat the sample's `[body, 0, 0]` / `[body, 0:0]` rows as artifacts — compute proper offsets.
   - Write `editions/T/<text-id>-T.manifest.yaml` and `<text-id>.manifest.yaml`. Manifest hash via zero-then-JCS-then-patch.
6. **YAML emitter** (`write/yaml_writer.py`):
   - PyYAML `SafeDumper`, `default_flow_style=False`.
   - Per-node flow style on marker dicts (single-line `{type: …, offset: …, content: …, id: …}`).
   - Stable key order matching the sample (insertion order via plain `dict`).
   - LF line endings, final newline.
   - `represent_str` overridden to avoid bool/null misfires on values like `n` and `y`.

## Marker handling details

- **Types:** `page-break`, `tls:head`, `paragraph-break`, `tls:seg`, `punctuation` (extension via the `tls:` namespace per [../bunkankun.md §Markers](../bunkankun.md)).
- **Sort key at equal offset (priority):** `page-break < tls:head < paragraph-break < tls:seg < punctuation`. Encode this explicitly in `canonicalize.py`.
- **Whitespace** between XML elements is dropped silently.

## Hashes

- `text_hash`: SHA-256 over the UTF-8 bytes of the post-NFC text stream — **not** via JCS. This follows [../bunkankun.md §Hash and integrity model](../bunkankun.md) ("text field hash is taken over the UTF-8 byte sequence of its post-canonicalization text stream").
- `juan_file_hash`, `manifest_hash`: SHA-256 over JCS canonical JSON of the corresponding dict.
- Manifest self-reference: serialize with `hash: 'sha256:000…000'`, JCS, hash, write the result back into the dict, then YAML-emit.
- `canonical_set.hash`: stays at the zero string; flag in code with a `# TODO bkk-cjk-v1` so it can be flipped in one place once the canonical-set asset exists.

## Verification (end-to-end)

The sample at `samples/KR6q0053` is hand-crafted and is treated as a witness to the *intended shape*, not a byte-precise oracle. The verification regime reflects that:

1. Run `python -m bkk.importer --format tls --in input/tls --out output --text-id KR6q0053` from `import/`.
2. **Structural diff.** A test helper parses each YAML file in `output/KR6q0053` and the corresponding file in `samples/KR6q0053`, normalizes both into a comparable shape (sorted keys, marker collections sorted by `(offset, type-priority)`, hashes either compared exactly or stripped if known to be placeholder in the sample), and reports a structured **divergence report** rather than asserting equality. The report names every field that differs and classifies each as `expected` (e.g. sample's zeroed manifest hash vs our real hash, sample's `[body, 0, 0]` TOC artifacts) or `unexpected` (anything else).
3. **Spirit-of-the-sample assertions.** A small set of hard assertions: every juan body text is non-empty; every marker's offset lies within `len(text)`; every annotation's seg_id resolves; every text hash recomputes; every juan hash recomputes; the manifest hash recomputes after the zero-then-patch round-trip.
4. `pytest tests/` — JCS RFC 8785 vector tests, canonicalize unit tests, and the divergence-report test (which prints the report on success, fails only on `unexpected` rows).
5. The divergence report is written to `output/divergence-from-sample.md` after each run so the user can review what differs and decide whether the sample, the importer, or the spec interpretation needs adjustment.

## Implementation sequence

1. Skeleton + `pyproject.toml` + empty modules.
2. `jcs.py` + `hashing.py` + `tests/test_jcs.py` (RFC 8785 vectors).
3. `canonicalize.py` + tests for offset arithmetic on toy inputs.
4. `read/tls.py` text path → IR; intermediate test against the body-text-only round of the sample.
5. `classify.py` + `write/yaml_writer.py` + `write/bundle.py` for juan files + T manifest. Run the structural diff against `editions/T/*` and iterate until only `expected` divergences remain.
6. Master-edition emission. Structural diff against root `<text-id>.manifest.yaml` and `<text-id>_<NNN>.yaml`.
7. `read/tls.py` annotation path; ann offsets via the shared seg-offset map. Structural diff against `*.ann.yaml`.
8. `cli.py` + `__main__.py` wiring; full end-to-end run + divergence report written to `output/divergence-from-sample.md`.

## Critical files

- [bkk/importer/read/tls.py](bkk/importer/read/tls.py)
- [bkk/importer/canonicalize.py](bkk/importer/canonicalize.py)
- [bkk/importer/jcs.py](bkk/importer/jcs.py)
- [bkk/importer/write/bundle.py](bkk/importer/write/bundle.py)
- [bkk/importer/write/yaml_writer.py](bkk/importer/write/yaml_writer.py)
- [bkk/importer/ir.py](bkk/importer/ir.py)
- [tests/test_tls_roundtrip.py](tests/test_tls_roundtrip.py)

## Risks

- **Sample is hand-crafted.** It is a witness to the intended shape, not a precise oracle; the divergence report (above) is the surface where that gap is made visible. Plan time for a review pass with the user once the first end-to-end run lands.
- **Self-hashing manifest.** Verifier must reverse the patch; document the procedure inline.
- **Annotation file size (~1.7 MB).** Stream the ann XML rather than building a full DOM.
- **Sample TOC artifacts.** Two TOC entries in the sample have `[body, 0, 0]` / `[body, 0:0]` — implement the computed-offsets rule and let the divergence report flag those rows for the user to confirm.
