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

export interface SearchState {
  query: string;
  target: SearchTarget;
  sort: SearchSort;
  status: "idle" | "loading" | "ok" | "error";
  error: string | null;
  response: SearchResponse | null;
}

export interface PendingHighlight {
  textid: string;
  seq: number;
  offset: number;
  length: number;
}

export interface SelectionRange {
  textid: string;
  seq: number;
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
  // user-tunable read-mode display preferences (persisted in localStorage).
  readPrefs: { lineMode: LineMode };
  // user-tunable panel widths, persisted in localStorage. The handle
  // between activity-bar and left panel adjusts `left`; the one between
  // workspace and right panel adjusts `right`.
  panelWidths: { left: number; right: number };
}

const READ_PREFS_KEY = "bkk.readPrefs";
const PANEL_WIDTHS_KEY = "bkk.panelWidths";
const DEFAULT_LEFT_WIDTH = 240;
const DEFAULT_RIGHT_WIDTH = 360;
export const PANEL_MIN_WIDTH = 180;
export const PANEL_MAX_WIDTH = 600;

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

function clampWidth(n: unknown, fallback: number): number {
  if (typeof n !== "number" || !Number.isFinite(n)) return fallback;
  return Math.max(PANEL_MIN_WIDTH, Math.min(PANEL_MAX_WIDTH, Math.round(n)));
}

function loadPanelWidths(): { left: number; right: number } {
  if (typeof window === "undefined") {
    return { left: DEFAULT_LEFT_WIDTH, right: DEFAULT_RIGHT_WIDTH };
  }
  try {
    const raw = window.localStorage.getItem(PANEL_WIDTHS_KEY);
    if (!raw) return { left: DEFAULT_LEFT_WIDTH, right: DEFAULT_RIGHT_WIDTH };
    const parsed = JSON.parse(raw);
    return {
      left: clampWidth(parsed?.left, DEFAULT_LEFT_WIDTH),
      right: clampWidth(parsed?.right, DEFAULT_RIGHT_WIDTH),
    };
  } catch {
    return { left: DEFAULT_LEFT_WIDTH, right: DEFAULT_RIGHT_WIDTH };
  }
}

function savePanelWidths(widths: { left: number; right: number }): void {
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
    status: "idle",
    error: null,
    response: null,
  },
  pendingHighlight: null,
  readPrefs: loadReadPrefs(),
  panelWidths: loadPanelWidths(),
};

// monotonically increasing run id so an in-flight stale request can't clobber
// a newer one when the user submits twice quickly.
let searchRunId = 0;

async function runSearchInternal(offset: number): Promise<void> {
  const { query, target, sort } = state.search;
  if (!query.trim() || target !== "fulltext") return;
  const runId = ++searchRunId;
  state = {
    ...state,
    search: { ...state.search, status: "loading", error: null },
    rightTab: "search",
  };
  notify();
  try {
    const response = await searchCorpus({ q: query, sort, offset });
    if (runId !== searchRunId) return;
    state = {
      ...state,
      search: { ...state.search, status: "ok", error: null, response },
    };
    notify();
  } catch (e) {
    if (runId !== searchRunId) return;
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
      pane,
    };
    notify();
  },
  setServerInfo(info: WorkspaceState["serverInfo"]) {
    state = { ...state, serverInfo: info };
    notify();
  },
  setSearchQuery(query: string) {
    state = { ...state, search: { ...state.search, query } };
    notify();
  },
  setSearchTarget(target: SearchTarget) {
    state = { ...state, search: { ...state.search, target } };
    notify();
  },
  setSearchSort(sort: SearchSort) {
    state = { ...state, search: { ...state.search, sort } };
    notify();
  },
  runSearch() {
    return runSearchInternal(0);
  },
  runSearchAt(offset: number) {
    return runSearchInternal(offset);
  },
  clearSearch() {
    searchRunId++;
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
      pane,
      pendingHighlight: {
        textid: hit.textid,
        seq: hit.juan_seq,
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
  setPanelWidth(side: "left" | "right", width: number) {
    const next = clampWidth(width, state.panelWidths[side]);
    if (next === state.panelWidths[side]) return;
    const panelWidths = { ...state.panelWidths, [side]: next };
    state = { ...state, panelWidths };
    savePanelWidths(panelWidths);
    notify();
  },
};
