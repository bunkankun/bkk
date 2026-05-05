We want a one page web application that after loading interacts with the `bkk serve` backend via API calls.

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

Open <http://127.0.0.1:8000>. FastAPI serves the built SPA at `/` and the API at `/bundles`, `/catalog`, `/server-info`, etc. Unknown non-API paths fall back to `index.html` so client-side routing works after a hard refresh.

### Configuration

| Flag                      | Env var               | Notes |
|---------------------------|-----------------------|-------|
| `--corpus PATH`           | `BKK_CORPUS_ROOT`     | required; bundle root |
| `--index PATH`            | `BKK_INDEX_PATH`      | merged `.bkkx`; default `<corpus>/_corpus.bkkx` |
| `--host` / `--port`       | `BKK_HOST` / `BKK_PORT` | default `127.0.0.1:8000` |
| `--admin-token TOKEN`     | `BKK_ADMIN_TOKEN`     | bearer required for `/admin/*`; if unset, admin is open |
| `--reload`                | —                     | dev only; enables auto-reload + CORS for `:5173` |
| `--upstream-repo ORG/REPO`| `BKK_UPSTREAM_REPO`   | echoed at `GET /server-info`; the SPA reads it once on startup |
| `--web-dist PATH`         | `BKK_WEB_DIST`        | directory containing the built SPA; mounted at `/` |

CLI flags override env vars.

### What works in v1

- Catalog → click a bundle → TOC populates from the manifest
- Click a juan → body text renders character-by-character
- Annotated chars are dotted-underlined; hover for tooltip; click to push into the right-panel selection
- Drag-select a phrase → annotations panel filters to that offset range
- Bundles without annotations show "No annotations for this juan."
- The menubar logo tooltip reports the configured `upstream_repo`
- Full-text search (Menubar) with five sort modes; results render in a Search tab on the right panel and clicking a hit scrolls + flashes the matched span in the workspace
- A pinned filter input on the LeftPanel Catalog narrows the loaded bundle list client-side (title / alt_titles / authors / identifiers)

What does NOT work yet (deferred to later slices): GitHub login, in-browser editing/PRs, translation mode, IIIF facsimile (Inspect), AI/Dharma panels, the cross-text annotation dictionary, pane splits.

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

Results render in a third right-panel tab (alongside Annot. and Chat) that appears once a search is in flight and remains for the session. Each row shows `toc_label · textid · juan` plus a KWIC line where the left context is right-aligned via `direction: rtl; unicode-bidi: plaintext`. A `[witness]` chip flags hits whose match came from a non-master witness. Clicking a row opens the juan in the workspace and triggers a 1.2s amber flash on the master span. Pagination uses prev/next over `offset` (page size 50).

The catalog filter input on the LeftPanel is currently a UI-only client-side substring filter over the already-loaded matches. v2 will swap this for a backend `GET /catalog?q=` endpoint and lift the filter state into the workspace store.