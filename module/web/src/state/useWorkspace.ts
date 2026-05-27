// A small global store for v1 workspace state, built on React's
// useSyncExternalStore so we don't pull in redux/zustand.
// v1 has a single workspace pane — the types still allow the
// shape to grow into a pane tree later.

import { useSyncExternalStore } from "react";
import { getManifest, searchCorpus } from "../api/client";
import type { SearchHit, SearchResponse, SearchSort } from "../api/types";

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
  // v1 has a single leaf; kept so PaneTree.tsx can later host splits.
  pane: PaneLeaf;
  // search slice; ephemeral (no URL persistence in v1).
  search: SearchState;
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
  pendingHighlight: null,
  currentPage: null,
  readPrefs: loadReadPrefs(),
  panelWidths: loadPanelWidths(),
};

// monotonically increasing run id so an in-flight stale request can't clobber
// a newer one when the user submits twice quickly.
let searchRunId = 0;
let searchAbort: AbortController | null = null;

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
    };
    notify();
  },
  setServerInfo(info: WorkspaceState["serverInfo"]) {
    state = { ...state, serverInfo: info };
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
  },
  setPanelWidth(side: PanelSide, width: number) {
    const next = clampWidth(width, state.panelWidths[side], side);
    if (next === state.panelWidths[side]) return;
    const panelWidths = { ...state.panelWidths, [side]: next };
    state = { ...state, panelWidths };
    savePanelWidths(panelWidths);
    notify();
  },
};
