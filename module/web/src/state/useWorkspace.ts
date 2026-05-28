// A small global store for v1 workspace state, built on React's
// useSyncExternalStore so we don't pull in redux/zustand.
// v1 has a single workspace pane — the types still allow the
// shape to grow into a pane tree later.

import { useSyncExternalStore } from "react";
import {
  ApiError,
  deleteWorkspaceFile,
  getAuthSession,
  getWorkspaceFile,
  listWorkspaceFiles,
  getManifest,
  logout as logoutRequest,
  putWorkspaceFile,
  searchCorpus,
  searchTextids,
} from "../api/client";
import type {
  AuthSession,
  SearchHit,
  SearchResponse,
  SearchSort,
} from "../api/types";
import {
  addTextidsToContent,
  listColor,
  listNameFromPath,
  listPathFromName,
  parseTextList,
  serializeTextList,
} from "../lib/textLists";

export type Activity = "texts" | "catalog" | "timeline" | "lists";
export type RightTab = "annotations" | "chat" | "search";
export type ReadMode = "read" | "trans" | "inspect";
export type SearchTarget = "fulltext" | "dictionary" | "translations";
export type LineMode = "paragraph" | "phrase";
export type Theme = "current" | "dark" | "light";
export type ListFilterMode = "off" | "any" | "all";
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
  textidExclude: string[];
  category: string[];
  categoryExclude: string[];
  categoryDescendants: boolean;
  dateBefore: number | null;
  dateAfter: number | null;
  witness: string[];
  witnessExclude: string[];
  voice: string[];
  voiceExclude: string[];
  leftChar: string[];
  leftCharExclude: string[];
  rightChar: string[];
  rightCharExclude: string[];
  leftBigram: string[];
  leftBigramExclude: string[];
  rightBigram: string[];
  rightBigramExclude: string[];
  aroundBinom: string[];
  aroundBinomExclude: string[];
}

export interface SearchState {
  query: string;
  target: SearchTarget;
  sort: SearchSort;
  filters: SearchFilters;
  facetLimit: number;
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

export interface TextList {
  path: string;
  name: string;
  content: string;
  textids: string[];
  sha?: string;
  source: "local" | "remote";
}

export interface ListBadge {
  path: string;
  name: string;
  color: string;
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
  textLists: TextList[];
  activeListPaths: string[];
  listFilterMode: ListFilterMode;
  // a search-result span the TextViewer should scroll to + flash, then clear.
  pendingHighlight: PendingHighlight | null;
  // the page-break the user is currently viewing in Inspect mode; drives the
  // ImagePanel. Updated by TextViewer's page-anchor IntersectionObserver
  // and by ImagePanel's prev/next toolbar.
  currentPage: CurrentPage | null;
  // user-tunable read-mode display preferences (persisted in localStorage).
  readPrefs: { lineMode: LineMode };
  // broader UI preferences (persisted locally and, when logged in, in the
  // user's GitHub workspace session file).
  uiPrefs: { theme: Theme };
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
const UI_PREFS_KEY = "bkk.uiPrefs";
const PANEL_WIDTHS_KEY = "bkk.panelWidths";
const TEXT_LISTS_KEY = "bkk.textLists";
const LIST_PREFS_KEY = "bkk.listPrefs";
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

function coerceTheme(value: unknown, fallback: Theme = "current"): Theme {
  return value === "dark" || value === "light" || value === "current"
    ? value
    : fallback;
}

function loadUiPrefs(): { theme: Theme } {
  if (typeof window === "undefined") return { theme: "current" };
  try {
    const raw = window.localStorage.getItem(UI_PREFS_KEY);
    if (!raw) return { theme: "current" };
    const parsed = JSON.parse(raw);
    return { theme: coerceTheme(parsed?.theme) };
  } catch {
    return { theme: "current" };
  }
}

function saveUiPrefs(prefs: { theme: Theme }): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(UI_PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* localStorage disabled — silently keep state in memory only */
  }
}

function loadLocalTextLists(): TextList[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(TEXT_LISTS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item) => typeof item?.path === "string" && typeof item?.content === "string")
      .map((item) => {
        const fallback = listNameFromPath(item.path);
        const parsedList = parseTextList(item.content, fallback);
        return {
          path: item.path,
          name: parsedList.name ?? fallback,
          content: item.content,
          textids: parsedList.textids,
          source: "local" as const,
        };
      });
  } catch {
    return [];
  }
}

function saveLocalTextLists(lists: TextList[]): void {
  if (typeof window === "undefined") return;
  try {
    const localLists = lists.map((list) => ({
      path: list.path,
      content: list.content,
    }));
    window.localStorage.setItem(TEXT_LISTS_KEY, JSON.stringify(localLists));
  } catch {
    /* localStorage disabled — silently keep state in memory only */
  }
}

function loadListFilterMode(): ListFilterMode {
  if (typeof window === "undefined") return "off";
  try {
    const raw = window.localStorage.getItem(LIST_PREFS_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed?.filterMode === "any" || parsed?.filterMode === "all"
      ? parsed.filterMode
      : "off";
  } catch {
    return "off";
  }
}

function saveListPrefs(mode: ListFilterMode): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(LIST_PREFS_KEY, JSON.stringify({ filterMode: mode }));
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
      textidExclude: [],
      category: [],
      categoryExclude: [],
      categoryDescendants: true,
      dateBefore: null,
      dateAfter: null,
      witness: [],
      witnessExclude: [],
      voice: [],
      voiceExclude: [],
      leftChar: [],
      leftCharExclude: [],
      rightChar: [],
      rightCharExclude: [],
      leftBigram: [],
      leftBigramExclude: [],
      rightBigram: [],
      rightBigramExclude: [],
      aroundBinom: [],
      aroundBinomExclude: [],
    },
    facetLimit: 12,
    status: "idle",
    error: null,
    response: null,
  },
  searchHistory: [],
  textLists: loadLocalTextLists(),
  activeListPaths: [],
  listFilterMode: loadListFilterMode(),
  pendingHighlight: null,
  currentPage: null,
  readPrefs: loadReadPrefs(),
  uiPrefs: loadUiPrefs(),
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
      textids: scopedListTextids(),
      offset,
      textid: filters.textid ?? undefined,
      textidNot: filters.textidExclude,
      witness: filters.witness,
      witnessNot: filters.witnessExclude,
      voice: filters.voice,
      voiceNot: filters.voiceExclude,
      category: filters.category,
      categoryNot: filters.categoryExclude,
      categoryDescendants: filters.categoryDescendants,
      dateBefore: filters.dateBefore ?? undefined,
      dateAfter: filters.dateAfter ?? undefined,
      pivotTextid: state.activeTextid ?? undefined,
      leftChar: filters.leftChar,
      leftCharNot: filters.leftCharExclude,
      rightChar: filters.rightChar,
      rightCharNot: filters.rightCharExclude,
      leftBigram: filters.leftBigram,
      leftBigramNot: filters.leftBigramExclude,
      rightBigram: filters.rightBigram,
      rightBigramNot: filters.rightBigramExclude,
      aroundBinom: filters.aroundBinom,
      aroundBinomNot: filters.aroundBinomExclude,
      facetLimit: state.search.facetLimit,
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

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

const excludeKeyByFacet: Record<SearchFacetKind, keyof SearchFilters> = {
  category: "categoryExclude",
  witness: "witnessExclude",
  voice: "voiceExclude",
  leftChar: "leftCharExclude",
  rightChar: "rightCharExclude",
  leftBigram: "leftBigramExclude",
  rightBigram: "rightBigramExclude",
  aroundBinom: "aroundBinomExclude",
};

function coerceSearchFilters(value: unknown): SearchFilters {
  const rec =
    typeof value === "object" && value != null
      ? (value as Partial<Record<keyof SearchFilters, unknown>>)
      : {};
  return {
    textid: typeof rec.textid === "string" ? rec.textid : null,
    textidExclude: stringArray(rec.textidExclude),
    category: stringArray(rec.category),
    categoryExclude: stringArray(rec.categoryExclude),
    categoryDescendants:
      typeof rec.categoryDescendants === "boolean" ? rec.categoryDescendants : true,
    dateBefore: typeof rec.dateBefore === "number" ? rec.dateBefore : null,
    dateAfter: typeof rec.dateAfter === "number" ? rec.dateAfter : null,
    witness: stringArray(rec.witness),
    witnessExclude: stringArray(rec.witnessExclude),
    voice: stringArray(rec.voice),
    voiceExclude: stringArray(rec.voiceExclude),
    leftChar: stringArray(rec.leftChar),
    leftCharExclude: stringArray(rec.leftCharExclude),
    rightChar: stringArray(rec.rightChar),
    rightCharExclude: stringArray(rec.rightCharExclude),
    leftBigram: stringArray(rec.leftBigram),
    leftBigramExclude: stringArray(rec.leftBigramExclude),
    rightBigram: stringArray(rec.rightBigram),
    rightBigramExclude: stringArray(rec.rightBigramExclude),
    aroundBinom: stringArray(rec.aroundBinom),
    aroundBinomExclude: stringArray(rec.aroundBinomExclude),
  };
}

function resetSearchFilters(filters: SearchFilters): SearchFilters {
  return {
    ...filters,
    textid: null,
    textidExclude: [],
    category: [],
    categoryExclude: [],
    dateBefore: null,
    dateAfter: null,
    witness: [],
    witnessExclude: [],
    voice: [],
    voiceExclude: [],
    leftChar: [],
    leftCharExclude: [],
    rightChar: [],
    rightCharExclude: [],
    leftBigram: [],
    leftBigramExclude: [],
    rightBigram: [],
    rightBigramExclude: [],
    aroundBinom: [],
    aroundBinomExclude: [],
  };
}

function cloneSearchFilters(filters: SearchFilters): SearchFilters {
  const safe = coerceSearchFilters(filters);
  return {
    textid: safe.textid,
    textidExclude: [...safe.textidExclude],
    category: [...safe.category],
    categoryExclude: [...safe.categoryExclude],
    categoryDescendants: safe.categoryDescendants,
    dateBefore: safe.dateBefore,
    dateAfter: safe.dateAfter,
    witness: [...safe.witness],
    witnessExclude: [...safe.witnessExclude],
    voice: [...safe.voice],
    voiceExclude: [...safe.voiceExclude],
    leftChar: [...safe.leftChar],
    leftCharExclude: [...safe.leftCharExclude],
    rightChar: [...safe.rightChar],
    rightCharExclude: [...safe.rightCharExclude],
    leftBigram: [...safe.leftBigram],
    leftBigramExclude: [...safe.leftBigramExclude],
    rightBigram: [...safe.rightBigram],
    rightBigramExclude: [...safe.rightBigramExclude],
    aroundBinom: [...safe.aroundBinom],
    aroundBinomExclude: [...safe.aroundBinomExclude],
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

function textListFromContent(path: string, content: string, sha?: string): TextList {
  const fallback = listNameFromPath(path);
  const parsed = parseTextList(content, fallback);
  return {
    path,
    name: parsed.name ?? fallback,
    content,
    textids: parsed.textids,
    sha,
    source: sha ? "remote" : "local",
  };
}

function replaceTextList(list: TextList): void {
  const others = state.textLists.filter((item) => item.path !== list.path);
  const textLists = [...others, list].sort((a, b) => a.name.localeCompare(b.name));
  state = {
    ...state,
    textLists,
    activeListPaths: state.activeListPaths.filter((path) =>
      textLists.some((item) => item.path === path),
    ),
  };
  saveLocalTextLists(textLists);
}

function scopedListTextids(): string[] | undefined {
  if (state.listFilterMode === "off") return undefined;
  const active = state.textLists.filter((list) => state.activeListPaths.includes(list.path));
  if (active.length === 0) return undefined;
  if (state.listFilterMode === "any") {
    return [...new Set(active.flatMap((list) => list.textids))].sort();
  }
  const [head, ...tail] = active;
  const intersection = new Set(head.textids);
  for (const list of tail) {
    const ids = new Set(list.textids);
    for (const id of [...intersection]) {
      if (!ids.has(id)) intersection.delete(id);
    }
  }
  return [...intersection].sort();
}

function searchParamsForLists() {
  const { query, sort, filters } = state.search;
  return {
    q: query,
    sort,
    textid: filters.textid ?? undefined,
    textidNot: filters.textidExclude,
    textids: scopedListTextids(),
    witness: filters.witness,
    witnessNot: filters.witnessExclude,
    voice: filters.voice,
    voiceNot: filters.voiceExclude,
    category: filters.category,
    categoryNot: filters.categoryExclude,
    categoryDescendants: filters.categoryDescendants,
    dateBefore: filters.dateBefore ?? undefined,
    dateAfter: filters.dateAfter ?? undefined,
    leftChar: filters.leftChar,
    leftCharNot: filters.leftCharExclude,
    rightChar: filters.rightChar,
    rightCharNot: filters.rightCharExclude,
    leftBigram: filters.leftBigram,
    leftBigramNot: filters.leftBigramExclude,
    rightBigram: filters.rightBigram,
    rightBigramNot: filters.rightBigramExclude,
    aroundBinom: filters.aroundBinom,
    aroundBinomNot: filters.aroundBinomExclude,
  };
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
  return {
    id: rec.id,
    query: rec.query,
    target: rec.target as SearchTarget,
    sort: rec.sort as SearchSort,
    filters: coerceSearchFilters(rec.filters),
    pivotTextid: typeof rec.pivotTextid === "string" ? rec.pivotTextid : null,
    createdAt: rec.createdAt,
  };
}

function uniqueSearchHistory(entries: SearchHistoryEntry[]): SearchHistoryEntry[] {
  const seen = new Set<string>();
  const out: SearchHistoryEntry[] = [];
  for (const entry of entries) {
    const key = entry.query.trim();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(entry);
  }
  return out;
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
  const deduped = state.searchHistory.filter((item) => item.query.trim() !== q);
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
      uiPrefs: state.uiPrefs,
      panelWidths: state.panelWidths,
      activeListPaths: state.activeListPaths,
      listFilterMode: state.listFilterMode,
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
        readPrefs?: unknown;
        uiPrefs?: unknown;
        panelWidths?: unknown;
        activeListPaths?: unknown;
        listFilterMode?: unknown;
      }>(SESSION_PATH),
    ]);
    await loadRemoteTextLists();
    const entries = Array.isArray(historyDoc?.entries)
      ? historyDoc.entries
          .map(validSearchHistoryEntry)
          .filter((item): item is SearchHistoryEntry => item != null)
      : [];
    const uniqueEntries = uniqueSearchHistory(entries).slice(0, MAX_SEARCH_HISTORY);
    state = {
      ...state,
      searchHistory: uniqueEntries,
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
      const sessionReadPrefs =
        typeof sessionDoc.readPrefs === "object" && sessionDoc.readPrefs != null
          ? (sessionDoc.readPrefs as { lineMode?: unknown })
          : null;
      const readPrefs = sessionReadPrefs
        ? {
            ...state.readPrefs,
            lineMode:
              sessionReadPrefs.lineMode === "phrase" ? "phrase" : state.readPrefs.lineMode,
          }
        : state.readPrefs;
      const sessionUiPrefs =
        typeof sessionDoc.uiPrefs === "object" && sessionDoc.uiPrefs != null
          ? (sessionDoc.uiPrefs as { theme?: unknown })
          : null;
      const uiPrefs = sessionUiPrefs
        ? { ...state.uiPrefs, theme: coerceTheme(sessionUiPrefs.theme, state.uiPrefs.theme) }
        : state.uiPrefs;
      const sessionPanelWidths =
        typeof sessionDoc.panelWidths === "object" && sessionDoc.panelWidths != null
          ? (sessionDoc.panelWidths as { left?: unknown; right?: unknown; inspect?: unknown })
          : null;
      const panelWidths = sessionPanelWidths
        ? {
            left: clampWidth(sessionPanelWidths.left, state.panelWidths.left, "left"),
            right: clampWidth(sessionPanelWidths.right, state.panelWidths.right, "right"),
            inspect: clampWidth(sessionPanelWidths.inspect, state.panelWidths.inspect, "inspect"),
          }
        : state.panelWidths;
      const activeListPaths = Array.isArray(sessionDoc.activeListPaths)
        ? sessionDoc.activeListPaths.filter((item): item is string => typeof item === "string")
        : state.activeListPaths;
      const listFilterMode =
        sessionDoc.listFilterMode === "any" || sessionDoc.listFilterMode === "all"
          ? sessionDoc.listFilterMode
          : state.listFilterMode;
      if (sessionReadPrefs) saveReadPrefs(readPrefs);
      if (sessionUiPrefs) saveUiPrefs(uiPrefs);
      if (sessionPanelWidths) savePanelWidths(panelWidths);
      saveListPrefs(listFilterMode);
      state = {
        ...state,
        readMode,
        rightTab,
        readPrefs,
        uiPrefs,
        panelWidths,
        activeListPaths,
        listFilterMode,
      };
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

async function loadRemoteTextLists(): Promise<void> {
  if (state.auth.status !== "authenticated") return;
  const localBeforeLogin = loadLocalTextLists();
  const listing = await listWorkspaceFiles("lists/");
  const remoteLists: TextList[] = [];
  for (const entry of listing.files) {
    if (entry.type !== "file" || !entry.path.endsWith(".txt")) continue;
    const file = await getWorkspaceFile(entry.path);
    workspaceFileShas[entry.path] = file.sha;
    remoteLists.push(textListFromContent(entry.path, file.content, file.sha));
  }
  let merged = [...remoteLists];
  for (const local of localBeforeLogin) {
    const remote = merged.find((item) => item.path === local.path);
    if (!remote) {
      const result = await putWorkspaceFile({ path: local.path, content: local.content });
      workspaceFileShas[local.path] = result.sha ?? undefined;
      merged.push({ ...local, sha: result.sha ?? undefined, source: "remote" });
      continue;
    }
    if (remote.content === local.content) continue;
    const choice = typeof window !== "undefined"
      ? window.prompt(
          `List "${remote.name}" exists locally and in your GitHub workspace. Type merge, remote, or local.`,
          "merge",
        )
      : "remote";
    if (choice === "local") {
      const result = await putWorkspaceFile({
        path: remote.path,
        content: local.content,
        sha: remote.sha,
      });
      const next = textListFromContent(remote.path, local.content, result.sha ?? undefined);
      merged = merged.map((item) => (item.path === remote.path ? next : item));
    } else if (choice === "merge") {
      const content = addTextidsToContent(remote.content, remote.name, local.textids);
      const result = await putWorkspaceFile({
        path: remote.path,
        content,
        sha: remote.sha,
      });
      const next = textListFromContent(remote.path, content, result.sha ?? undefined);
      merged = merged.map((item) => (item.path === remote.path ? next : item));
    }
  }
  state = {
    ...state,
    textLists: merged.sort((a, b) => a.name.localeCompare(b.name)),
    activeListPaths: state.activeListPaths.filter((path) =>
      merged.some((item) => item.path === path),
    ),
  };
  saveLocalTextLists(state.textLists);
  notify();
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
      textLists: loadLocalTextLists(),
      activeListPaths: [],
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
    const textidExclude = textid
      ? state.search.filters.textidExclude.filter((v) => v !== textid)
      : state.search.filters.textidExclude;
    state = {
      ...state,
      search: {
        ...state.search,
        filters: { ...state.search.filters, textid, textidExclude },
      },
    };
    notify();
    return runSearchInternal(0);
  },
  toggleSearchTextidExclude(textid: string) {
    const filters = state.search.filters;
    state = {
      ...state,
      search: {
        ...state.search,
        filters: {
          ...filters,
          textid: filters.textid === textid ? null : filters.textid,
          textidExclude: toggled(filters.textidExclude, textid),
        },
      },
    };
    notify();
    return runSearchInternal(0);
  },
  toggleSearchFacet(
    kind: SearchFacetKind,
    value: string,
    mode: "include" | "exclude" = "include",
  ) {
    const filters = state.search.filters;
    const excludeKey = excludeKeyByFacet[kind];
    const includeValues = filters[kind];
    const excludeValues = filters[excludeKey] as string[];
    const nextFilters = mode === "exclude"
      ? {
          ...filters,
          [kind]: includeValues.filter((v) => v !== value),
          [excludeKey]: toggled(excludeValues, value),
        }
      : {
          ...filters,
          [kind]: toggled(includeValues, value),
          [excludeKey]: excludeValues.filter((v) => v !== value),
        };
    state = {
      ...state,
      search: {
        ...state.search,
        filters: nextFilters,
      },
    };
    notify();
    return runSearchInternal(0);
  },
  setSearchFacetLimit(facetLimit: number) {
    state = { ...state, search: { ...state.search, facetLimit } };
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
        facetLimit: 12,
      },
    };
    return runSearchInternal(0);
  },
  runSearchAt(offset: number) {
    return runSearchInternal(offset);
  },
  async saveSearchAsTextList(path: string | null = null) {
    const params = searchParamsForLists();
    if (!params.q.trim()) return;
    const result = await searchTextids(params);
    const suggested = path ?? listPathFromName(`Search ${params.q}`);
    const existing = state.textLists.find((item) => item.path === suggested);
    const name = existing?.name ?? listNameFromPath(suggested);
    const entries = (result.entries ?? []).map((entry) => ({
      textid: entry.textid,
      hitCount: entry.hit_count,
      title: entry.title,
    }));
    const content = addTextidsToContent(existing?.content ?? "", name, result.textids, {
      source: "search",
      query: result.query,
      hit_count: result.hit_count,
      text_count: result.text_count,
      columns: "textid hit_count title",
    }, entries);
    return workspace.saveTextList(suggested, content);
  },
  async createTextList(name: string) {
    const path = listPathFromName(name);
    const content = serializeTextList({ name, textids: [] });
    return workspace.saveTextList(path, content);
  },
  async saveTextList(path: string, content: string) {
    const list = textListFromContent(path, content, workspaceFileShas[path]);
    if (state.auth.status === "authenticated") {
      state = { ...state, persistence: { status: "saving", error: null } };
      notify();
      try {
        const result = await putWorkspaceFile({
          path,
          content,
          sha: workspaceFileShas[path],
        });
        workspaceFileShas[path] = result.sha ?? undefined;
        replaceTextList({ ...list, sha: result.sha ?? undefined, source: "remote" });
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
    } else {
      replaceTextList(list);
      notify();
    }
  },
  async deleteTextList(path: string) {
    const nextLists = state.textLists.filter((item) => item.path !== path);
    if (state.auth.status === "authenticated" && workspaceFileShas[path]) {
      state = { ...state, persistence: { status: "saving", error: null } };
      notify();
      try {
        await deleteWorkspaceFile({ path, sha: workspaceFileShas[path] });
        delete workspaceFileShas[path];
      } catch (e) {
        state = {
          ...state,
          persistence: {
            status: "error",
            error: e instanceof Error ? e.message : String(e),
          },
        };
        notify();
        return;
      }
    }
    state = {
      ...state,
      textLists: nextLists,
      activeListPaths: state.activeListPaths.filter((p) => p !== path),
      persistence: { status: "idle", error: null },
    };
    saveLocalTextLists(nextLists);
    notify();
  },
  async renameTextList(path: string, name: string) {
    const list = state.textLists.find((item) => item.path === path);
    if (!list) return;
    const nextPath = listPathFromName(name);
    const content = serializeTextList({
      name,
      textids: list.textids,
      existingContent: list.content,
    });
    await workspace.saveTextList(nextPath, content);
    if (nextPath !== path) await workspace.deleteTextList(path);
  },
  async addTextToList(path: string, textid: string) {
    const list = state.textLists.find((item) => item.path === path);
    if (!list) return;
    const content = addTextidsToContent(list.content, list.name, [textid]);
    await workspace.saveTextList(path, content);
  },
  async addCurrentTextToList(path: string) {
    if (!state.activeTextid) return;
    await workspace.addTextToList(path, state.activeTextid);
  },
  setListActive(path: string, active: boolean) {
    const activeListPaths = active
      ? [...new Set([...state.activeListPaths, path])]
      : state.activeListPaths.filter((item) => item !== path);
    state = { ...state, activeListPaths };
    notify();
    scheduleSessionSave();
    if (state.listFilterMode !== "off" && state.search.status !== "idle") {
      return runSearchInternal(0);
    }
  },
  setListFilterMode(mode: ListFilterMode) {
    state = { ...state, listFilterMode: mode };
    saveListPrefs(mode);
    notify();
    scheduleSessionSave();
    if (state.search.status !== "idle") return runSearchInternal(0);
  },
  listBadgesForTextid(textid: string): ListBadge[] {
    return state.textLists
      .filter((list) => state.activeListPaths.includes(list.path) && list.textids.includes(textid))
      .map((list) => ({ path: list.path, name: list.name, color: listColor(list.path) }));
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
        facetLimit: 12,
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
  setTheme(theme: Theme) {
    const next = coerceTheme(theme, state.uiPrefs.theme);
    if (next === state.uiPrefs.theme) return;
    const uiPrefs = { ...state.uiPrefs, theme: next };
    state = { ...state, uiPrefs };
    saveUiPrefs(uiPrefs);
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
