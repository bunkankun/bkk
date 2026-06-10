# Codeberg migration feasibility

## Context

`bunkankun.org` currently depends on GitHub for login, per-user workspace storage, core-record editing via fork+PR, and team-based authorization. The question is whether Codeberg (Forgejo) can cover all of these and what would actually break or need rework. This document is an assessment, not an implementation plan.

## What we use GitHub for today

Inventory drawn from the running webapp (developer-tool use of `gh`/`git` for KRP imports is out of scope unless noted):

1. **OAuth login** — [module/bkk/serve/routers/auth.py:303-395](../module/bkk/serve/routers/auth.py#L303-L395). Scopes `repo`, `read:user`, `read:org`.
2. **Team membership check** — `GET /orgs/{org}/teams/{slug}/memberships/{login}` at [auth.py:267-287](../module/bkk/serve/routers/auth.py#L267-L287). Drives `bunkankun/bkk-admin` and `bunkankun/bkk-editor` gating.
3. **Workspace bootstrap** — generate-from-template + branch creation, [auth.py:162-264](../module/bkk/serve/routers/auth.py#L162-L264). Each user gets a private repo from `bunkankun/BKK-Workspace`.
4. **Workspace file I/O** — Contents API GET/PUT under `settings/`, `notes/`, `searches/`, `lists/` in [module/bkk/serve/routers/workspace.py](../module/bkk/serve/routers/workspace.py).
5. **Core edit fork+PR flow** — fork upstream, branch, PUT/DELETE contents, open PR, in [module/bkk/serve/routers/core_edit.py:162-240](../module/bkk/serve/routers/core_edit.py#L162-L240).
6. **Importer repo discovery** — `GET /users/{user}/repos` paginated, in [module/bkk/serve/routers/source.py:300-336](../module/bkk/serve/routers/source.py#L300-L336). Optional `GITHUB_TOKEN`.
7. **Source pull on admin update** — plain `git fetch && git merge --ff-only` on a local clone (not API).

Not used: GraphQL, GitHub Apps, Actions, webhooks, Pages, Releases, Discussions, Gists, submodules.

## Per-feature coverage on Codeberg/Forgejo

| Feature | Codeberg/Forgejo coverage | Notes |
|---|---|---|
| OAuth2 login | Native | Different scope names; no `repo`/`read:org` — use `read:user`, `read:organization`, `write:repository`. |
| User profile fields (`login`, `name`, `avatar_url`, `html_url`) | Yes | Same field names in Forgejo `/user`. |
| Generate from template | Yes | `POST /repos/{owner}/{repo}/generate` exists in Forgejo. |
| Fork | Yes | `POST /repos/{owner}/{repo}/forks`. |
| Contents GET/PUT/DELETE with SHA | Yes | Same shape, base64 content, optimistic SHA locking. |
| Git refs (create/get) | Yes | `git/refs/...` endpoints present. |
| Pull requests (create/list) | Partial | No `head={owner}:{branch}` query filter — must list and filter client-side. |
| Team membership check | Different shape | No `/orgs/{org}/teams/{slug}/memberships/{login}`. Use `GET /orgs/{org}/teams/search?q={slug}` → team id, then `GET /teams/{id}/members/{username}`. Two calls instead of one. |
| List user/org repos | Yes | `GET /users/{u}/repos`, `GET /orgs/{o}/repos`. |
| Auth header | Differs | `token <t>` rather than `Bearer <t>`; no `X-GitHub-Api-Version`. |
| API base URL | Differs | `https://codeberg.org/api/v1` vs. `https://api.github.com`. |

## Real functional gaps

1. **Codeberg ToS / FOSS expectation.** Codeberg's terms restrict the platform to free/libre software and related content. The current model creates a **private per-user workspace fork** for every logged-in user, holding personal notes/searches/lists. This is the biggest non-technical blocker: it likely doesn't fit Codeberg's mission and could trigger pushback at any scale. **Mitigations:** self-host Forgejo (you get the same API surface without the ToS issue), or move workspace storage off-Forge entirely (sqlite/Postgres on the server) and use Codeberg only for the *core* editing PR flow, which is genuinely FOSS-aligned.
2. **Team membership API requires an extra hop.** Forgejo needs team-id resolution before checking membership. Trivial code change, but the team-slug → id lookup result should be cached.
3. **PR `head` filter not supported.** Have to fetch all open PRs and filter for `head.repo.owner == user && head.ref == branch` client-side. Fine at current scale, watch as PR count grows.
4. **OAuth scope semantics differ.** Need to register a new OAuth app on Codeberg and pick the right scopes; no 1:1 mapping with GitHub's `repo`/`read:org`.
5. **Identity migration.** Existing users are keyed by GitHub `login`. After migration, sessions are keyed by Codeberg `login`. Either: (a) cut over hard and ask users to re-bootstrap workspaces, or (b) maintain a `github_login → codeberg_login` mapping. There's no SSO bridge.
6. **Importer repo discovery.** KRP source repos live in the GitHub `kanripo/` org. Codeberg won't have them unless mirrored. The importer either keeps talking to GitHub (dual-vendor) or you mirror KRP to Codeberg and adjust `.bkkrc` `import.github`.
7. **Token-bearing clones.** `git clone https://github.com/...` in [source.py:250](../module/bkk/serve/routers/source.py#L250) becomes a Codeberg URL; same shape, but private repo clones use a Codeberg token in the URL.

## Non-gaps (things that just work)

- All Contents API operations the webapp uses.
- Fork + branch + commit + PR happy path.
- OAuth login itself.
- Generate-from-template.
- The PUA codepoint / marker-id logic, nothing GitHub-specific there.

## Recommended shape, if you proceed

Cleanest split is **two-vendor**: keep GitHub for the public core repo + KRP imports (where the open-source community already lives), introduce a `Forge` abstraction that hides the GitHub-vs-Forgejo API differences, and either:

- **(a) Self-host Forgejo** for the per-user workspace storage — sidesteps the Codeberg ToS issue and gives you a Codeberg-compatible API to test against; or
- **(b) Move workspace storage out of git entirely** to a server-side database, and use Codeberg only for the core-edit PR flow.

(b) eliminates the largest blocker (private user repos on Codeberg) and is probably less code than (a) once the workspace router is rewritten.

## Files that would change in a migration

- [module/bkk/serve/routers/auth.py](../module/bkk/serve/routers/auth.py) — OAuth endpoints, team membership call, workspace bootstrap.
- [module/bkk/serve/routers/workspace.py](../module/bkk/serve/routers/workspace.py) — Contents API calls (or removed entirely under option (b)).
- [module/bkk/serve/routers/core_edit.py](../module/bkk/serve/routers/core_edit.py) — fork + PR flow.
- [module/bkk/serve/routers/source.py](../module/bkk/serve/routers/source.py) — importer repo discovery + clone URLs.
- [module/bkk/serve/config.py](../module/bkk/serve/config.py) — new env vars, scope strings, callback URL.
- [module/bkk/serve/state.py](../module/bkk/serve/state.py) — `UserSession` if identity model changes.
- [docs/production-server-setup.md](production-server-setup.md), [docs/web.md](web.md), `.bkkrc.sample` — config docs.
- New: thin `forge.py` abstraction with `GithubForge` and `ForgejoForge` adapters.

## Verification

End-to-end smoke tests against a Forgejo instance (Codeberg or self-hosted):

1. OAuth round-trip lands a session with `login`, `name`, `avatar_url`.
2. Team membership check returns true/false correctly for a known editor/admin.
3. New user → workspace repo generated (or, under (b), DB row created) → settings file PUT/GET round-trips.
4. Core edit on a fresh user → fork created → branch created → contents PUT → PR opened → second edit reuses branch + PR.
5. Importer lists repos under a Codeberg/Forgejo org.

## Bottom line

**Technically feasible** — every GitHub API call the webapp makes has a Forgejo equivalent, with small but real divergences (team membership shape, PR list filter, auth header). **Policy-blocked on Codeberg specifically** for the per-user private workspace model. Either self-host Forgejo or rehome workspace storage off-git, and the rest is straightforward adapter work.

## Per-user forge choice

Could users individually pick their backend? Partly.

**Feasible per-user:**
- **Login provider.** Standard "Sign in with GitHub / Codeberg" pattern. Register one OAuth app per supported forge, route the callback by `?provider=` on the start URL. Small server change once the `Forge` adapter exists.
- **Workspace storage.** Each user's notes/searches/lists are independent, so a user's workspace can live on whichever forge they logged in with. No cross-user coordination required.

**Not feasible per-user:**
- **Core edit upstream.** The core repo is a single source of truth — one canonical upstream has to receive PRs. A user can pick where *their fork* lives, but the upstream side is fixed. If the upstream is on GitHub and a user logs in via Codeberg, they have no Codeberg-side fork to PR from; cross-forge PRs would need something like ForgeFed (nascent, not production-ready).
- **Admin/editor team gating.** Teams are forge-local. Either maintain parallel `bkk-admin`/`bkk-editor` teams on every supported forge (sync drift), or gate authorization to the forge that hosts the canonical org.

**Practical shape if pursued:**

1. Pick one canonical home for `bkk-core` + the admin/editor teams (likely GitHub, given KRP and existing community).
2. Let users pick **login provider** and have their **workspace** live on the matching forge.
3. Core editing is constrained to users whose fork lives on the canonical forge — so the login choice effectively determines who can edit core.

Cost on top of the basic Codeberg migration: a provider-selection step at login, per-provider OAuth config, and the workspace template duplicated on each supported forge. The `Forge` abstraction already needed for the migration covers most of the code.
