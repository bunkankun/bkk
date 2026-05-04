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

What does NOT work yet (deferred to later slices): GitHub login, in-browser editing/PRs, translation mode, IIIF facsimile (Inspect), AI/Dharma panels, the cross-text annotation dictionary, pane splits.
