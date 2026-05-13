# CLI module for the BKK files


## Configuration file (`.bkkrc`)

Persistent defaults for any `bkk` subcommand can be stored in a YAML file
named `.bkkrc`.  Copy `.bkkrc.sample` (in this directory) to `~/.bkkrc` and
uncomment the settings you want.

### Search and merge order

`bkk` collects every `.bkkrc` it finds along the path from `~` down to the
current working directory and deep-merges them, with files closer to `cwd`
taking precedence:

```
~/.bkkrc            ← lowest priority  (credentials, shared paths)
~/work/.bkkrc
~/work/project/.bkkrc  ← highest priority  (project-specific overrides)
```

This lets you keep `serve.admin_token` in `~/.bkkrc` and override only
`serve.host` in a project-local file without repeating the token.

### Precedence

```
.bkkrc  <  env vars (BKK_*)  <  CLI flags
```

### Minimal example

```yaml
global:
  corpus: /data/bkk/corpus   # shared corpus root
  tls_root: /data/tls        # TLS source → bkk import --in (tls), bkk validate --tls-source
  krp_root: /data/krp        # KRP mirror  → bkk import --in (krp)
  skip_confirm: true         # skip bulk-op prompts (bkk import/export --yes)

import:
  format: krp
  cache_dir: ~/.cache/bkk/krp

serve:
  admin_token: changeme      # keep this in ~/.bkkrc, not in a project file
  port: 9000
```

> **Note:** Do not use bare `yes:` as a YAML key — YAML 1.1 parses it as a
> boolean.  Use `skip_confirm: true` instead (shown above).

### Section reference

| Section | CLI args affected |
|---|---|
| `global.corpus` | `bkk export --corpus`, `bkk index merge <corpus>`, `bkk serve --corpus` |
| `global.tls_root` | `bkk import --in` (when `format=tls`), `bkk validate --tls-source` |
| `global.krp_root` | `bkk import --in` (when `format=krp`) |
| `global.skip_confirm` | `bkk import --yes`, `bkk export --yes` |
| `import.*` | all `bkk import` flags; `import.in` overrides the global roots |
| `export.*` | all `bkk export` flags; `export.corpus` overrides `global.corpus` |
| `index.corpus` | overrides `global.corpus` for `bkk index merge` |
| `validate.tls_source` | overrides `global.tls_root` for `bkk validate --tls-source` |
| `serve.*` | all `bkk serve` flags; also read from `BKK_*` env vars (env beats rc) |
| `recipe.corpus` | corpus root for `bkk recipe render` |
| `info.corpus` / `info.index` | overrides for `bkk info` (fall back to `global.corpus` and `index.out`) |

See `.bkkrc.sample` for the full list of supported keys with inline comments.


## Import

Here is where the import is handled. We will have input from different sources, with varying shapes.  

A `read` module will read them in to an abstract shape and a `write` module will produce the required files where they belong. 

### Invocation of the input procedure

`bkk import --format <format> ` 

The format will be `krp` for the Kanseki Repository format, `tls` for the TLS / HXWD source-text format, and `translation` for TLS-shaped translation files (one TEI `type="transl"` document per language/revision; see [docs/translation-import.md](../docs/translation-import.md)).

### Archival format

The archival format is mainly for text data of premodern Chinese. Texts used to be transmitted in scrolls, *juan* in Chinese; this remains a useful subdivision and is still in use today. In our format, each juan is a separate file. A **manifest** plus a **table of contents** and additional metadata pertain to the whole text and point into the juan files. All `text` fields, no matter their location or type, are accompanied by a `hash` field whose value audits the content.

A **juan** file has a `front`, a `body`, and a `back`; only the body must be non-empty, the others are optional and need not be present if empty. Additional metadata fields are available. The text elements of the body and back may be subdivided where appropriate. A typical front contains an opening line that locates the juan in a larger collection, the title of the text, the sequential number of the juan, and an attribution naming persons and roles with respect to the body. The back contains a closing line. The placement of prefaces, postfaces, colophons, and similar paratextual material is open: such material may go into the body or be separated out into front or back, at the discretion of the project applying the format.

The body has one text element that holds the canonical character content of the whole juan. Space characters, punctuation, line breaks, and similar content are not present in this stream — they are extracted into a **markers** object that follows the text element. A marker has at minimum a **type** and an **offset**; further fields are optional and typically include **id**, **content**, and additional structured information appropriate to the marker's type. The set of marker types is open; a small core vocabulary is defined separately.

One text can be represented in several editions, they can be made accessible through the 'master' edition of the text or a recipe can adress them directly. 

A sample to illustrate the format has been prepared in samples/KR6q0053

### Input sources


#### Kanseki Repository

Mandoku-view source format. The text lives in a git repository whose branches
are editions: each text-bearing branch (e.g. `WYG`) carries one
`KR<id>_NNN.txt` file per juan, the `master` branch carries a curated reading,
and a sibling `_data` branch carries `imglist/<text-id>_NNN.txt` mapping
`<juan>-<page>` ids to image filenames plus `imglist/imginfo.cfg` (base URLs).

Per-text knobs (branch → edition mapping, master witnesses, imglist source)
are pinned in a recipe file:

```
python -m bkk.importer --format krp --recipe recipes/<text-id>.yaml
```

Each documentary edition is written under `<out>/<text-id>/editions/<short>/`;
the master edition is written at the bundle root with a `PUA-map.yaml`
summarising every Kanripo `&KRnnnn;` private-use-area character that appears
in any edition (codepoint = `0x105000 + nnnn`). Where the master differs from
a witness, a `variant` marker is emitted on the master juan.

See [recipes/KR3a0013.yaml](recipes/KR3a0013.yaml) for the schema.

#### TLS / HXWD

XML format used in the TLS application.  In the application, texts are in subdirectories of `tls-texts/data/`, sources for the annotations in `tls-data/notes/swl` and `tls-data/notes/doc` 

See `input/tls` for the text files that will be used to produce the output, to be written to `output/`

#### TLS translations

Companion translations of TLS source texts live under
`<tls-root>/tls-data/translations/` as one TEI `type="transl"` file per
(text-id, language[, revision]), e.g. `KR1h0004-en.xml` or
`KR1h0004-fr-138ffefe.xml`. Each `<seg corresp="#KR…_tls_<location>">`
pins one translation segment to a source marker; empty segs are dropped.

```
python -m bkk.importer --format translation \
       --in <tls-root> --out <out-root> \
       [--text-id KR1h0004] [--lang en]
```

Each file becomes its own bundle under `<out>/translations/<stem>/` —
snapshots (`-<rev>` suffix) coexist alongside the un-suffixed bundle.
See [docs/translation-import.md](../docs/translation-import.md) for the
output layout and per-segment span shape.

#### CBETA

TBD

#### Plain text files

TBD

### Output layout

By default each bundle is written at `<out>/<text-id>/`. Pass
`--by-section` (or set `import.by_section: true` in `.bkkrc`) to slice
the output into 4-character prefix buckets — bundles land at
`<out>/<section>/<text-id>/` instead, e.g. `KR6d/KR6d0001/`. Useful for
large corpora to avoid overloading file browsers with thousands of
sibling directories. `bkk index merge` and `bkk export --corpus`
discover bundles in either layout (or a mix of both).

## Index

The indexer turns a bundle (or a whole corpus root) into a portable SQLite
artifact (`.bkkx`) that supports variant-aware substring search with KWIC
output. Queries match against the master text *and* against each per-edition
witness reading, so a character that appears only in a witness still finds
the master position.

The artifact is plain SQLite — the same file can be loaded by `sql.js` in
the browser to drive static-site search.

### Per-bundle build

```
python -m bkk.index build <bundle_dir> [--out PATH]
```

Walks `<bundle_dir>/<textid>_NNN.yaml` plus the manifest and writes
`<bundle_dir>/<textid>.bkkx` (or `--out PATH`).

### Corpus-level merge

```
python -m bkk.index merge <corpus> [--out PATH]      # default: <corpus>/_corpus.bkkx
                          [--prefix KR3a]   # restrict to one subgroup
                          [--rebuild]       # rebuild every per-bundle .bkkx
                          [--no-build]      # error if any per-bundle .bkkx is missing/stale
```

`<corpus>` falls back to `index.corpus` / `global.corpus` from `.bkkrc`;
`--out` falls back to `index.out` from `.bkkrc`, else `<corpus>/_corpus.bkkx`.

Walks `<corpus>` for `<textid>/<textid>.manifest.yaml`, descending one
level for sectioned layouts (`<corpus>/<section>/<textid>/`) produced by
`bkk import --by-section`. Builds any missing or stale per-bundle `.bkkx`
(mtime check against the manifest and juan files), then unions every
per-bundle index into one merged `.bkkx`. Primary keys are shifted per
source so they remain unique in the merged file. The merged artifact
carries a `bundle` provenance table (textid, editions, source path,
sha256 of the source `.bkkx`).

### Search

```
python -m bkk.index search <bkkx_path> <query> [--context N]
                                               [--witness LABEL]...
                                               [--textid ID]
```

Plain CJK substring; matching is variant-aware. `--witness` (repeatable)
restricts witness-side matches; master matches are always returned.
`--textid` scopes to one bundle inside a merged corpus index. Output is one
KWIC line per hit, with any variant readings overlaid; the variant that
contains the matched span is flagged with `*`.

Example (variant query against a merged corpus):

```
$ python -m bkk.index search corpus.bkkx 甞不盡 --context 8
KR1a0024:001/body@24307  [卷一]  via SBCK='甞不盡'
  …以決勢足以專然未「嘗不盡」天下之議上九視履…
  * variant @24307 len=1 '嘗' → SBCK='甞'
```

### Python API

```python
from bkk.index import Index, build_index, merge_bundles

build_index("path/to/KR1a0024")                       # writes KR1a0024.bkkx
merge_bundles("/path/to/corpus", "corpus.bkkx")       # builds + merges

with Index("corpus.bkkx") as ix:
    for hit in ix.search("甞不盡"):
        print(hit.textid, hit.master_offset, hit.matched_via)
```

## Validation

The validator checks a bundle on disk against the structural and field-level
constraints documented under `Archival format`. It does not recompute or
verify hashes — only their format (`sha256:<64-hex>`) is checked.

### CLI

```
python -m bkk.validator <bundle-dir>           # human-readable report
python -m bkk.validator --json <bundle-dir>    # JSON report
```

Exit codes: `0` if no error-severity findings (warnings allowed), `1` if any
error is present, `2` for bad invocation (e.g. path is not a directory).

### Severity

Two tiers: `error` (the bundle violates a hard constraint) and `warning`
(suspicious but not strictly invalid — e.g. PUA codepoint counts that drift
from the master text). Warnings do not fail the exit code.

### Python API

```python
from bkk.validator import validate_bundle

report = validate_bundle("path/to/KR3a0013")
print(report.render_text())
for f in report.findings:
    print(f.rule_id, f.severity, f.path, f.message)
if report.has_errors:
    ...
```

### Rule catalog

Rules are grouped by concern; each emits findings under a stable `rule_id`
(used for grouping in text output and as the JSON key).

| Module | Concern | Examples |
|---|---|---|
| `rules/filesystem.py` | file presence, edition mirroring | `MANIFEST_MISSING`, `EDITION_JUAN_COVERAGE` |
| `rules/manifest.py`   | manifest field shapes, TOC spans | `CANONICAL_IDENTIFIER_FORMAT`, `TOC_REF_SPAN_BOUNDS` |
| `rules/juan.py`       | juan buckets, NFC, markers | `JUAN_TEXT_NFC`, `JUAN_MARKER_ID_FORMAT` |
| `rules/ann.py`        | TLS annotations | `ANN_SEG_ID_RESOLVES`, `ANN_OFFSET_BOUNDS` |
| `rules/pua.py`        | PUA-map cross-checks | `PUA_ENTRY_KR_CODEPOINT_MATCH`, `PUA_COUNT_MATCHES_TEXT` |

The `Report` collapses runaway repetitions of the same `(rule_id, path)` pair
after five findings to keep output scannable.

### Updating rules

To **change an existing rule**, edit the relevant module under
`bkk/validator/rules/` and adjust the `ctx.report.add(rule_id, severity,
path, message)` call. Severity is `"error"` or `"warning"`. Keep `rule_id`
stable — downstream tooling and the test suite key on it.

To **add a rule** to an existing section, write the check inside the
section's `run(ctx)` (or a helper it calls) and emit findings via
`ctx.report.add(...)`. Pick a new `rule_id` that fits the section's prefix
(`MANIFEST_*`, `JUAN_*`, …).

To **add a whole new section**, drop a new module under `rules/` exposing a
`run(ctx) -> None` function, then append it to the `_MODULES` tuple in
`rules/__init__.py`. Modules run in `_MODULES` order; later rules already
tolerate missing/broken files because earlier ones flag them and set
context flags.

Each rule should be **independent** where possible: emit a finding and
continue, rather than raising. The `ValidationContext` (`context.py`) holds
parsed manifests, juans, editions, and the running `Report`; reuse those
rather than re-reading files.

To **test a new rule**, add a case to `tests/test_validator.py`. The pattern
is: take a `bundle_copy` (per-test copy of a freshly imported bundle),
mutate it to provoke the rule, call `validate_bundle`, and assert the
expected `rule_id` appears in the report.

### Possible extension: pluggable YAML/JSON schema

The current rules are pure Python because most checks are cross-file
(span bounds resolved against the referenced juan's text length, marker ids
that must agree with the surrounding filename, PUA counts cross-summed
against master text). These do not fit a declarative schema cleanly.

For the *shape*-level checks (required keys, scalar types, enum values,
regex-constrained strings) a JSON Schema layer could be slotted in as one
more rule module without changing the rest of the pipeline:

1. Place schemas under `bkk/validator/schemas/` — one per file kind, e.g.
   `manifest.schema.json`, `juan.schema.json`, `ann.schema.json`,
   `pua-map.schema.json`.
2. Add `rules/schema.py` exposing `run(ctx)` that, for each loaded file in
   the `ValidationContext`, picks the matching schema and runs it (using
   `jsonschema` from PyPI) against `lf.data`. Emit findings under a stable
   `SCHEMA_<kind>` `rule_id` so they group separately from hand-written
   rules.
3. Register `schema` in the `_MODULES` tuple in `rules/__init__.py`,
   ideally after `manifest`/`juan`/`ann` so schema findings appear after
   the targeted ones.
4. Move only the redundant shape checks (`MANIFEST_REQUIRED_KEYS`,
   `JUAN_REQUIRED_KEYS`, `ANN_REQUIRED_KEYS`, simple enums) into the
   schemas. Leave cross-file checks in Python.

Tradeoff: schemas are easier to read and edit by non-Python contributors
and travel well as a public spec, but they cannot express constraints that
need a second file's content. Treat them as a complement to the Python
rules, not a replacement.

## Info

`bkk info` prints a quick orientation summary: where the corpus and index
live, how many bundles are present, what the index contains, and which
`.bkkrc` files / sections are currently in effect.

### CLI

```
bkk info                          # corpus + index + config summary
bkk info --corpus PATH            # inspect a different corpus
bkk info --index PATH             # point at a non-default .bkkx
bkk info --bundles                # append a per-bundle table
bkk info --prefix KR1a            # filter the bundle table; implies --bundles
bkk info --json                   # machine-readable output
```

Defaults follow the usual chain: `--corpus` falls back to `[info].corpus`
then `[global].corpus`; `--index` falls back to `[info].index` then
`[index].out` then `<corpus>/_corpus.bkkx`.

The output is split into three (or four, with `--bundles`) sections:

- **corpus** — path, bundle count, breakdown by 3-char section prefix
  (`KR1`, `KR3`, …).
- **index** — schema version, on-disk size, per-table row counts
  (`bundle`, `juan`, `bucket`, `witness`, `variant`, `voice_range`, `toc`,
  `trigram`), available voices, and how many per-bundle `.bkkx` files are
  stale relative to their sources.
- **config** — the `.bkkrc` files that contributed (low → high precedence)
  and the merged values for the `global`, `info`, and `index` sections.
- **bundles** (optional) — one row per bundle with juan / bucket / witness
  counts pulled from the merged index. When no merged index exists, only
  textid and editions (parsed from each manifest) are shown.

Exit code is `0` on success; `2` if neither `--corpus` nor a corpus default
is available.
