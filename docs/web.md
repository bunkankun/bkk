# Web frontend for BKK: Bunkankun


This is a one page web application that after loading interacts with the `bkk serve` backend via API calls.

Users will login with a GitHub ID.  In prod texts will also be loaded from GH account krp-yaml.  Users can clone them and edit -> PR.

Interactive text editing of the BKK texts will happen in the browser and saved back to the user GH account.

There are other parts of the app in Markdown format, we will also need a markdown editor.

## Running it

The frontend lives at [module/web/](module/web/) (Vite + React + TS). It always talks to a running `bkk serve` backend; it never goes to GitHub directly in v1.

### Dev mode (hot reload, two processes)

```
# Terminal 1 — backend on :8000
bkk serve --corpus module/samples --reload --upstream-repo krp-yaml/krp-yaml

# Terminal 2 — frontend on :5173
cd module/web && npm run dev
```

Open <http://localhost:5173>. Vite proxies `/api/*` to the backend at `http://127.0.0.1:8000`. The backend enables CORS for `localhost:5173` only when `--reload` is passed.

If Vite reports `Port 5173 is in use, trying another one…`, kill the stale process or pass `--port` to `npm run dev`. The proxy target (the backend) is hard-coded to `:8000` in [vite.config.ts](module/web/vite.config.ts) — if you run the backend on a different port in dev, edit that file.

### Prod mode (single process)

```
cd module/web && npm run build
bkk serve --corpus module/samples \
          --web-dist module/web/dist \
          --upstream-repo krp-yaml/krp-yaml
```

Open <http://127.0.0.1:8000>. FastAPI serves the built SPA at `/` and all backend routes under `/api/*` (e.g. `/api/bundles`, `/api/catalog`, `/api/server-info`). Unknown non-API paths fall back to `index.html` so client-side routing works after a hard refresh.

To exercise the prod bundle against the same GitHub OAuth app used for vite dev, run `bkk serve --port 5173 --web-dist module/web/dist`. The OAuth callback `http://localhost:5173/api/auth/github/callback` works identically whether vite is in front or not, because the backend always serves auth under `/api`.

### Configuration

| Flag                      | Env var               | Notes |
|---------------------------|-----------------------|-------|
| `--corpus PATH`           | `BKK_CORPUS_ROOT`     | required; bundle root |
| `--index PATH`            | `BKK_INDEX_PATH`      | merged `.bkkx`; default `<corpus>/_corpus.bkkx` |
| `--catalog PATH`          | `BKK_CATALOG_PATH`    | catalog `.bkkc`; default `<corpus>/_catalog.bkkc` |
| `--user-texts-root PATH`  | `BKK_USER_TEXTS_ROOT` | validated owner-partitioned cache outside the shared corpus |
| `--host` / `--port`       | `BKK_HOST` / `BKK_PORT` | default `127.0.0.1:8000` |
| `--admin-team ORG/SLUG`   | `BKK_ADMIN_TEAM`      | GitHub team for `/admin/*` access (default `bunkankun/bkk-admin`); members get the Admin activity in the SPA |
| —                         | `BKK_SOURCE_ROOT`     | git checkout used by `POST /admin/update` and `POST /admin/restart`; auto-detected for editable installs |
| —                         | `BKK_SOURCE_BRANCH`   | branch fetched by `/admin/update` (default `master`) |
| `--reload`                | —                     | dev only; enables auto-reload + CORS for `:5173` |
| `--upstream-repo ORG/REPO`| `BKK_UPSTREAM_REPO`   | echoed at `GET /server-info`; the SPA reads it once on startup |
| `--web-dist PATH`         | `BKK_WEB_DIST`        | directory containing the built SPA; mounted at `/` |

CLI flags override env vars.

### What works in v1

- Catalog renders the KR taxonomy as a two-level tree (top categories KR1–KR6 → subcategories), bundles lazy-load when a subcategory is opened
- Selecting a bundle opens its TOC and **auto-opens the first juan** so body text appears in parallel with the TOC
- Body text renders character-by-character with block-level lazy mounting (paragraph- or phrase-per-line, toggled in the StatusBar via ¶ / ↵)
- Text is selectable: drag-select a phrase → search box populates and Annotations panel filters to that offset range; "Search this" / "Copy ref" buttons in the selection summary
- Selection references resolve to the most recent id-bearing marker (`textid:markerId+offset`) for stable cross-edition citations
- Annotated chars are dotted-underlined; hover for tooltip; click to push into the right-panel selection
- Bundles without annotations show "No annotations for this juan."
- Left and right panel widths are user-draggable (handles between activity-bar/LeftPanel and Workspace/RightPanel) and persisted in localStorage
- Theme selection lives in the Menubar with three choices: Current, Dark, and Light. The choice is cached in `localStorage["bkk.uiPrefs"]` and, when logged in, saved into the user's GitHub workspace session file.
- The menubar logo tooltip reports the configured `upstream_repo`; the search bar sits at the right next to the theme selector and GitHub login/user controls
- Full-text search (Menubar) with five sort modes; results render in a Search tab on the right panel and clicking a hit scrolls + flashes the matched span in the workspace for 15s
- User text lists live in the left activity bar's Lists view. Lists are plain `lists/*.txt` files: a KR id in the first token of a line counts as membership, while comments and other line content are ignored. Anonymous lists persist locally; logged-in lists sync through the GitHub workspace. Active lists mark search hits; when a search is open they also appear as colored list facets, where the user can switch list filtering between badges-only, Any, and All.
- Logged-in users create private texts from **Settings → New user text**.
  The wizard accepts pasted or uploaded KRP, TLS, or CBETA source, confirms a
  detected ID (or suggests the next owner-local `KR9a####` ID), applies
  `bkk chars canonicalize`, validates the bundle, and creates a private
  `<github-user>/<text-id>` repository. The text is readable and visible in
  that owner's Catalog immediately; its badge changes to `search ready` after
  background indexing. Other users and anonymous requests receive 404.

For lists:

badges means: active lists do not filter the search. They only add colored badges to matching search results, so you can see which hits belong to which list.

any means: filter search results to texts that are in at least one active list. This is a union.

all means: filter search results to texts that are in every active list. This is an intersection.

Example:

List A: KR1, KR2, KR3
List B: KR2, KR3, KR4

badges: search whole corpus, badge hits from A/B
any: search only KR1, KR2, KR3, KR4
all: search only KR2, KR3


- A pinned search input on the LeftPanel Catalog queries the server-side catalog across title, pinyin (tone-insensitive), English title, and identifiers before any category is expanded
- The left activity bar has separate Catalog and Timeline entries; Timeline browses calendar-century buckets

What does NOT work yet (deferred to later slices): in-browser text editing/PRs, translation mode, AI/Dharma panels, the cross-text annotation dictionary, pane splits.

## Admin activity

The shield icon in the Activity Bar is visible only to GitHub users who are active members of the team named in `BKK_ADMIN_TEAM` (default `bunkankun/bkk-admin`). Membership is checked once at OAuth callback and cached on the session for 30 days; revoking team membership only takes effect after the session expires or the user logs out. Non-members never see the icon, and `/admin/*` returns 403 if they probe by URL.

The panel has two tabs:

- **Dashboard** — read-only health snapshot from `GET /admin/info`: server version, corpus path + bundle count by section, index/catalog schema version and counts, core/annotations index status, and the configured `source_root` + branch. Stale per-bundle indices show as an amber banner at the top.
- **Operations** — long-running maintenance jobs. Each button POSTs to its endpoint, polls `GET /admin/jobs/{id}` every 1.5s, and shows the status badge (`running`/`success`/`error`).
  - *Corpus indexes*: rebuild merged corpus index, catalog index, translation search, annotation location index
  - *Per-bundle*: reindex / validate a single bundle by textid
  - *bkk-core*: fast-forward the local clone and rebuild its index
  - *Server*: `Update (git pull + pip install)` runs `git fetch && git merge --ff-only origin/<source_branch>` in `source_root`, then `pip install -e <source_root>/module`; captures pip stdout/stderr into the job result. `Restart server` sends SIGTERM to the process — only useful under a supervisor (systemd) that respawns it. The UI then polls `/server-info` and shows `online` once the server is reachable again.

Update and Restart are intentionally separate clicks so you can read the update job's pip output before bouncing the process. There is no automatic rollback — if the new code crashes on boot, systemd will keep restarting it and shell access is required to `git reset`.

## Search

The Menubar carries a search bar with two dropdowns:

- **Target** — *Full text* (active in v1). *Dictionary* and *Translations* are present but disabled with a `v2` tooltip. Catalog search is intentionally absent here; it lives as a separate filter input pinned to the top of the LeftPanel Catalog.
- **Sort** — five options, all server-side so pagination stays correct:
  - `match` (default) — order by `match + right`; reading-order forward from the match position.
  - `textid` — natural index order: `(textid, juan_seq, master_offset)`.
  - `reverse_prematch` — order by `left[::-1]`; classical reverse concordance, reading-order backward from the match position.
  - `date` — order by the bundle's `metadata.composition_period` leading year (BCE markers `前` / `BC` / `BCE` parsed as negative). Bundles with no parseable date fall to the end.
  - `closeness` — greedy chain over pairwise KWIC character-overlap. Head = hit with the highest summed overlap to all others; each subsequent step appends the unvisited hit with the greatest character-overlap to the most recently appended hit. Adjacent rows share the most KWIC chars; outliers fall to the end. Query characters are excluded from the overlap set.

The endpoint is `GET /search?q=…&sort=…` (see [module/bkk/serve/routers/search.py](module/bkk/serve/routers/search.py)); the response echoes the `sort` value that took effect. Hits are sorted over the **full** result list before the `offset:offset+limit` slice.

Results render in a third right-panel tab (alongside Annot. and Chat) that appears once a search is in flight and remains for the session. Each row shows `toc_label · textid · juan` plus a KWIC line. The left context is anchored to the right edge via `display: flex; justify-content: flex-end`, so when it overflows the column its **leftmost** chars get clipped (those nearest the match stay visible). Phrase-boundary trim (`。！？；`) preserves clean breakpoints when sentence-enders are present, with an ellipsis chip marking elided text on either side. Hits whose match came from a non-master witness are flagged with an amber chip in the meta row naming the edition (e.g. `TKD`); when the witness provides context around the match (i.e. the variant reading is longer than its master span) a second, dimmer KWIC line renders below the master line, showing the witness's actual context around the query (`witness_left` / `matched_text` / `witness_right`) — useful when the master text was rewritten and so the master line's highlighted token is the replaced master string rather than the query itself. Clicking a row opens the juan in the workspace and triggers a 15s amber flash on the master span. Pagination uses prev/next over `offset` (page size 50). Repeated `textids` parameters restrict search to a list-scope; `GET /search/textids` returns all unique matched text ids with `hit_count` and `text_count` for saving search-derived lists.

The catalog search input on the LeftPanel calls `GET /catalog?q=…`. It searches the whole catalog server-side across title, tone-insensitive pinyin, English title, `textid`, canonical identifier, and manifest identifiers.

## Catalog tree

The LeftPanel Catalog is a two-level tree of the Kanripo classification, populated from `GET /catalog/categories` ([module/bkk/serve/routers/catalog.py](module/bkk/serve/routers/catalog.py)). The endpoint joins `bkk.data/kr_categories.yaml` (bilingual labels) with per-leaf bundle counts derived from the live corpus.

- Top categories KR1–KR6 are listed at startup with descendant bundle counts; expanding a top reveals its subcategories.
- Subcategory bundles load lazily on first expand via `GET /catalog?tags.kr-categories=<code>` and are cached for the session.
- The Timeline view loads `GET /catalog/timeline`; opening a century lazily fetches `GET /catalog?century=<bucket>`.
- Clicking a bundle calls `workspace.selectBundle(textid)`, which both populates the right-side TOC AND fire-and-forget auto-opens the first part — body text and TOC appear in parallel rather than requiring a TOC click.

## Read view

The Workspace TextViewer ([module/web/src/components/Workspace/TextViewer.tsx](module/web/src/components/Workspace/TextViewer.tsx)) renders juan body text in **blocks** so very long juans don't pay the cost of mounting every span up-front:

- Block boundaries follow `paragraph-break` markers (paragraph mode, default) or `tls:seg` markers (phrase mode), falling back to literal `\n` / phrase-ending punctuation when those markers are absent.
- Each block lives in an `IntersectionObserver` with `rootMargin: "200% 0px"`; once a block enters the expanded viewport it stays mounted (so scroll position never jumps back).
- The line-mode toggle (¶ / ↵) lives in the StatusBar and persists in `localStorage["bkk.readPrefs"]`. When the user is logged in, it is also saved to `settings/session.json` in their GitHub workspace.
- Punctuation is injected from `punctuation`-type markers at their `offset`, skipping positions where the master text already has punctuation.
- PUA Kanripo refs (`&KRnnnn;`) are decoded on render via `decodeKrRefs`; only CJK + PUA chars participate in selection (drag-select skips ASCII/whitespace markers).

### Selection refs

A drag-select carries the most recent id-bearing marker at-or-before the selection start. The Annotations panel surfaces it as `@ <markerId> + <offset>` (or just `@ offset N` if no marker is upstream). "Copy ref" copies the canonical form `<textid>:<markerId>+<offset>` to the clipboard; "Search this" runs full-text search on the selected chars.

### Scroll-to-match

Clicking a search hit calls `workspace.openHit(hit)`, which atomically updates the active tab and stages a `pendingHighlight`. WorkspacePane keys `<TextViewer>` by `${textid}:${seq}` so a navigation forces a clean unmount/remount — the previous juan's stale DOM can never be the target of the layout-effect `scrollIntoView`. A `lastFlashedRef` prevents the layout effect from re-flashing on subsequent re-runs (e.g. when the IntersectionObserver expands `visibleBlocks` after the smooth scroll completes), and the 15s clear-timer lives in its own effect keyed on `flashOffsets` so an unrelated dep change can't cancel it.

## Resizable panels

Both the LeftPanel and RightPanel widths are user-draggable. Handles (`<ResizeHandle side="left|right" />` in [module/web/src/App.tsx](module/web/src/App.tsx)) sit between the activity-bar/LeftPanel and Workspace/RightPanel respectively. Widths are clamped to `[180, 600]` px and persisted in `localStorage["bkk.panelWidths"]`. Authenticated sessions also restore and save these widths through `settings/session.json`.

The drag's terminating `mouseup` bubbles into the Workspace's `.ec` element, whose `handleMouseUp` would otherwise treat any non-collapsed `window.getSelection()` range as a fresh drag-select and switch the right tab to Annotations (hiding live search results). A module-scoped `isResizing` guard in [module/web/src/state/useWorkspace.ts](module/web/src/state/useWorkspace.ts) is set on drag start and cleared in a `setTimeout(0)` after `mouseup` — the deferred clear lets the bubbling event observe the guard as still true and short-circuit. The drag also pre-clears `window.getSelection()` so a stale selection (from before the drag) can't trip the same path.


# CSS styles

## for sections 

```css
:root {
    --kr1: #2E7D32; /* Classics */
    --kr2: #C62828; /* History */
    --kr3: #1565C0; /* Masters */
    --kr4: #424242; /* Belles-lettres */
    --kr5: #7B4FA3; /* Daoism */
    --kr6: #D4A017; /* Buddhism */
}

.kr1 { color: var(--kr1); }
.kr2 { color: var(--kr2); }
.kr3 { color: var(--kr3); }
.kr4 { color: var(--kr4); }
.kr5 { color: var(--kr5); }
.kr6 { color: var(--kr6); }
```
