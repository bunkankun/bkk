# BKK web app — internals

Developer documentation for the SPA at [module/web/](../../module/web/).
Covers code layout, the major components, and how the pieces fit. For
user-facing flows (admin UI, search, login) see [../web.md](../web.md);
for the backend it talks to see [../BKK serve.md](../BKK%20serve.md).

The SPA is React + TypeScript + Vite. No router, no Redux/Zustand —
state lives in one hand-rolled store built on `useSyncExternalStore`.
Same bundle runs in two modes: vite dev (separate processes, `/api/*`
proxied to `:8000`) and prod (built `dist/` served by `bkk serve` on
the same origin as the API).

## Top-level layout

```
module/web/src/
├── main.tsx            React mount + global style imports
├── App.tsx             Shell layout, boot effects, resize handling
├── api/
│   ├── client.ts       fetch wrapper, one function per endpoint
│   └── types.ts        TS interfaces mirroring backend schemas
├── state/
│   └── useWorkspace.ts Global store + selector hook + actions
├── components/
│   ├── ActivityBar.tsx Left rail (activity switcher)
│   ├── StatusBar.tsx   Bottom bar (textid, modes, hover codepoint)
│   ├── CharInfoBar.tsx Inline char + codepoint readout
│   ├── Welcome.tsx     Empty-workspace splash (markdown from server)
│   ├── SenseUses.tsx   Modal: all annotations attached to a sense
│   ├── Menubar/        Top bar (logo, search, sidebar toggles, user)
│   ├── LeftPanel/      One module per activity (catalog, toc, …)
│   ├── Workspace/      Pane tree + the views a pane can host
│   └── RightPanel/     Tabs: annotations | search | chat (stub)
├── lib/                Pure utilities (PUA, markers, lists, images)
└── styles/
    ├── tokens.css      CSS custom properties (colors, fonts, KR classes)
    └── app.css         Shell flex layout
```

## App shell

[App.tsx](../../module/web/src/App.tsx) lays out a fixed grid:

```
┌──────────────────────────── Menubar ────────────────────────────┐
│ AB │ LeftPanel │═│      Workspace pane tree     │═│ RightPanel │
├──────────────────────────── StatusBar ──────────────────────────┤
```

- **Menubar** — [Menubar/index.tsx](../../module/web/src/components/Menubar/index.tsx). Logo (click → reset pane layout), search bar, sidebar toggles, user account / sync status.
- **ActivityBar (AB)** — [ActivityBar.tsx](../../module/web/src/components/ActivityBar.tsx). Vertical strip of icon buttons; sets `activity` in the store.
- **LeftPanel** — wrapper picks the module matching the current activity. Width persisted; collapsible.
- **Workspace** — recursive pane tree, see below.
- **RightPanel** — tabbed; width persisted; collapsible. Tabs are annotations / search / chat.
- **StatusBar** — [StatusBar.tsx](../../module/web/src/components/StatusBar.tsx). Active textid (colored by KR class), read mode buttons, line mode toggle, hover codepoint.

Resize handles (`.pane-resize`) sit between AB/LP, LP/Workspace, and Workspace/RP. Widths are written to `bkk.panelWidths` in localStorage on drag end.

Routing: none in v1. The active text + juan + pane layout is in-memory state, persisted to the user's GitHub workspace (`settings/session.json`) for logged-in users so it restores on next visit. Anonymous users get localStorage only.

Bootstrap (in `App.tsx` mount effects):
1. `workspace.loadAuthSession()` — `GET /auth/session`; on hit, pull `session.json` from the workspace repo and rehydrate history/prefs.
2. `getServerInfo()` — version + upstream repo, cached in the store and shown in the menubar logo tooltip.
3. Apply theme via `document.documentElement.dataset.theme`.

## State (the store)

Everything lives in [state/useWorkspace.ts](../../module/web/src/state/useWorkspace.ts) — a single ~2.4k-line module with three parts:

1. **State shape** (`WorkspaceState`) — one frozen object, replaced wholesale on each update.
2. **Mutators / actions** — exported as the `workspace` object (`workspace.openJuan(...)`, `workspace.runSearch()`, etc.). They build a new state and call `setState`.
3. **Subscription hook** — `useWorkspace(selector)` runs the selector on each store update; React `useSyncExternalStore` handles the subscription bookkeeping. Components subscribe to slices; updates that don't touch their slice don't re-render them.

No Redux, no Zustand, no Context. The agent of choice when adding new state: extend `WorkspaceState`, add a mutator on `workspace`, read it via `useWorkspace(s => s.your_slice)`.

Major slices (non-exhaustive):

| Slice              | What it owns                                                                        |
|--------------------|-------------------------------------------------------------------------------------|
| `activity`         | which LeftPanel module is showing                                                   |
| `pane`             | recursive `PaneNode` tree (leaf has tabs of text or core-record; split has children)|
| `activeTextid` / `activeSeq` | the focused text + juan (computed from active pane's active tab)         |
| `readMode`         | `read` / `trans` / `inspect` / `edit` — affects WorkspacePane rendering             |
| `rightTab`         | which RightPanel tab is visible                                                     |
| `search`           | query, target, filters, results, status, pagination, abort controller               |
| `searchHistory`    | last 50 queries; saved to `session.json`                                            |
| `textHistory`      | last 20 opened texts; saved to `session.json`                                       |
| `textLists`        | `.txt` lists (anon: localStorage; auth: `settings/lists/` in workspace repo)        |
| `auth`             | login state + GitHub identity                                                       |
| `serverInfo`       | version + upstream repo                                                             |
| `uiPrefs`          | theme, sidebar visibility — `bkk.uiPrefs` + `session.json`                          |
| `readPrefs`        | line mode — `bkk.readPrefs` + `session.json`                                        |
| `panelWidths`      | LP/RP widths — `bkk.panelWidths`                                                    |
| `selection`        | current text-range selection — drives annotation filtering                          |
| `coreTarget`       | sense the next annotation will attach to                                            |
| `blueskyStatus`    | Bluesky handle/DID for annotation posting                                           |
| `localAnnotations` | optimistic annotations until the harvester picks them up                            |

Persistence strategy:

- **localStorage** keys (`bkk.*`) — small UI prefs that should survive a tab close even without login.
- **GitHub workspace files** (logged in) — `settings/session.json` (session/history/prefs), `settings/lists/*.txt` (user lists). Writes are debounced (~1s) and use file SHA for optimistic concurrency.

## API client

[api/client.ts](../../module/web/src/api/client.ts) is a thin `fetch` wrapper. Conventions:

- Base URL: `import.meta.env.DEV ? "/api" : ""`. In dev Vite proxies `/api/*` → `127.0.0.1:8000`. In prod the SPA is same-origin as the API.
- One exported function per backend endpoint. Names follow `getThing` / `postThing` / `patchThing`.
- Manifest responses (`getManifest`) go through `manifestCache` — repeat calls for the same textid return the cached promise.
- Annotation responses (`getAnnotations`) go through `annotationsCache` keyed by `${textid}_${seq}`, so the Read pane, the Trans pane, and the AnnotationsTab share one fetch per juan. `archiveDeleteAnnotation` and `patchContributionCuration` invalidate the affected juan automatically; `invalidateAnnotationsCache(textid, seq)` is exported for explicit refreshes.
- Errors throw `ApiError(status, message, body)`. Components display these inline; there is no global error boundary.

Endpoint groups: server info, auth, admin jobs, catalog, bundles + juan + annotations, GitHub-backed bundle editing, full-text search, translation search + alignment, annotation write + Bluesky, CORE (dictionary) read/write/PR, workspace file CRUD. See [api/types.ts](../../module/web/src/api/types.ts) for the response shapes.

## Components by area

### Menubar

- [Menubar/index.tsx](../../module/web/src/components/Menubar/index.tsx) — logo, sidebar toggles, user button (login / logout / sync status), entry point for Bluesky login modal.
- [Menubar/SearchBar.tsx](../../module/web/src/components/Menubar/SearchBar.tsx) — query input, target dropdown (fulltext / dictionary / translations), sort dropdown.
- [Menubar/BlueskyLogin.tsx](../../module/web/src/components/Menubar/BlueskyLogin.tsx) — modal: handle + app password → `postBlueskyLogin`.

### LeftPanel modules

Each file is the entire UI for one activity. Switching activity unmounts the previous module.

- [LeftPanel/Catalog.tsx](../../module/web/src/components/LeftPanel/Catalog.tsx) — category tree + timeline view, scoped search.
- [LeftPanel/Toc.tsx](../../module/web/src/components/LeftPanel/Toc.tsx) — juan list + TOC markers for the active text.
- [LeftPanel/Core.tsx](../../module/web/src/components/LeftPanel/Core.tsx) — dictionary collections, fuzzy search, super-entry list.
- [LeftPanel/Lists.tsx](../../module/web/src/components/LeftPanel/Lists.tsx) — user text-list editor, active-list toggles for search scoping.
- [LeftPanel/Translations.tsx](../../module/web/src/components/LeftPanel/Translations.tsx) — translations available for the active bundle; click to open an alignment.
- [LeftPanel/History.tsx](../../module/web/src/components/LeftPanel/History.tsx) — recently opened texts.
- [LeftPanel/Settings.tsx](../../module/web/src/components/LeftPanel/Settings.tsx) — theme + default-open-mode pickers.
- [LeftPanel/NewUserTextDialog.tsx](../../module/web/src/components/LeftPanel/NewUserTextDialog.tsx) — authenticated KRP/TLS/CBETA import wizard launched from Settings.
- [LeftPanel/Admin.tsx](../../module/web/src/components/LeftPanel/Admin.tsx) — admin dashboard + operations (job submit, status polling). Visible only to admin team members.

### Workspace (pane tree)

- [Workspace/PaneTree.tsx](../../module/web/src/components/Workspace/PaneTree.tsx) — 22 lines. Recursively renders `PaneNode`: a leaf renders `WorkspacePane`; a split renders children in a flex row with a resize handle.
- [Workspace/WorkspacePane.tsx](../../module/web/src/components/Workspace/WorkspacePane.tsx) — per-leaf container. Tab bar, close/pin buttons, read/trans/inspect mode buttons, dispatches the active tab to the right viewer.
- [Workspace/TextViewer.tsx](../../module/web/src/components/Workspace/TextViewer.tsx) — the main text rendering. Pulls juan body/front/back, splits into spans, lays in annotation overlays, owns text selection (→ `selection` slice), hover codepoint → CharInfoBar, scroll-spy for current page, and the mouse-coord → `marker_id` + offset mapping that anchors annotations. Line-mode toggle is here.
- [Workspace/ImagePanel.tsx](../../module/web/src/components/Workspace/ImagePanel.tsx) — scanned page images. Pan/zoom via pointer events. Syncs current page with TextViewer via IntersectionObserver.
- [Workspace/TranslationViewer.tsx](../../module/web/src/components/Workspace/TranslationViewer.tsx) — parallel source/target alignment table. Mirrors TextViewer's annotation linkage: source spans decorated from the same `annotationsCache`, single-click on an annotated char sets `selectedAnnotationId` and opens the Annotations tab, and `pendingHighlight` scrolls + flashes the matching span (so card↔text navigation works in either direction). When the pane is opened in Trans mode for a text with no translations, it calls `getBundleTranslations` and falls back to Read mode via `setReadMode("read")` instead of leaving an empty pane.
- [Workspace/CoreRecord.tsx](../../module/web/src/components/Workspace/CoreRecord.tsx) — dictionary entry view. Sense tree, backlinks, concept words, attribution badges.
- [Workspace/CoreRecordEditor.tsx](../../module/web/src/components/Workspace/CoreRecordEditor.tsx) — edit mode for a core record. Fork → patch → commit → PR via the backend GitHub proxy.
- [Workspace/BundleEditor.tsx](../../module/web/src/components/Workspace/BundleEditor.tsx) — GitHub-backed text/marker editor. It edits one front/body/back bucket, shifts markers through codepoint-based text splices, blocks ambiguous anchors, and submits either an admin commit or a user fork PR.
- [Workspace/AnnotationLayer.tsx](../../module/web/src/components/Workspace/AnnotationLayer.tsx) — pure helper (no JSX): builds the per-juan annotation index TextViewer overlays.

### RightPanel tabs

- [RightPanel/AnnotationsTab.tsx](../../module/web/src/components/RightPanel/AnnotationsTab.tsx) — annotations at the current text selection, grouped by sense. Click a sense to compose, see uses, or open the picker.
- [RightPanel/AnnotationCompose.tsx](../../module/web/src/components/RightPanel/AnnotationCompose.tsx) — form for a new annotation; requires Bluesky login.
- [RightPanel/CoreTargetPicker.tsx](../../module/web/src/components/RightPanel/CoreTargetPicker.tsx) — modal: pick a sense from the CORE super-entry for the selected orthography.
- [RightPanel/SearchTab.tsx](../../module/web/src/components/RightPanel/SearchTab.tsx) — search results: hits or translation segments, faceted filters, pagination, "save as list" action.
- [RightPanel/ChatTab.tsx](../../module/web/src/components/RightPanel/ChatTab.tsx) — placeholder for v2 AI panel.

### Standalone

- [components/Welcome.tsx](../../module/web/src/components/Welcome.tsx) — empty-workspace splash. Markdown fetched from `GET /server-welcome` (re-read each request server-side, so edits land without a restart).
- [components/SenseUses.tsx](../../module/web/src/components/SenseUses.tsx) — modal listing every annotation attached to a given sense, across the corpus.

## lib/ — pure utilities

- [lib/krClass.ts](../../module/web/src/lib/krClass.ts) — `KRn…` → `krn` CSS class. Used everywhere a textid is rendered.
- [lib/pua.ts](../../module/web/src/lib/pua.ts) — Kanripo PUA decode (`&KRnnnn;` ↔ `chr(0x105000 + n)`). Same formula as the backend.
- [lib/markers.ts](../../module/web/src/lib/markers.ts) — parse `marker_id` (`textid_edition_location`). Splits from the right because textids may contain underscores.
- [lib/textLists.ts](../../module/web/src/lib/textLists.ts) — parse/serialize `.txt` list files (header metadata + textid lines). Path/name normalization.
- [lib/imageResolver.ts](../../module/web/src/lib/imageResolver.ts) — manifest + page-break → image URL. Looks up the edition in `manifest.metadata.image_base_urls`; handles direct paths and IIIF declarations.

## Styles

Two files, both global:

- [styles/tokens.css](../../module/web/src/styles/tokens.css) — design tokens as CSS custom properties: backgrounds, borders, text colors, accents (`--amb`, `--blu`, `--grn`), the six KR-class colors (`--kr1…kr6`), font families. Dark/light variants under `:root[data-theme="dark"]` / `[data-theme="light"]`.
- [styles/app.css](../../module/web/src/styles/app.css) — shell layout. Class names are short (`.app`, `.mb`, `.ab`, `.lp`, `.rp`, `.sb`, `.pane-resize`).

No CSS modules, no Tailwind. Components add classes directly; style rules live in `app.css` keyed off those classes.

## Cross-cutting concerns

**Theme** — `workspace.setTheme()` writes to `uiPrefs`, persists, and sets `document.documentElement.dataset.theme`. CSS handles the rest.

**Auth** — `startGithubLogin()` redirects to `/api/auth/github/start`; the backend completes the OAuth dance and sets a session cookie. On boot, `loadAuthSession()` calls `GET /api/auth/session`. Logout clears the cookie and resets in-memory session-derived slices. Workspace sync (pull `session.json`, push debounced) is triggered by login.

**Bundle editing** — authenticated users load the editable remote from `GET /api/bundles/{textid}/juan/{seq}/edit`. `POST` saves the juan, marker asset, and manifest as one Git commit. Admins update the upstream repository's default branch; other users get a fork branch and pull request. Repositories default to `bkkbooks/<textid>` and each repository's GitHub `default_branch`; override these with `BKK_BUNDLE_GITHUB_ORG` / `BKK_BUNDLE_GITHUB_BRANCH` or the corresponding `serve` keys.

**User texts** — `POST /api/user-texts/preview` stages and validates source;
`POST /api/user-texts` publishes the confirmed private bundle and starts
indexing; `POST /api/user-texts/sync` refreshes registered repositories after
login and prunes entries whose private repositories were deleted by hand.
Private resolution, catalog records, and search indexes are keyed by the
authenticated GitHub login and never merged into shared indexes.

**Search lifecycle** — `runSearch()` aborts any in-flight request via `searchAbort`, fires the new one, and writes status (`idle`/`loading`/`ok`/`error`) into `state.search`. List filters narrow the textid scope via `scopedListTextids()` before the request.

**Read mode ↔ LeftPanel activity** — `setReadMode`, `openJuan`, and `selectBundle` derive the activity from the resolved read mode: `read` → Contents (`texts`), `trans` → Translations (`overlays`), `inspect` leaves activity alone. So opening a text in Trans mode lands on the Translations panel, falling back from Trans to Read also switches the panel back to Contents, and the activity tracks the mode without needing a separate click.

**Dev vs prod** — all backend routes live under `/api` in both setups. `apiBase` is the constant `/api`. Vite proxies `/api/*` straight through to the FastAPI backend in dev ([vite.config.ts](../../module/web/vite.config.ts)); in prod the SPA and API share the same origin.

**No global error boundary, no service worker, no websockets.** Long-running admin jobs are polled (`getAdminJob(id)` on an interval). All other backend interactions are request/response.
