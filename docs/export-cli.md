# Generic recipes + CLI overrides + corpus-walk for the exporter

## Context

A surface-edition-only KRP recipe is mostly format/shape/edition knobs —
the `bundle:` and `output_dir:` fields are per-text and turn it into a
single-use file. Generating ~9,200 near-identical recipes for a corpus
batch was unworkable. The fix mirrors the importer's recipe-less +
`--in`/`--out`/`--text-id`/`--section`/`--yes` shape (see
[docs/PLAN.md](PLAN.md)).

## What changed

1. **Every recipe field is optional in the file.** The loader parses
   what's present and validates internal consistency; `bundle`,
   `output_dir`, `format`, even `edition` for `shape: single` can all be
   omitted from the YAML. Unknown keys still raise.

2. **`apply_overrides(recipe, **kwargs)` layers CLI flags on top.** It
   accepts `recipe=None` (no file at all), CLI overrides win over recipe
   values, and the final consistency check (format/bundle/output_dir
   present, shape↔edition consistent, no krp-only options leaking into
   tls) runs once at dispatch time.

3. **The exporter CLI grew three modes.** `--recipe` is now optional;
   `--bundle`, `--output-dir`, `--corpus`, `--text-id`, `--section`,
   `--yes`, `--format`, `--shape`, `--edition`, `--mode` were added.

   - **Recipe-only (legacy)** — `bkk export --recipe <path>`. Behaves
     exactly as before.
   - **Single-with-overrides** — `bkk export [--recipe <template>]
     --bundle <path> --output-dir <path> [...]`. The recipe (if given) is
     a template; CLI flags fill in or override.
   - **Corpus walk** — `bkk export [--recipe <template>] --corpus <root>
     --output-dir <out-parent> [--text-id <id>|--section <prefix>]
     [--yes]`. For each bundle dir under `<root>` (predicate: contains
     `<dirname>.manifest.yaml`), the loop derives
     `bundle = <root>/<id>` and `output_dir = <out-parent>/<id>`,
     dispatches, and continues past per-bundle failures (warns to
     stderr, exit code 1 if any failed).

## Generic recipe shape

```yaml
# recipes/krp-surface-WYG.yaml
format: krp
shape: single
edition: WYG
mode: split
# bundle and output_dir intentionally omitted; supplied by CLI.
```

## Example invocations

```bash
# Single bundle, generic recipe + CLI paths:
bkk export --recipe recipes/krp-surface-WYG.yaml \
           --bundle module/output/KR3a0013 \
           --output-dir /tmp/wyg/KR3a0013

# Same template, every bundle in the corpus:
bkk export --recipe recipes/krp-surface-WYG.yaml \
           --corpus module/output \
           --output-dir /tmp/wyg-export

# A slice (just KR3a* texts), no confirmation:
bkk export --recipe recipes/krp-surface-WYG.yaml \
           --corpus module/output --section KR3a \
           --output-dir /tmp/wyg-export --yes

# No recipe at all, all-CLI:
bkk export --format krp --shape single --edition WYG \
           --bundle module/output/KR3a0013 \
           --output-dir /tmp/wyg/KR3a0013
```

## Per-bundle robustness

Some texts in the corpus won't have the requested edition (e.g. a
`single, edition: WYG` template against a bundle whose master only
declares `KZA`). In **corpus mode** the loop catches `RecipeError` and
any per-bundle exception, prints `error exporting <text-id>: ...` to
stderr, and continues. Single-bundle and recipe-only modes raise as
before — those are explicit user actions where fail-fast is wanted.

## Critical files

- [module/bkk/exporter/recipe.py](../module/bkk/exporter/recipe.py) —
  optional-everything schema; new `apply_overrides` +
  `_validate_executable`.
- [module/bkk/exporter/cli.py](../module/bkk/exporter/cli.py) — three
  execution modes; `_iter_bundle_dirs` corpus walker (mirrors the
  manifest-existence predicate from
  [tools/validate_corpus.py](../tools/validate_corpus.py)).
- [module/tests/test_recipe.py](../module/tests/test_recipe.py) — added
  coverage for partial recipes + every `apply_overrides` branch.
- [module/tests/test_exporter_cli.py](../module/tests/test_exporter_cli.py)
  — new file: 13 cases covering generic recipes, CLI-only
  invocation, corpus walk, filters, per-bundle skip, mutual-exclusion
  guards.

## Risks / follow-ups

- **`--text-id` and `--section` are independent**; they can be combined
  but typically aren't. No validation that prevents passing both — the
  filters AND together, so passing both narrows the set, which is
  arguably useful.
- **Confirmation prompt fires whenever `len(bundles) > 1`** — same
  threshold as the importer. If the corpus walk gets slow enough for
  users to invoke it casually, lifting that to a higher threshold or
  adding `--no-confirm` (alias of `--yes`) might be worth it.
- **No `--editions`/`--juans` flags yet.** The recipe still supports
  these, but they aren't surfaced on the CLI. Add when needed.
- **Recipe-less invocation only really helps for trivial shapes.** For
  anything beyond `format: krp` + defaults, a generic recipe is still
  the ergonomic choice — keeping the YAML around lets the format/shape
  knobs live with the source-tree, not in shell history.
