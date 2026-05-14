# Recipe-less KRP imports + github source + bulk corpus traversal

## Context

The KRP importer today refuses to run without a hand-edited `recipes/<text-id>.yaml` that pins:

- `source.repo` (local clone path)
- `source.editions: [{branch, short}, ...]`
- `source.master.branch` (always `master`)
- `source.master.witnesses` (always the documentary edition shorts)
- `source.imglist.branch` (always `_data`)
- `source.imglist.path` (always `imglist/{text_id}_{NNN}.txt`)
- `metadata.title`, `metadata.date`

Almost all of that is derivable from the source repository itself (branch list, `Readme.org`, juan files, imglist path). The recipe is a sustainability tax that scales linearly with the corpus — unworkable for "import all KR3a*" or "import everything kanripo ships".

The TLS path is already recipe-less and runs on `--in / --out / --text-id`. KRP should follow the same shape, with three additions the user called out:

1. **Default source = github @kanripo.** Without `--in`, fetch from `https://github.com/kanripo/<text-id>` over the network. A different user/org can be selected with `--github <user>`.
2. **Local trees stay supported** with the kanripo mirror layout (`<root>/KR3a/KR3a0013/`).
3. **Bulk imports.** A `--section` flag selects every text under one corpus prefix (e.g. `KR3a`); omitting both `--text-id` and `--section` traverses the whole `--in` tree (or the whole org). Bulk modes require user confirmation.

Existing behavior to preserve:

- `--recipe <path>` keeps working (tests rely on it; recipes encode the rare case where the user really does want to override defaults).
- `read_krp(recipe)` keeps its signature; the CLI synthesises a `Recipe` from flags.
- The bundle written to disk is unchanged.

## Decisions (locked via clarifying questions)

| Question | Choice |
|---|---|
| Github fetch tooling | `git clone` + new `requests` dep for org listing (`/orgs/<user>/repos`). |
| Bulk confirmation | Print the discovered text-id list, prompt once `Import N texts? [y/N]`; skipped by `--yes`. |
| Local `--in` lookup | `<root>/<section>/<text-id>/` → `<root>/<text-id>/` → recursive search. |

## CLI surface

```
python -m bkk.importer --format krp [SOURCE] [SELECTOR] --out <path> [OPTIONS]

SOURCE (mutually exclusive; default: --github kanripo):
  --in <path>            local directory tree (clone or parent of clones)
  --github <user>        github user/org (default: kanripo) — fetched on demand

SELECTOR (mutually exclusive; default: traverse-all):
  --text-id <id>         single text (e.g. KR3a0013)
  --section <prefix>     all texts under a corpus prefix (e.g. KR3a)
  (none)                 every discoverable text under SOURCE — confirms first

OPTIONS:
  --out <path>           required: bundle written to <path>/<text-id>/
  --recipe <path>        legacy: full recipe overrides everything else
  --master-branch NAME   default: master
  --imglist-branch NAME  default: _data
  --cache-dir PATH       default: ~/.cache/bkk/krp ; github clones land here
  --yes                  skip the bulk confirmation prompt
```

## Discovery rules

For one text id `<TEXT>` (with corpus prefix `<TEXT[:4]>`):

1. **Repo location.**
   - `--in` set: `<in>/<prefix>/<TEXT>/`, then `<in>/<TEXT>/`, then a recursive `<in>/**/<TEXT>/` (first match wins; warn if ambiguous, like the TLS path already does in [cli.py:50-55](bkk/importer/cli.py#L50-L55)).
   - `--in` unset: `<cache-dir>/<github-user>/<TEXT>/` — clone with `git clone --bare-ish` if missing, `git fetch --all` if stale (mtime older than ~24h, or `--refresh`).
2. **Branches.** `git for-each-ref --format='%(refname:short)' refs/heads/` against the resolved repo. Strip `master`, `_data`, and any branch starting with `_` → that's the documentary-edition list. Edition `short` defaults to the branch name (override only via `--recipe`).
3. **Master branch.** Literal `master`, override `--master-branch`. Validates by checking the branch exists.
4. **Witnesses.** All documentary editions (the recipe's per-text witness pinning was always "all of them" in practice).
5. **Imglist.** `_data` branch, `imglist/{text_id}_{NNN}.txt` template — already the recipe defaults.
6. **Title / date / labels.** Title comes from the master branch's `Readme.org` `#+TITLE:` line; date from `#+DATE:`; edition labels from `Readme.org`'s `* 版本` table — `_load_edition_labels()` in [read/krp.py:106-131](bkk/importer/read/krp.py#L106-L131) already does the label work; extract the title/date the same way.

For `--section <PREFIX>` (e.g. `KR3a`):

- Local `--in`: list `<in>/<PREFIX>/*` directories, plus a fallback recursive scan filtering names by `^<PREFIX>` so flat-layout trees work too.
- Github: GET `https://api.github.com/users/<user>/repos?per_page=100` (paginated), filter `name.startswith(prefix)`. Print the list, prompt y/N (unless `--yes`), then loop.

For traverse-all (no selector):

- Local `--in`: walk every directory that *looks like* a KRP clone (contains `.git/` and a `master` ref). Print list, confirm.
- Github: list all repos under `<user>`, confirm.

## Module layout

New files:

- [bkk/importer/source.py](bkk/importer/source.py) — source resolution. Functions:
  - `resolve_local_repo(in_root: Path, text_id: str) -> Path` (3-step lookup above).
  - `resolve_github_repo(user: str, text_id: str, cache: Path) -> Path` (clone-or-fetch).
  - `list_local_text_ids(in_root: Path, prefix: str | None) -> list[str]`.
  - `list_github_text_ids(user: str, prefix: str | None) -> list[str]` (uses `requests`).
  - `discover_branches(repo: Path) -> list[str]` (`git for-each-ref`).
  - `synthesize_recipe(repo: Path, text_id: str, master_branch: str, imglist_branch: str) -> Recipe`.

Modified files:

- [bkk/importer/cli.py](bkk/importer/cli.py)
  - Add the new flags. `_run_krp` becomes:
    1. If `--recipe` given → load it as today (legacy path, no behavior change).
    2. Else, resolve `(source-kind, selector)` → list of `(text_id, repo_path)` pairs.
    3. For bulk, prompt `Import N texts? [y/N]` unless `--yes`.
    4. For each pair, build a Recipe via `synthesize_recipe()` and run the existing read/write loop unchanged.
  - Reuse the existing TLS-side find-and-warn helper pattern for ambiguous local matches.
- [bkk/importer/recipe.py](bkk/importer/recipe.py)
  - No schema changes. The dataclass stays as-is — `synthesize_recipe()` builds one programmatically.
- [bkk/importer/read/krp.py](bkk/importer/read/krp.py)
  - Extend `_load_edition_labels` (or add a sibling `_load_readme_metadata`) to also surface `#+TITLE:` and `#+DATE:` so the synthesized recipe can fill `metadata.title` / `metadata.date`. Keep the existing function's return signature for backward compatibility; add a separate helper.
- [pyproject.toml](pyproject.toml)
  - Add `requests` to `dependencies`.

Tests:

- [tests/test_krp_source.py](tests/test_krp_source.py) — new. Cover `resolve_local_repo` (all three lookup orders), `discover_branches` against a fixture repo, and `synthesize_recipe` (asserts the resulting Recipe round-trips through `read_krp`).
- [tests/test_krp_cli.py](tests/test_krp_cli.py) — new. End-to-end: invoke `cli.run([...])` with `--in input/krp --text-id KR3a0013 --out tmp` and check the bundle lands. Skip if `input/krp/KR3a0013` is absent (matches existing skip pattern in [tests/test_krp_roundtrip.py:34-38](tests/test_krp_roundtrip.py#L34-L38)).
- Github fetch + bulk confirmation paths are not covered by automated tests in v1 — gated on network and TTY. The plan's verification section exercises them manually.

Recipes:

- Keep [recipes/KR3a0013.yaml](recipes/KR3a0013.yaml) and [recipes/KR6q0053.yaml](recipes/KR6q0053.yaml) as documented examples for the legacy override path. Don't delete — round-trip tests still consume them.

## Implementation sequence

1. **Recipe synthesis from a local repo.** Add `bkk/importer/source.py` with `resolve_local_repo()`, `discover_branches()`, `synthesize_recipe()`. Wire into `cli._run_krp` as the no-recipe path (`--in` + `--text-id`). Keep `--recipe` working unchanged. Add `tests/test_krp_source.py`.

2. **Readme metadata extraction.** Extend `read/krp.py` so the title/date pulled from `Readme.org` populate the synthesized recipe's `metadata`. Today the recipe carries them explicitly; once auto-extracted, they're optional inputs.

3. **Local section + bulk traversal.** Add `list_local_text_ids()`. Add `--section` and confirmation prompt to `cli`. Loop synthesis + read/write per text. Add `--yes` for non-interactive runs.

4. **Github source.** Add `requests` dep. Add `resolve_github_repo()` (`git clone` to cache dir; `git fetch` if stale) and `list_github_text_ids()` (org repos via API). Wire `--github` flag and the "no `--in`" default. Network path stays manual-only for tests.

5. **Docs.** Update the docstring at the top of [cli.py:1-22](bkk/importer/cli.py#L1-L22) to describe the new invocation shapes; mention `--recipe` as the legacy override.

## Verification

1. **Single local text, recipe-less:**
   `python -m bkk.importer --format krp --in input/krp --text-id KR3a0013 --out output`
   → bundle at `output/KR3a0013/`, identical (modulo hashes that key off content) to today's recipe-driven output.

2. **Recipe still works:** `python -m bkk.importer --format krp --recipe recipes/KR3a0013.yaml` produces the same bundle.

3. **Section traversal (local):**
   `python -m bkk.importer --format krp --in input/krp --section KR3a --out output`
   → prompts `Import 1 texts? [y/N]`; on `y`, builds the same single bundle.

4. **Github single-text fetch:**
   `python -m bkk.importer --format krp --text-id KR3a0013 --out output`
   → clones `github.com/kanripo/KR3a0013` to `~/.cache/bkk/krp/kanripo/KR3a0013/`, then runs the import. Re-running uses the cache.

5. **Whole-corpus traversal confirmation:**
   `python -m bkk.importer --format krp --in input/krp --out output` (no selector)
   → lists discovered text-ids, prompts; refuses to run if not confirmed.

6. `pytest` — full suite green; new `tests/test_krp_source.py` and `tests/test_krp_cli.py` pass; existing recipe-driven tests still pass.

## Critical files

- [bkk/importer/source.py](bkk/importer/source.py) — NEW; resolution, discovery, recipe synthesis.
- [bkk/importer/cli.py](bkk/importer/cli.py) — wires the new flags; legacy `--recipe` path preserved.
- [bkk/importer/read/krp.py](bkk/importer/read/krp.py) — surface Readme.org title/date alongside the existing label parsing.
- [bkk/importer/recipe.py](bkk/importer/recipe.py) — unchanged schema; consumed by the synthesizer.
- [pyproject.toml](pyproject.toml) — add `requests`.
- [tests/test_krp_source.py](tests/test_krp_source.py) — NEW.
- [tests/test_krp_cli.py](tests/test_krp_cli.py) — NEW.

## Risks

- **Section prefix is brittle** — assumes the first 4 chars of every text-id match its corpus directory (`KR3a0013` → `KR3a`). Verified against current recipes and tests, but the rule should live in one helper (`section_prefix(text_id) -> text_id[:4]`) so a future format change is cheap.
- **Branch convention drift** — if a kanripo text uses a non-`master` master branch, default discovery silently picks no master. Mitigation: warn loudly when the default branch isn't found and recommend `--master-branch` or `--recipe`.
- **Github rate limits** — unauthenticated GitHub API allows ~60 requests/hour. Bulk org listing for kanripo (~hundreds of repos) needs pagination but stays under one walk = one pass. If rate-limited, the error surfaces in stderr; users can pass `GITHUB_TOKEN` env var which `requests` reads via the auth header (document this).
- **Cache staleness** — `git fetch` on every run is slow for large corpora. v1 fetches once; an explicit `--refresh` flag on a later iteration can force re-fetch.
- **`requests` is a new runtime dep** — small, ubiquitous, but adds a wheel to install. Alternative was `urllib`; the user picked `requests` for cleanliness.
- **Tests rely on `input/krp/KR3a0013`** — the new CLI tests skip if absent, matching the existing pattern. CI without that fixture still passes.
