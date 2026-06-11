# Bunkankun — a non-technical overview

Bunkankun (BKK) is a small ecosystem for working with premodern Chinese
texts. It takes texts that already exist in scattered, partly incompatible
forms — the Kanseki Repository (KRP), the TLS/HXWD project, the CBETA
Buddhist canon — and republishes them in a single, audited format that
scholars can cite, search, translate, and annotate in a way that does not
quietly drift over time. This document sketches the moving parts and how
they fit together.

## The big picture

Four kinds of actor meet around each text:

```
        ┌──────────────────┐                ┌──────────────────┐
        │     GitHub       │                │     Bluesky      │
        │  (slow & solid)  │                │  (fast & noisy)  │
        │                  │                │                  │
        │ • text bundles   │                │ • notes          │
        │ • translations   │                │ • comments       │
        │ • annotations    │                │ • translation    │
        │   archive        │                │   drafts         │
        │ • user logins    │                │ • author identity│
        └─────────┬────────┘                └─────────┬────────┘
                  │                                   │
                  │      pulls / publishes            │  listens / posts
                  ▼                                   ▼
        ┌──────────────────────────────────────────────────────┐
        │                bkk serve  (backend)                  │
        │  the librarian: holds the verified corpus, answers   │
        │  search queries, mediates every contribution         │
        └──────────────────────────┬───────────────────────────┘
                                   │  HTTP /api/...
                                   ▼
        ┌──────────────────────────────────────────────────────┐
        │                Bunkankun SPA  (browser)              │
        │  the reading room: catalog, parallel views, search,  │
        │  selection → annotate / comment / translate          │
        └──────────────────────────────────────────────────────┘
```

The browser never talks to GitHub or Bluesky on its own. Everything goes
through the backend, so that what the user sees has been verified against
its content hash and joined with the right annotations.

## The data: bundles, manifests, and four layers

A text is published as a **bundle** — one folder per text, with one file
per *juan* (scroll), a *manifest* that lists what is inside, and a few
shared reference files (the canonical character set, mappings for
characters that Unicode does not yet have a codepoint for, and so on).
Every text element carries a SHA-256 hash. A consumer can re-hash
anything they receive and tell, byte-for-byte, whether it matches what the
author published — trust comes from the hash, not from where the file
arrived from.

A *recipe* is a separate, smaller object that pins a specific set of
bundles together — for example, a base text, a translation of it, and a
glossary — by their canonical identifiers and hashes. A recipe is what a
client submits to the backend to ask for an assembled composition, and
what a scholar can attach to an article so that a later reader can fetch
the exact materials cited.

Conceptually, four layers live in four address spaces:

```
   Texts        ── per-text repositories on GitHub
                   stable, edition-faithful, rarely change

   bkk-core     ── single repository: shared vocabulary
                   (concepts, words/senses, syntactic functions, …)

   Translations ── one repository per translation
                   editable prose, anchored to source segments

   Annotations  ── live as Bluesky records; periodically snapshotted
                   into an archive repository
```

Keeping these separate means the stable text layer is not rewritten every
time someone posts a comment, and the fast-moving annotation layer is not
forced into a slow, PR-mediated workflow.

## The backend: `bkk serve`

`bkk serve` is a Python web service. It does four jobs:

1. **Hold the corpus.** It mounts a directory of bundles and a SQLite
   search index (`.bkkx`) built from them. The same index supports
   variant-aware substring search — a query for `甞不盡` finds passages
   where the master edition reads `嘗不盡` but a witness edition records
   `甞`, and the result shows both.
2. **Serve the API.** Endpoints under `/api/...` let the browser list
   the catalog, fetch a juan, run a search, list annotations for a
   passage, post a new contribution, change curation state, and so on.
3. **Mediate identity.** Logging in is a GitHub OAuth flow. The user's
   GitHub identity is what authorises any write action; team membership
   (`bunkankun/bkk-admin`, `bunkankun/bkk-editor`) determines what
   surfaces the user sees.
4. **Bridge to Bluesky.** When the user posts an annotation, the backend
   converts it from the JSON shape the browser sends to the atproto
   shape Bluesky expects, and writes it to the user's Bluesky repository
   on their behalf. When something interesting appears anywhere on the
   Bluesky network, the backend hears about it through a streaming
   "firehose" subscription and shows it in the live Chat tab.

## The frontend: a single-page application

The browser side is a React app at [module/web/](../module/web/). It is
a single-page application — one HTML page, then everything driven by
JavaScript talking to the backend. The visible furniture is:

- **Catalog** on the left, a tree of texts by category.
- **Workspace** in the middle, where one or more juan files are open
  with their tables of contents.
- **Right panel**, with tabs for annotations attached to the current
  passage, search results, and a live "Chat" feed of recent contributions
  from anywhere on the network.

Text is selectable: drag-select a phrase, and the right panel filters
to annotations covering that span; a compose form lets the user post
their own.

## GitHub's role

GitHub is BKK's **archive and identity provider**. It holds:

- The per-text repositories that the backend's corpus directory tracks.
  Editorial changes to a text happen through ordinary GitHub pull
  requests against those repositories — slow, reviewable, and signed off
  by maintainers.
- The single `bkk-annotations` repository, which receives periodic
  snapshots from the live annotation store. This is the citable,
  offline-distributable form of the annotation layer.
- The per-translation repositories (`bkk-tr-…`), one per translation
  bundle.
- The user-account graph that the backend uses for login and for
  authorisation: who is an editor, who is an admin.

GitHub never talks to the browser directly. The browser knows about
GitHub only because the login button leads through GitHub's OAuth
screens.

## Bluesky's role

Bluesky (specifically the AT Protocol, "atproto", on which Bluesky is
built) is BKK's **live transport** for contributions. The choice is
deliberate: annotations are short, frequent, and posted by many people
in parallel. Treating each one as a pull request against a text
repository would be impossible at scale.

When the user posts an annotation:

```
   browser       bkk serve         user's PDS         Jetstream relay
     │               │                  │                    │
     │  POST /api/   │                  │                    │
     │  annotations  │                  │                    │
     ├──────────────►│                  │                    │
     │               │ createRecord     │                    │
     │               ├─────────────────►│                    │
     │               │◄─── uri, cid ────┤                    │
     │◄── 200 OK ────┤                  ├── commit event ───►│
     │                                                       │
     │   later: GET /api/contributions   ◄────────── live ───┤
     │                                              feed     │
```

The record lives in the user's own atproto repository (their PDS,
"personal data server"). It is signed by their decentralised identifier
(DID) and addressed by its content hash (CID). The backend keeps a
running view of recent records through Jetstream, a streaming feed of
everything published anywhere on the atproto network in the BKK
namespaces (`org.bunkankun.annotation.note`,
`org.bunkankun.comment.post`, `org.bunkankun.translation.segment`).

Periodically, an operator runs `bkk annotations harvest`, which walks
the configured authors' Bluesky repositories and writes their records
into per-juan JSONL files inside the `bkk-annotations` archive — at
which point GitHub takes over as the long-term keeper.

## Why this split

Bluesky is good at what GitHub is bad at (atomic, low-friction posts
with built-in identity) and vice versa (durable, reviewable, citable
history). The backend exists precisely to make those two substrates
look like one coherent surface to the reader in the browser — and to
make sure that nothing reaches the reader without having been verified
against its declared hash on the way.
