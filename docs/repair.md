# Manifest rebuild for multi-XML-file TLS texts

## Context

A handful of TLS texts are split across multiple TEI XML files whose
`xml:id` ends in a lowercase letter (`KR2b007a.xml`, `KR2b007b.xml`,
…). Each sub-file's `<idno type="kanripo">` points at the same canonical
text id (`KR2b0007`), so all sub-files belong to the same BKK bundle.
Juan numbering is globally sequential and non-overlapping across the
sub-files.

The importer reads the canonical id at
[`module/bkk/importer/read/tls.py`](../module/bkk/importer/read/tls.py)
(`_parse_metadata`), so each sub-file's bundle is keyed by the same
`text_id` and writes its juan files into the same `<out-root>/<id>/`
directory. Juan files coexist on disk without conflict, but
`write_bundle`
([`module/bkk/importer/write/bundle.py`](../module/bkk/importer/write/bundle.py))
unconditionally **overwrites** both the master manifest
(`<id>.manifest.yaml`) and the per-edition manifest
(`editions/<short>/<id>-<short>.manifest.yaml`) on every run — no merge,
no read-before-write. After a bulk import the manifests therefore only
list the *last* sub-file's juans; earlier sub-files' juan YAMLs sit on
disk orphaned.

The remedy is a manual maintenance command that scans an existing
bundle directory and regenerates both manifests from the juan files on
disk. The importer was originally unchanged; auto-detection at import
time was deferred. **Update:** the TLS importer now resolves a
canonical ``--text-id`` (e.g. ``KR2b0007``) to the set of letter-
suffix sub-files that declare it via ``<idno type="kanripo">``, imports
each, and runs ``rebuild_manifests`` on the resulting bundle so the
manifest reflects every part. ``bkk repair manifest`` remains for
fixing bundles produced by an older importer or by a corpus-wide bulk
run that does not pass ``--text-id``.

## What changed

A new top-level subcommand:

```
bkk repair manifest <bundle-dir-or-text-id> [--out <root>]
```

- The bundle argument may be a path or a bare text id. A bare id is
  resolved against (in order) `repair.out`, `import.out`, or
  `global.corpus` from `.bkkrc`. `--out` overrides all three.
- Rebuilds the master manifest and every per-edition manifest under the
  resolved bundle directory.
- `assets.parts` is rebuilt from `<id>_NNN.yaml` (master) and
  `<id>_NNN-<short>.yaml` (each edition). Each part's hash is read
  directly from the juan YAML's `hash:` field (master juans are
  byte-copies of edition juans, so the stored hash is already correct).
- `assets.annotations` (master only) is rebuilt from
  `<id>_NNN.ann.yaml`.
- `table_of_contents` is reconstructed from the markers stored in each
  juan's `front`/`body`/`back` buckets:
  - **Classic TLS:** one `type: section, level: 1` entry per `tls:head`
    marker; span = `[bucket, head_offset,
    next_head_offset_or_len(text)]`. Headless leading regions are
    skipped — lossy compared to the original importer shape, but every
    real TLS `<div type="juan">` carries a `<head>`, so the reconstructed
    TOC matches the importer-emitted one for the case this command
    exists for.
  - **CBETA-flavor:** one `type: juan` entry per `cbeta:juan-start`
    marker (label = `extras.jhead`), one `type: mulu` point entry per
    `cbeta:mulu` marker. Mirrors `_build_toc_cbeta` in `bundle.py`.
- `metadata`, `canonical_identifier`, `canonical_location`,
  `canonical_set`, `entity_encoding`, and the master-only top-level
  `editions:` list are carried over from the existing manifest (these
  don't change between sub-files of the same canonical text).

`build_manifest` in
[`module/bkk/importer/write/bundle.py`](../module/bkk/importer/write/bundle.py)
is the same function the importer uses; the repair module calls it
directly so the manifest shape stays in lockstep with importer output.

## Files

- [`module/bkk/repair/__init__.py`](../module/bkk/repair/__init__.py)
- [`module/bkk/repair/manifest.py`](../module/bkk/repair/manifest.py) —
  `rebuild_manifests(bundle_dir)` + helpers.
- [`module/bkk/repair/cli.py`](../module/bkk/repair/cli.py) —
  argparse + `.bkkrc` resolution.
- [`module/bkk/cli.py`](../module/bkk/cli.py) — registers `repair` in
  `SUBCOMMANDS`.
- [`module/bkk/config.py`](../module/bkk/config.py) — `repair` added to
  `_VALID_SECTIONS`.
- [`module/.bkkrc.sample`](../module/.bkkrc.sample) — sample
  `[repair]` section.
- [`module/tests/test_repair_manifest.py`](../module/tests/test_repair_manifest.py)
  — 8 tests covering the bug reproduction, master/edition rebuild,
  metadata carryover, and `.bkkrc` resolution.

## Example invocations

```bash
# Explicit path:
bkk repair manifest /home/Shared/bkk/bkkbooks/KR2b0007/

# Bare text id, resolved against import.out (or repair.out / global.corpus):
bkk repair manifest KR2b0007

# Override the resolution root for one invocation:
bkk repair manifest KR2b0007 --out /alt/corpus/root
```

After running, `bkk validate <bundle-dir>` should report no manifest-
or part-hash errors.

## Out of scope

- The `<id>.source.yaml` sidecar is also overwritten per sub-file. It
  is not part of the manifest hash chain and the user has not flagged
  it as a problem; left untouched.
