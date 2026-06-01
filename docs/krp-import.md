# KRP importer

## Overview

The KRP importer reads Kanripo git repos and writes BKK bundles under
`<out>/<text-id>/`. Each repo is discovered either from a local
kanripo mirror on disk or cloned on demand from GitHub. No hand-written
recipe is required: the importer synthesises the recipe from the repo's
branch list and `Readme.org`.

## Quick reference

```bash
# single text from a local kanripo mirror
bkk import --format krp --in /data/krp --text-id KR3a0013 --out /data/out

# single text fetched from github.com/kanripo/<id>
bkk import --format krp --text-id KR3a0013 --out /data/out

# every text under a subsection prefix (prompts for confirmation)
bkk import --format krp --in /data/krp --section KR3a --out /data/out --yes

# all of KR3 (all subsections), excluding KR6
bkk import --format krp --in /data/krp --out /data/out \
           --exclude-section KR6 --yes

# resume an aborted bulk merge run (skip already-merged, redo TLS-only)
bkk import --format krp --in /data/krp --section KR3a --out /data/out \
           --on-exists skip-merged --yes
```

## Source layouts

### Local kanripo mirror (`--in`)

Pass the parent of the per-section directories:

```
<in>/
  KR3a/
    KR3a0001/   ← git clone
    KR3a0013/
  KR3b/
    KR3b0001/
```

`--in <root> --text-id KR3a0013` resolves in order:
1. `<root>/KR3a/KR3a0013/` (standard mirror layout)
2. `<root>/KR3a0013/` (flat layout)
3. recursive scan for an unambiguous match under `<root>`

### GitHub (`--github` / default)

When `--in` is absent the importer clones from
`github.com/<github-user>/<text-id>` and caches the working tree under
`<cache-dir>/<user>/<text-id>/`. A cached clone is refreshed when its
working tree is more than ~24 h old.

- `--github <user>` — GitHub user/org (default: `kanripo`)
- `--cache-dir <path>` — local clone cache (default: `~/.cache/bkk/krp`)

## Target selection

### Single text

```bash
bkk import --format krp --text-id KR3a0013 [--in <root>] --out <out>
```

`--text-id` and `--section` are mutually exclusive.

### Bulk by prefix (`--section`)

```bash
bkk import --format krp --in <root> --section KR3a --out <out>
```

`--section` is a **prefix filter** applied to text ids. A four-character
prefix like `KR3a` selects one subsection. A three-character prefix like
`KR3` selects the whole top-level section (all subsections whose ids
start with `KR3`).

Resolution for a local mirror:

1. Looks for `<in>/<prefix>/` as a direct section directory and scans its
   immediate children.
2. If that directory does not exist, falls back to a recursive walk
   filtering by `text_id.startswith(prefix)`.

### Excluding a section (`--exclude-section`)

```bash
bkk import --format krp --in <root> --out <out> --exclude-section KR6
```

Applied after `--section` filtering. Any text whose id starts with the
given prefix is dropped before the confirmation prompt. Useful when
importing a large range while skipping a known-bad or not-yet-ready
section.

### Whole corpus

Omitting both `--text-id` and `--section` walks the entire `--in` tree.

### Confirmation prompt

Bulk runs (more than one text) always prompt:

```
about to import 412 text(s):
  KR3a0001  (/data/krp/KR3a/KR3a0001)
  ...
Import 412 texts? [y/N]
```

Pass `--yes` (or set `global.skip_confirm: true` in `.bkkrc`) to
suppress the prompt.

## Output layout

```bash
# flat: <out>/<text-id>/
bkk import --format krp --in /data/krp --text-id KR3a0013 --out /data/out

# sectioned: <out>/<section>/<text-id>/
bkk import --format krp --in /data/krp --out /data/out --by-section
```

`--by-section` places each bundle under a per-subsection subdirectory
(`KR3a/KR3a0013/`), which avoids crowding a single output directory
when importing a large corpus. The same flag is honoured by the TLS and
CBETA importers.

## Merge behavior

When a bundle already exists at the target path the importer inspects its
state before writing:

| Existing state | How detected | Default action |
|---|---|---|
| `empty` | no master manifest | import as KRP |
| `krp` | master manifest has `entity_encoding`, no `.source.yaml` sidecar | re-import (overwrite) |
| `tls` | `.source.yaml` sidecar present | **merge**: demote KRP master to an edition |
| `unknown` | manifest present but unclassifiable | **error** — inspect or run `bkk repair manifest` |

A merged TLS+KRP bundle keeps state `tls` because the TLS surface (master
manifest, juan YAMLs, `.source.yaml`) is preserved. The KRP content lands
under `editions/krp/` and the master manifest's `editions:` list is
extended with the new edition shorts. TLS-owned edition shorts are
protected — the importer refuses to overwrite them even if the KRP source
happens to share the same short.

### `--on-exists`

Controls what happens when an existing bundle is found:

| Value | Behaviour |
|---|---|
| `overwrite` | **(default)** overwrite KRP bundles; merge into TLS bundles |
| `skip` | skip every existing bundle (`krp` and `tls` states alike) |
| `skip-merged` | skip KRP bundles and TLS bundles that already have KRP editions; still merge into TLS-only bundles |

`skip-merged` is designed for resuming an aborted bulk merge run: texts
whose KRP editions were already merged are left alone, while texts that
exist only as TLS bundles (KRP not yet merged) are still processed.

Detection of "already merged" for `skip-merged`: a TLS bundle's
`editions:` list is empty when it has never had KRP content merged into
it, and non-empty afterwards. The flag exploits this to distinguish the
two cases without a separate state.

## Branch conventions

The importer reads a repo's branch list and classifies each branch:

- **master branch** (default: `master`) — carries the curated master text.
- **imglist branch** (default: `_data`) — carries `imglist` and `imginfo`
  data for page-image linking.
- All other non-underscore branches are treated as **edition** branches.

Override with `--master-branch` / `--imglist-branch` if a repo deviates
from the convention.

## `.bkkrc` defaults

All KRP import options can be pinned in `.bkkrc` so they need not be
repeated on every invocation:

```yaml
global:
  krp_root: /data/krp       # feeds --in when format=krp
  skip_confirm: true         # suppress bulk confirmation prompts

import:
  format: krp
  out: /data/bkk/out
  by_section: true
  on_exists: skip-merged
  cache_dir: ~/.cache/bkk/krp
  github: kanripo
  master_branch: master
  imglist_branch: _data
```

Precedence (lowest → highest): `.bkkrc` < environment variables < CLI flags.

## Critical files

- [module/bkk/importer/cli.py](../module/bkk/importer/cli.py) —
  argument parser, `_run_krp`, `_resolve_targets`, `_import_one`,
  `_skip_filter_krp_pairs`; `--section`, `--exclude-section`,
  `--on-exists` logic lives here.
- [module/bkk/importer/source.py](../module/bkk/importer/source.py) —
  `list_local_text_ids`, `list_github_text_ids`, `resolve_local_repo`,
  `resolve_github_repo`, `synthesize_recipe`.
- [module/bkk/importer/read/krp.py](../module/bkk/importer/read/krp.py)
  — KRP reader: parses juan text, edition branches, PUA entity encoding,
  master/witness marker merging.
- [module/bkk/importer/write/merge.py](../module/bkk/importer/write/merge.py)
  — `inspect_existing_bundle` (state detection), `extend_master_editions`,
  `project_krp_apparatus_onto_tls` (the TLS+KRP merge path).
- [module/tests/test_krp_cli.py](../module/tests/test_krp_cli.py) —
  CLI-level coverage for single-text, bulk `--section`, `--on-exists`.
