// A small global store for v1 workspace state, built on React's
// useSyncExternalStore so we don't pull in redux/zustand.
// v1 has a single workspace pane — the types still allow the
// shape to grow into a pane tree later.

import { useSyncExternalStore } from "react";
import {
  ApiError,
  getAuthSession,
  getWorkspaceFile,
  getManifest,
  logout as logoutRequest,
  putWorkspaceFile,
  searchCorpus,
} from "../api/client";
import type {
  AuthSession,
  SearchHit,
  SearchResponse,
  SearchSort,
} from "../api/types";

export type Activity = "texts" | "catalog";
export type RightTab = "annotations" | "chat" | "search";
export type ReadMode = "read" | "trans" | "inspect";
export type SearchTarget = "fulltext" | "dictionary" | "translations";
export type LineMode = "paragraph" | "phrase";
export type SearchFacetKind =
  | "category"
  | "witness"
  | "voice"
  | "leftChar"
  | "rightChar"
  | "leftBigram"
  | "rightBigram"
  | "aroundBinom";

export interface SearchFilters {
  textid: string | null;
  category: string[];
  categoryDescendants: boolean;
  dateBefore: number | null;
  dateAfter: number | null;
  witness: string[];
  voice: string[];
  leftChar: string[];
  rightChar: string[];
  leftBigram: string[];
  rightBigram: string[];
  aroundBinom: string[];
}

export interface SearchState {
  query: string;
  target: SearchTarget;
  sort: SearchSort;
  filters: SearchFilters;
  status: "idle" | "loading" | "ok" | "error";
  error: string | null;
  response: SearchResponse | null;
}

export interface SearchHistoryEntry {
  id: string;
  query: string;
  target: SearchTarget;
  sort: SearchSort;
  filters: SearchFilters;
  pivotTextid: string | null;
  createdAt: string;
}

export interface PendingHighlight {
  textid: string;
  seq: number;
  bucket: string;
  offset: number;
  length: number;
}

export interface CurrentPage {
  textid: string;
  seq: number;
  bucket: string;
  markerId: string;
  offset: number;
}

export interface SelectionRange {
  textid: string;
  seq: number;
  bucket: string;
  // master_offset range, half-open: [start, end)
  start: number;
  end: number;
  // the actual char list, with PUA + CJK chars preserved
  chars: string[];
  // most recent id-bearing marker at or before `start`; null if the juan
  // has no id-bearing marker before this offset.
  anchorMarkerId: string | null;
  // start - anchorMarker.master_offset (0 when start sits exactly on it).
  anchorOffset: number;
}

export interface PaneLeaf {
  kind: "leaf";
  id: string;
  // v1 only ever holds one tab; structured for later splits.
  tabs: { id: string; type: "text"; textid: string; seq: number }[];
  activeTabId: string | null;
}

export interface WorkspaceState {
  activity: Activity;
  // active bundle/juan; juan is null until user picks one from TOC.
  activeTextid: string | null;
  activeSeq: number | null;
  // hovered char + its codepoint (for CharInfoBar / StatusBar).
  hoverChar: string | null;
  hoverCodepoint: number | null;
  // user selection for filtering the annotations panel.
  selection: SelectionRange | null;
  // right panel
  rightTab: RightTab;
  // upper-right "Read | Trans | Inspect" — Trans/Inspect disabled in v1.
  readMode: ReadMode;
  // info from GET /api/info (loaded once at startup).
  serverInfo: { upstream_repo?: string | null; version?: string } | null;
  auth: {
    status: "unknown" | "loading" | "authenticated" | "anonymous" | "error";
    error: string | null;
    session: AuthSession | null;
  };
  // v1 has a single leaf; kept so PaneTree.tsx can later host splits.
  pane: PaneLeaf;
  // search slice; ephemeral (no URL persistence in v1).
  search: SearchState;
  searchHistory: SearchHistoryEntry[];
  // a search-result span the TextViewer should scroll to + flash, then clear.
  pendingHighlight: PendingHighlight | null;
  // the page-break the user is currently viewing in Inspect mode; drives the
  // ImagePanel. Updated by TextViewer's page-anchor IntersectionObserver
  // and by ImagePanel's prev/next toolbar.
  currentPage: CurrentPage | null;
  // user-tunable read-mode display preferences (persisted in localStorage).
  readPrefs: { lineMode: LineMode };
  // user-tunable panel widths, persisted in localStorage. The handle
  // between activity-bar and left panel adjusts `left`; the one between
  // workspace and right panel adjusts `right`; the inspect-mode splitter
  // between TextViewer and ImagePanel adjusts `inspect`.
  panelWidths: { left: number; right: number; inspect: number };
  persistence: {
    status: "idle" | "loading" | "saving" | "error";
    error: string | null;
  };
}

const READ_PREFS_KEY = "bkk.readPrefs";
const PANEL_WIDTHS_KEY = "bkk.panelWidths";
const DEFAULT_LEFT_WIDTH = 240;
const DEFAULT_RIGHT_WIDTH = 360;
const DEFAULT_INSPECT_WIDTH = 480;
export const PANEL_MIN_WIDTH = 180;
export const PANEL_MAX_WIDTH = 600;
const INSPECT_MIN_WIDTH = 220;
const INSPECT_MAX_WIDTH = 1200;
const SEARCH_HISTORY_PATH = "searches/history.json";
const SESSION_PATH = "settings/session.json";
const MAX_SEARCH_HISTORY = 50;

function loadReadPrefs(): { lineMode: LineMode } {
  if (typeof window === "undefined") return { lineMode: "paragraph" };
  try {
    const raw = window.localStorage.getItem(READ_PREFS_KEY);
    if (!raw) return { lineMode: "paragraph" };
    const parsed = JSON.parse(raw);
    const lm = parsed?.lineMode === "phrase" ? "phrase" : "paragraph";
    return { lineMode: lm };
  } catch {
    return { lineMode: "paragraph" };
  }
}

function saveReadPrefs(prefs: { lineMode: LineMode }): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(READ_PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* localStorage disabled — silently keep state in memory only */
  }
}

type PanelSide = "left" | "right" | "inspect";

function clampWidth(n: unknown, fallback: number, side: PanelSide): number {
  if (typeof n !== "number" || !Number.isFinite(n)) return fallback;
  const min = side === "inspect" ? INSPECT_MIN_WIDTH : PANEL_MIN_WIDTH;
  const max = side === "inspect" ? INSPECT_MAX_WIDTH : PANEL_MAX_WIDTH;
  return Math.max(min, Math.min(max, Math.round(n)));
}

function loadPanelWidths(): { left: number; right: number; inspect: number } {
  const fallback = {
    left: DEFAULT_LEFT_WIDTH,
    right: DEFAULT_RIGHT_WIDTH,
    inspect: DEFAULT_INSPECT_WIDTH,
  };
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(PANEL_WIDTHS_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    return {
      left: clampWidth(parsed?.left, DEFAULT_LEFT_WIDTH, "left"),
      right: clampWidth(parsed?.right, DEFAULT_RIGHT_WIDTH, "right"),
      inspect: clampWidth(parsed?.inspect, DEFAULT_INSPECT_WIDTH, "inspect"),
    };
  } catch {
    return fallback;
  }
}

function savePanelWidths(widths: {
  left: number;
  right: number;
  inspect: number;
}): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(PANEL_WIDTHS_KEY, JSON.stringify(widths));
  } catch {
    /* localStorage disabled — silently keep state in memory only */
  }
}

// Transient drag-in-progress flag for the panel resize handles. Module-scoped
// because it is purely UI ephemera — it does not need to trigger re-renders
// and it is read once-per-event by `TextViewer.handleMouseUp` to avoid
// hijacking the right-tab focus when a drag's mouseup bubbles into `.ec`.
let _resizing = false;
export function setResizing(v: boolean): void {
  _resizing = v;
}
export function isResizing(): boolean {
  return _resizing;
}

let state: WorkspaceState = {
  activity: "catalog",
  activeTextid: null,
  activeSeq: null,
  hoverChar: null,
  hoverCodepoint: null,
  selection: null,
  rightTab: "annotations",
  readMode: "read",
  serverInfo: null,
  auth: {
    status: "unknown",
    error: null,
    session: null,
  },
  pane: {
    kind: "leaf",
    id: "root",
    tabs: [],
    activeTabId: null,
  },
  search: {
    query: "",
    target: "fulltext",
    sort: "match",
    filters: {
      textid: null,
      category: [],
      categoryDescendants: true,
      dateBefore: null,
      dateAfter: null,
      witness: [],
      voice: [],
      leftChar: [],
      rightChar: [],
      leftBigram: [],
      rightBigram: [],
      aroundBinom: [],
    },
    status: "idle",
    error: null,
    response: null,
  },
  searchHistory: [],
  pendingHighlight: null,
  currentPage: null,
  readPrefs: loadReadPrefs(),
  panelWidths: loadPanelWidths(),
  persistence: { status: "idle", error: null },
};

// monotonically increasing run id so an in-flight stale request can't clobber
// a newer one when the user submits twice quickly.
let searchRunId = 0;
let searchAbort: AbortController | null = null;
const workspaceFileShas: Record<string, string | undefined> = {};
let sessionSaveTimer: number | null = null;
let historySaveTimer: number | null = null;
let restoredSessionOnce = false;

function cancelSearchRequest(): void {
  searchRunId++;
  searchAbort?.abort();
  searchAbort = null;
}

async function runSearchInternal(offset: number): Promise<void> {
  const { query, target, sort, filters } = state.search;
  if (!query.trim() || target !== "fulltext") return;
  cancelSearchRequest();
  const runId = searchRunId;
  const controller = new AbortController();
  searchAbort = controller;
  state = {
    ...state,
    search: { ...state.search, status: "loading", error: null },
    rightTab: "search",
  };
  notify();
  try {
    const response = await searchCorpus({
      q: query,
      sort,
      offset,
      textid: filters.textid ?? undefined,
      witness: filters.witness,
      voice: filters.voice,
      category: filters.category,
      categoryDescendants: filters.categoryDescendants,
      dateBefore: filters.dateBefore ?? undefined,
      dateAfter: filters.dateAfter ?? undefined,
      pivotTextid: state.activeTextid ?? undefined,
      leftChar: filters.leftChar,
      rightChar: filters.rightChar,
      leftBigram: filters.leftBigram,
      rightBigram: filters.rightBigram,
      aroundBinom: filters.aroundBinom,
      signal: controller.signal,
    });
    if (runId !== searchRunId) return;
    searchAbort = null;
    state = {
      ...state,
      search: { ...state.search, status: "ok", error: null, response },
    };
    notify();
    if (offset === 0) {
      rememberSearch({
        query,
        target,
        sort,
        filters,
        pivotTextid: state.activeTextid,
      });
    }
  } catch (e) {
    if (runId !== searchRunId) return;
    searchAbort = null;
    if (e instanceof DOMException && e.name === "AbortError") return;
    state = {
      ...state,
      search: {
        ...state.search,
        status: "error",
        error: e instanceof Error ? e.message : String(e),
      },
    };
    notify();
  }
}

const listeners = new Set<() => void>();

function notify() {
  for (const l of listeners) l();
}

function toggled(values: string[], value: string): string[] {
  return values.includes(value)
    ? values.filter((v) => v !== value)
    : [...values, value];
}

function resetSearchFilters(filters: SearchFilters): SearchFilters {
  return {
    ...filters,
    textid: null,
    category: [],
    dateBefore: null,
    dateAfter: null,
    witness: [],
    voice: [],
    leftChar: [],
    rightChar: [],
    leftBigram: [],
    rightBigram: [],
    aroundBinom: [],
  };
}

function cloneSearchFilters(filters: SearchFilters): SearchFilters {
  return {
    textid: filters.textid,
    category: [...filters.category],
    categoryDescendants: filters.categoryDescendants,
    dateBefore: filters.dateBefore,
    dateAfter: filters.dateAfter,
    witness: [...filters.witness],
    voice: [...filters.voice],
    leftChar: [...filters.leftChar],
    rightChar: [...filters.rightChar],
    leftBigram: [...filters.leftBigram],
    rightBigram: [...filters.rightBigram],
    aroundBinom: [...filters.aroundBinom],
  };
}

function isNotFound(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404;
}

async function readWorkspaceJson<T>(path: string): Promise<T | null> {
  try {
    const file = await getWorkspaceFile(path);
    workspaceFileShas[path] = file.sha;
    return JSON.parse(file.content) as T;
  } catch (e) {
    if (isNotFound(e)) return null;
    throw e;
  }
}

async function writeWorkspaceJson(path: string, value: unknown): Promise<void> {
  const result = await putWorkspaceFile({
    path,
    content: `${JSON.stringify(value, null, 2)}\n`,
    sha: workspaceFileShas[path],
  });
  workspaceFileShas[path] = result.sha ?? undefined;
}

function validSearchHistoryEntry(value: unknown): SearchHistoryEntry | null {
  if (typeof value !== "object" || value == null) return null;
  const rec = value as Partial<SearchHistoryEntry>;
  if (
    typeof rec.id !== "string" ||
    typeof rec.query !== "string" ||
    !["fulltext", "dictionary", "translations"].includes(String(rec.target)) ||
    typeof rec.sort !== "string" ||
    typeof rec.createdAt !== "string" ||
    typeof rec.filters !== "object" ||
    rec.filters == null
  ) {
    return null;
  }
  return rec as SearchHistoryEntry;
}

function scheduleSessionSave(): void {
  if (state.auth.status !== "authenticated") return;
  if (typeof window === "undefined") return;
  if (sessionSaveTimer != null) window.clearTimeout(sessionSaveTimer);
  sessionSaveTimer = window.setTimeout(() => {
    sessionSaveTimer = null;
    void saveSessionState();
  }, 2500);
}

function scheduleHistorySave(): void {
  if (state.auth.status !== "authenticated") return;
  if (typeof window === "undefined") return;
  if (historySaveTimer != null) window.clearTimeout(historySaveTimer);
  historySaveTimer = window.setTimeout(() => {
    historySaveTimer = null;
    void saveSearchHistory();
  }, 1000);
}

function rememberSearch(params: {
  query: string;
  target: SearchTarget;
  sort: SearchSort;
  filters: SearchFilters;
  pivotTextid: string | null;
}): void {
  const q = params.query.trim();
  if (!q) return;
  const entry: SearchHistoryEntry = {
    id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    query: q,
    target: params.target,
    sort: params.sort,
    filters: cloneSearchFilters(params.filters),
    pivotTextid: params.pivotTextid,
    createdAt: new Date().toISOString(),
  };
  const deduped = state.searchHistory.filter(
    (item) =>
      !(item.query === entry.query && item.target === entry.target && item.sort === entry.sort),
  );
  state = {
    ...state,
    searchHistory: [entry, ...deduped].slice(0, MAX_SEARCH_HISTORY),
  };
  notify();
  scheduleHistorySave();
}

async function saveSearchHistory(): Promise<void> {
  if (state.auth.status !== "authenticated") return;
  state = { ...state, persistence: { status: "saving", error: null } };
  notify();
  try {
    await writeWorkspaceJson(SEARCH_HISTORY_PATH, {
      version: 1,
      entries: state.searchHistory,
      updatedAt: new Date().toISOString(),
    });
    state = { ...state, persistence: { status: "idle", error: null } };
    notify();
  } catch (e) {
    state = {
      ...state,
      persistence: {
        status: "error",
        error: e instanceof Error ? e.message : String(e),
      },
    };
    notify();
  }
}

async function saveSessionState(): Promise<void> {
  if (state.auth.status !== "authenticated") return;
  state = { ...state, persistence: { status: "saving", error: null } };
  notify();
  try {
    await writeWorkspaceJson(SESSION_PATH, {
      version: 1,
      activeTextid: state.activeTextid,
      activeSeq: state.activeSeq,
      currentPage: state.currentPage,
      readMode: state.readMode,
      rightTab: state.rightTab,
      readPrefs: state.readPrefs,
      panelWidths: state.panelWidths,
      updatedAt: new Date().toISOString(),
    });
    state = { ...state, persistence: { status: "idle", error: null } };
    notify();
  } catch (e) {
    state = {
      ...state,
      persistence: {
        status: "error",
        error: e instanceof Error ? e.message : String(e),
      },
    };
    notify();
  }
}

async function loadWorkspacePersistence(): Promise<void> {
  if (state.auth.status !== "authenticated") return;
  state = { ...state, persistence: { status: "loading", error: null } };
  notify();
  try {
    const [historyDoc, sessionDoc] = await Promise.all([
      readWorkspaceJson<{ entries?: unknown[] }>(SEARCH_HISTORY_PATH),
      readWorkspaceJson<{
        activeTextid?: unknown;
        activeSeq?: unknown;
        currentPage?: unknown;
        readMode?: unknown;
        rightTab?: unknown;
      }>(SESSION_PATH),
    ]);
    const entries = Array.isArray(historyDoc?.entries)
      ? historyDoc.entries
          .map(validSearchHistoryEntry)
          .filter((item): item is SearchHistoryEntry => item != null)
          .slice(0, MAX_SEARCH_HISTORY)
      : [];
    state = {
      ...state,
      searchHistory: entries,
      persistence: { status: "idle", error: null },
    };
    notify();
    if (!restoredSessionOnce && sessionDoc != null) {
      restoredSessionOnce = true;
      const activeTextid =
        typeof sessionDoc.activeTextid === "string" ? sessionDoc.activeTextid : null;
      const activeSeq =
        typeof sessionDoc.activeSeq === "number" ? sessionDoc.activeSeq : null;
      const currentPage =
        typeof sessionDoc.currentPage === "object" && sessionDoc.currentPage != null
          ? (sessionDoc.currentPage as Partial<CurrentPage>)
          : null;
      const readMode =
        sessionDoc.readMode === "inspect" ||
        sessionDoc.readMode === "trans" ||
        sessionDoc.readMode === "read"
          ? sessionDoc.readMode
          : state.readMode;
      const rightTab =
        sessionDoc.rightTab === "chat" ||
        sessionDoc.rightTab === "search" ||
        sessionDoc.rightTab === "annotations"
          ? sessionDoc.rightTab
          : state.rightTab;
      state = { ...state, readMode, rightTab };
      notify();
      if (activeTextid != null && activeSeq != null) {
        workspace.openJuan(activeTextid, activeSeq);
        if (
          currentPage != null &&
          currentPage.textid === activeTextid &&
          currentPage.seq === activeSeq &&
          typeof currentPage.bucket === "string" &&
          typeof currentPage.offset === "number"
        ) {
          state = {
            ...state,
            pendingHighlight: {
              textid: activeTextid,
              seq: activeSeq,
              bucket: currentPage.bucket,
              offset: currentPage.offset,
              length: 1,
            },
          };
          notify();
        }
      }
    }
  } catch (e) {
    state = {
      ...state,
      persistence: {
        status: "error",
        error: e instanceof Error ? e.message : String(e),
      },
    };
    notify();
  }
}

function subscribe(l: () => void) {
  listeners.add(l);
  return () => {
    listeners.delete(l);
  };
}

export function useWorkspace<T>(selector: (s: WorkspaceState) => T): T {
  return useSyncExternalStore(
    subscribe,
    () => selector(state),
    () => selector(state),
  );
}

export const workspace = {
  get state() {
    return state;
  },
  set(patch: Partial<WorkspaceState>) {
    state = { ...state, ...patch };
    notify();
  },
  setActivity(activity: Activity) {
    state = { ...state, activity };
    notify();
  },
  setRightTab(rightTab: RightTab) {
    state = { ...state, rightTab };
    notify();
  },
  setReadMode(readMode: ReadMode) {
    state = { ...state, readMode };
    notify();
  },
  setHover(char: string | null) {
    if (char == null || char.length === 0) {
      state = { ...state, hoverChar: null, hoverCodepoint: null };
    } else {
      const cp = char.codePointAt(0) ?? null;
      state = { ...state, hoverChar: char, hoverCodepoint: cp };
    }
    notify();
  },
  setSelection(sel: SelectionRange | null) {
    state = { ...state, selection: sel };
    notify();
  },
  setCurrentPage(p: CurrentPage | null) {
    const cur = state.currentPage;
    if (
      (p == null && cur == null) ||
      (p != null &&
        cur != null &&
        p.textid === cur.textid &&
        p.seq === cur.seq &&
        p.markerId === cur.markerId &&
        p.offset === cur.offset)
    ) {
      return;
    }
    state = { ...state, currentPage: p };
    notify();
    scheduleSessionSave();
  },
  selectBundle(textid: string) {
    // Reset juan + selection when changing bundle.
    const pane: PaneLeaf = {
      kind: "leaf",
      id: state.pane.id,
      tabs: [],
      activeTabId: null,
    };
    state = {
      ...state,
      activeTextid: textid,
      activeSeq: null,
      selection: null,
      currentPage: null,
      activity: "texts",
      pane,
    };
    notify();
    scheduleSessionSave();
    // Fire-and-forget: auto-open the first part so body text appears in
    // parallel with the TOC instead of waiting for a TOC click.
    void getManifest(textid)
      .then((m) => {
        if (state.activeTextid !== textid) return;
        const first = m.assets?.parts?.[0]?.seq;
        if (typeof first === "number") workspace.openJuan(textid, first);
      })
      .catch(() => {
        /* TOC component will surface the same error to the user */
      });
  },
  openJuan(textid: string, seq: number) {
    const tabId = `${textid}:${seq}`;
    const tabs = [{ id: tabId, type: "text" as const, textid, seq }];
    const pane: PaneLeaf = {
      kind: "leaf",
      id: state.pane.id,
      tabs,
      activeTabId: tabId,
    };
    state = {
      ...state,
      activeTextid: textid,
      activeSeq: seq,
      selection: null,
      currentPage: null,
      pane,
      activity: "texts",
    };
    notify();
    scheduleSessionSave();
  },
  setServerInfo(info: WorkspaceState["serverInfo"]) {
    state = { ...state, serverInfo: info };
    notify();
  },
  async loadAuthSession() {
    state = {
      ...state,
      auth: { ...state.auth, status: "loading", error: null },
    };
    notify();
    try {
      const session = await getAuthSession();
      state = {
        ...state,
        auth: {
          status: session.authenticated ? "authenticated" : "anonymous",
          error: null,
          session,
        },
      };
      notify();
      if (session.authenticated) void loadWorkspacePersistence();
    } catch (e) {
      state = {
        ...state,
        auth: {
          status: "error",
          error: e instanceof Error ? e.message : String(e),
          session: null,
        },
      };
      notify();
    }
  },
  async logout() {
    await logoutRequest();
    state = {
      ...state,
      auth: {
        status: "anonymous",
        error: null,
        session: { authenticated: false, user: null },
      },
      searchHistory: [],
      activeTextid: null,
      activeSeq: null,
      currentPage: null,
      pane: { kind: "leaf", id: state.pane.id, tabs: [], activeTabId: null },
    };
    notify();
  },
  setSearchQuery(query: string) {
    cancelSearchRequest();
    state = {
      ...state,
      search: {
        ...state.search,
        query,
        filters: resetSearchFilters(state.search.filters),
        status: "idle",
        error: null,
        response: null,
      },
    };
    notify();
  },
  setSearchTarget(target: SearchTarget) {
    cancelSearchRequest();
    state = {
      ...state,
      search: {
        ...state.search,
        target,
        filters: resetSearchFilters(state.search.filters),
        status: "idle",
        error: null,
        response: null,
      },
    };
    notify();
  },
  setSearchSort(sort: SearchSort) {
    cancelSearchRequest();
    state = {
      ...state,
      search: {
        ...state.search,
        sort,
        filters: resetSearchFilters(state.search.filters),
        status: "idle",
        error: null,
        response: null,
      },
    };
    notify();
  },
  setSearchTextid(textid: string | null) {
    state = {
      ...state,
      search: {
        ...state.search,
        filters: { ...state.search.filters, textid },
      },
    };
    notify();
    return runSearchInternal(0);
  },
  toggleSearchFacet(kind: SearchFacetKind, value: string) {
    const filters = state.search.filters;
    state = {
      ...state,
      search: {
        ...state.search,
        filters: { ...filters, [kind]: toggled(filters[kind], value) },
      },
    };
    notify();
    return runSearchInternal(0);
  },
  setSearchDateFilter(which: "before" | "after", value: number | null) {
    const key = which === "before" ? "dateBefore" : "dateAfter";
    state = {
      ...state,
      search: {
        ...state.search,
        filters: { ...state.search.filters, [key]: value },
      },
    };
    notify();
    return runSearchInternal(0);
  },
  clearSearchFilters() {
    state = {
      ...state,
      search: {
        ...state.search,
        filters: resetSearchFilters(state.search.filters),
      },
    };
    notify();
    return runSearchInternal(0);
  },
  runSearch() {
    state = {
      ...state,
      search: {
        ...state.search,
        filters: resetSearchFilters(state.search.filters),
      },
    };
    return runSearchInternal(0);
  },
  runSearchAt(offset: number) {
    return runSearchInternal(offset);
  },
  useSearchHistoryEntry(entry: SearchHistoryEntry) {
    cancelSearchRequest();
    state = {
      ...state,
      search: {
        ...state.search,
        query: entry.query,
        target: entry.target,
        sort: entry.sort,
        filters: cloneSearchFilters(entry.filters),
        status: "idle",
        error: null,
        response: null,
      },
    };
    notify();
    return runSearchInternal(0);
  },
  clearSearch() {
    cancelSearchRequest();
    state = {
      ...state,
      search: {
        ...state.search,
        query: "",
        status: "idle",
        error: null,
        response: null,
      },
    };
    notify();
    scheduleSessionSave();
  },
  openHit(hit: SearchHit) {
    const tabId = `${hit.textid}:${hit.juan_seq}`;
    const tabs = [
      { id: tabId, type: "text" as const, textid: hit.textid, seq: hit.juan_seq },
    ];
    const pane: PaneLeaf = {
      kind: "leaf",
      id: state.pane.id,
      tabs,
      activeTabId: tabId,
    };
    state = {
      ...state,
      activeTextid: hit.textid,
      activeSeq: hit.juan_seq,
      selection: null,
      currentPage: null,
      pane,
      pendingHighlight: {
        textid: hit.textid,
        seq: hit.juan_seq,
        bucket: hit.bucket,
        offset: hit.master_offset,
        length: hit.master_length,
      },
    };
    notify();
  },
  consumeHighlight() {
    if (state.pendingHighlight == null) return;
    state = { ...state, pendingHighlight: null };
    notify();
  },
  setLineMode(lineMode: LineMode) {
    const readPrefs = { ...state.readPrefs, lineMode };
    state = { ...state, readPrefs };
    saveReadPrefs(readPrefs);
    notify();
    scheduleSessionSave();
  },
  setPanelWidth(side: PanelSide, width: number) {
    const next = clampWidth(width, state.panelWidths[side], side);
    if (next === state.panelWidths[side]) return;
    const panelWidths = { ...state.panelWidths, [side]: next };
    state = { ...state, panelWidths };
    savePanelWidths(panelWidths);
    notify();
    scheduleSessionSave();
  },
};
