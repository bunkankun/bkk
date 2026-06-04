// Thin fetch wrappers around the BKK serve API.
//
// In dev the Vite proxy maps `/api/*` to `http://127.0.0.1:8000/*`
// (with the `/api` prefix stripped). In prod the SPA is mounted at
// the same origin as the API, so apiBase is empty.
//
// `getServerInfo` deliberately targets the literal `/api/info` endpoint
// (not `GET /`) because in prod the backend serves the SPA index at `/`.

import type {
  Annotation,
  AnnotationPostRequest,
  AnnotationPostResponse,
  AuthSession,
  BlueskyLoginRequest,
  BlueskyStatus,
  CatalogResponse,
  CategoriesResponse,
  CoreBacklinksResponse,
  CoreCollectionsResponse,
  CoreConceptWordsResponse,
  CoreListResponse,
  CoreRecordResponse,
  CoreSuperEntryByOrth,
  CoreSuperEntryExpansion,
  CoreSuperEntryFull,
  AnnotationsBySenseResponse,
  AnnotationsBySenseCountsResponse,
  Juan,
  Manifest,
  ManifestPart,
  OverlaysResponse,
  SearchResponse,
  SearchSort,
  SearchTextidsResponse,
  ServerInfo,
  ServerWelcome,
  TimelineResponse,
  TranslationAlignmentResponse,
  TranslationListResponse,
  TranslationSearchResponse,
  TranslationSort,
  SegmentTranslationsResponse,
  WorkspaceFile,
  WorkspaceFileList,
  WorkspaceDeleteResult,
  WorkspaceWriteResult,
} from "./types";

export const apiBase = import.meta.env.DEV ? "/api" : "";

const manifestCache = new Map<string, Promise<Manifest>>();

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  let body: unknown = null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    try {
      body = await res.json();
    } catch {
      body = null;
    }
  } else {
    try {
      body = await res.text();
    } catch {
      body = null;
    }
  }
  if (!res.ok) {
    throw new ApiError(res.status, `${res.status} ${res.statusText} for ${url}`, body);
  }
  return body as T;
}

export async function getServerInfo(): Promise<ServerInfo> {
  return fetchJson<ServerInfo>(`${apiBase}/server-info`);
}

export async function getServerWelcome(): Promise<ServerWelcome | null> {
  try {
    return await fetchJson<ServerWelcome>(`${apiBase}/server-welcome`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

export async function getAuthSession(): Promise<AuthSession> {
  return fetchJson<AuthSession>(`${apiBase}/auth/session`);
}

export function startGithubLogin(): void {
  window.location.assign(`${apiBase}/auth/github/start`);
}

export async function logout(): Promise<void> {
  await fetchJson<{ ok: boolean }>(`${apiBase}/auth/logout`, { method: "POST" });
}

function workspacePath(path: string): string {
  return path
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
}

export async function listWorkspaceFiles(prefix = ""): Promise<WorkspaceFileList> {
  const q = new URLSearchParams();
  if (prefix) q.set("prefix", prefix);
  const qs = q.toString();
  return fetchJson<WorkspaceFileList>(
    `${apiBase}/workspace/files${qs ? `?${qs}` : ""}`,
  );
}

export async function getWorkspaceFile(path: string): Promise<WorkspaceFile> {
  return fetchJson<WorkspaceFile>(
    `${apiBase}/workspace/files/${workspacePath(path)}`,
  );
}

export async function putWorkspaceFile(params: {
  path: string;
  content: string;
  sha?: string;
}): Promise<WorkspaceWriteResult> {
  return fetchJson<WorkspaceWriteResult>(
    `${apiBase}/workspace/files/${workspacePath(params.path)}`,
    {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ content: params.content, sha: params.sha }),
    },
  );
}

export async function deleteWorkspaceFile(params: {
  path: string;
  sha?: string;
}): Promise<WorkspaceDeleteResult> {
  const q = new URLSearchParams();
  if (params.sha) q.set("sha", params.sha);
  return fetchJson<WorkspaceDeleteResult>(
    `${apiBase}/workspace/files/${workspacePath(params.path)}${q.toString() ? `?${q.toString()}` : ""}`,
    {
      method: "DELETE",
    },
  );
}

export async function getCatalog(params?: {
  q?: string;
  century?: string;
  limit?: number;
  offset?: number;
  filters?: Record<string, string[]>;
}): Promise<CatalogResponse> {
  const q = new URLSearchParams();
  if (params?.q) q.set("q", params.q);
  if (params?.century) q.set("century", params.century);
  if (params?.limit != null) q.set("limit", String(params.limit));
  if (params?.offset != null) q.set("offset", String(params.offset));
  if (params?.filters) {
    for (const [k, vs] of Object.entries(params.filters)) {
      for (const v of vs) q.append(k, v);
    }
  }
  const qs = q.toString();
  return fetchJson<CatalogResponse>(`${apiBase}/catalog${qs ? `?${qs}` : ""}`);
}

export async function getCategories(): Promise<CategoriesResponse> {
  return fetchJson<CategoriesResponse>(`${apiBase}/catalog/categories`);
}

export async function getTimeline(): Promise<TimelineResponse> {
  return fetchJson<TimelineResponse>(`${apiBase}/catalog/timeline`);
}

export async function getManifest(textid: string): Promise<Manifest> {
  const cached = manifestCache.get(textid);
  if (cached) return cached;
  const request = fetchJson<Manifest>(
    `${apiBase}/bundles/${encodeURIComponent(textid)}/manifest`,
  ).catch((e) => {
    manifestCache.delete(textid);
    throw e;
  });
  manifestCache.set(textid, request);
  return request;
}

export async function getJuanList(textid: string): Promise<ManifestPart[]> {
  return fetchJson<ManifestPart[]>(`${apiBase}/bundles/${encodeURIComponent(textid)}/juan`);
}

export async function getJuan(textid: string, seq: number): Promise<Juan> {
  return fetchJson<Juan>(
    `${apiBase}/bundles/${encodeURIComponent(textid)}/juan/${seq}`,
  );
}

export async function getAnnotations(
  textid: string,
  seq: number,
): Promise<Annotation[]> {
  return fetchJson<Annotation[]>(
    `${apiBase}/bundles/${encodeURIComponent(textid)}/juan/${seq}/annotations`,
  );
}

export async function getBlueskyStatus(): Promise<BlueskyStatus> {
  return fetchJson<BlueskyStatus>(`${apiBase}/annotations/bluesky/session`);
}

export async function postBlueskyLogin(
  body: BlueskyLoginRequest,
): Promise<BlueskyStatus> {
  return fetchJson<BlueskyStatus>(`${apiBase}/annotations/bluesky/session`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function deleteBlueskySession(): Promise<{ ok: boolean }> {
  return fetchJson<{ ok: boolean }>(`${apiBase}/annotations/bluesky/session`, {
    method: "DELETE",
  });
}

export async function postAnnotation(
  body: AnnotationPostRequest,
): Promise<AnnotationPostResponse> {
  return fetchJson<AnnotationPostResponse>(`${apiBase}/annotations`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function getOverlays(): Promise<OverlaysResponse> {
  return fetchJson<OverlaysResponse>(`${apiBase}/overlays`);
}

export async function searchTranslations(params?: {
  q?: string;
  sourceTextid?: string;
  lang?: string;
  limit?: number;
  offset?: number;
}): Promise<TranslationListResponse> {
  const q = new URLSearchParams();
  if (params?.q) q.set("q", params.q);
  if (params?.sourceTextid) q.set("source_textid", params.sourceTextid);
  if (params?.lang) q.set("lang", params.lang);
  if (params?.limit != null) q.set("limit", String(params.limit));
  if (params?.offset != null) q.set("offset", String(params.offset));
  const qs = q.toString();
  return fetchJson<TranslationListResponse>(
    `${apiBase}/translations${qs ? `?${qs}` : ""}`,
  );
}

export async function searchTranslationSegments(params: {
  q: string;
  sort?: TranslationSort;
  lang?: string;
  category?: string;
  dateBefore?: number;
  dateAfter?: number;
  isAi?: boolean;
  includeSource?: boolean;
  limit?: number;
  offset?: number;
}): Promise<TranslationSearchResponse> {
  const q = new URLSearchParams({ q: params.q });
  if (params.sort) q.set("sort", params.sort);
  if (params.lang) q.set("lang", params.lang);
  if (params.category) q.set("category", params.category);
  if (params.dateBefore != null) q.set("date_before", String(params.dateBefore));
  if (params.dateAfter != null) q.set("date_after", String(params.dateAfter));
  if (params.isAi != null) q.set("is_ai", String(params.isAi));
  if (params.includeSource != null) q.set("include_source", String(params.includeSource));
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.offset != null) q.set("offset", String(params.offset));
  return fetchJson<TranslationSearchResponse>(`${apiBase}/translations/search?${q}`);
}

export async function getBundleTranslations(
  textid: string,
): Promise<TranslationListResponse> {
  return fetchJson<TranslationListResponse>(
    `${apiBase}/bundles/${encodeURIComponent(textid)}/translations`,
  );
}

export async function getTranslationAlignment(
  textid: string,
  seq: number,
  translationId: string,
): Promise<TranslationAlignmentResponse> {
  return fetchJson<TranslationAlignmentResponse>(
    `${apiBase}/bundles/${encodeURIComponent(textid)}/juan/${seq}/translations/${encodeURIComponent(translationId)}`,
  );
}

export async function getSegmentTranslations(
  textid: string,
  seq: number,
  corresp: string,
  sourceText: string,
): Promise<SegmentTranslationsResponse> {
  const params = new URLSearchParams({ corresp, source_text: sourceText });
  return fetchJson<SegmentTranslationsResponse>(
    `${apiBase}/bundles/${encodeURIComponent(textid)}/juan/${seq}/segment-translations?${params}`,
  );
}

export async function searchCorpus(params: {
  q: string;
  sort: SearchSort;
  textid?: string;
  textidNot?: string[];
  textids?: string[];
  witness?: string[];
  witnessNot?: string[];
  voice?: string[];
  voiceNot?: string[];
  category?: string[];
  categoryNot?: string[];
  categoryDescendants?: boolean;
  dateBefore?: number;
  dateAfter?: number;
  pivotTextid?: string;
  leftChar?: string[];
  leftCharNot?: string[];
  rightChar?: string[];
  rightCharNot?: string[];
  leftBigram?: string[];
  leftBigramNot?: string[];
  rightBigram?: string[];
  rightBigramNot?: string[];
  aroundBinom?: string[];
  aroundBinomNot?: string[];
  context?: number;
  limit?: number;
  offset?: number;
  facetLimit?: number;
  signal?: AbortSignal;
}): Promise<SearchResponse> {
  const q = new URLSearchParams();
  q.set("q", params.q);
  q.set("sort", params.sort);
  if (params.textid) q.set("textid", params.textid);
  if (params.textidNot) for (const id of params.textidNot) q.append("textid_not", id);
  if (params.textids) for (const id of params.textids) q.append("textids", id);
  if (params.witness) for (const w of params.witness) q.append("witness", w);
  if (params.witnessNot) for (const w of params.witnessNot) q.append("witness_not", w);
  if (params.voice) for (const v of params.voice) q.append("voice", v);
  if (params.voiceNot) for (const v of params.voiceNot) q.append("voice_not", v);
  if (params.category) for (const c of params.category) q.append("category", c);
  if (params.categoryNot) for (const c of params.categoryNot) q.append("category_not", c);
  if (params.categoryDescendants != null) {
    q.set("category_descendants", String(params.categoryDescendants));
  }
  if (params.dateBefore != null) q.set("date_before", String(params.dateBefore));
  if (params.dateAfter != null) q.set("date_after", String(params.dateAfter));
  if (params.pivotTextid) q.set("pivot_textid", params.pivotTextid);
  if (params.leftChar) for (const v of params.leftChar) q.append("left_char", v);
  if (params.leftCharNot) for (const v of params.leftCharNot) q.append("left_char_not", v);
  if (params.rightChar) for (const v of params.rightChar) q.append("right_char", v);
  if (params.rightCharNot) for (const v of params.rightCharNot) q.append("right_char_not", v);
  if (params.leftBigram) for (const v of params.leftBigram) q.append("left_bigram", v);
  if (params.leftBigramNot) for (const v of params.leftBigramNot) q.append("left_bigram_not", v);
  if (params.rightBigram) for (const v of params.rightBigram) q.append("right_bigram", v);
  if (params.rightBigramNot) for (const v of params.rightBigramNot) q.append("right_bigram_not", v);
  if (params.aroundBinom) for (const v of params.aroundBinom) q.append("around_binom", v);
  if (params.aroundBinomNot) for (const v of params.aroundBinomNot) q.append("around_binom_not", v);
  if (params.context != null) q.set("context", String(params.context));
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.offset != null) q.set("offset", String(params.offset));
  if (params.facetLimit != null) q.set("facet_limit", String(params.facetLimit));
  return fetchJson<SearchResponse>(`${apiBase}/search?${q.toString()}`, {
    signal: params.signal,
  });
}

export async function getCoreCollections(): Promise<CoreCollectionsResponse> {
  return fetchJson<CoreCollectionsResponse>(`${apiBase}/core/collections`);
}

export async function getCoreList(
  collection: string,
  params?: { q?: string; limit?: number; offset?: number },
): Promise<CoreListResponse> {
  const q = new URLSearchParams();
  if (params?.q) q.set("q", params.q);
  if (params?.limit != null) q.set("limit", String(params.limit));
  if (params?.offset != null) q.set("offset", String(params.offset));
  const qs = q.toString();
  return fetchJson<CoreListResponse>(
    `${apiBase}/core/${encodeURIComponent(collection)}${qs ? `?${qs}` : ""}`,
  );
}

export async function getCoreSuperEntry(uuid: string): Promise<CoreSuperEntryExpansion> {
  return fetchJson<CoreSuperEntryExpansion>(
    `${apiBase}/core/words/super-entry/${encodeURIComponent(uuid)}`,
  );
}

export async function getCoreRecord(
  collection: string,
  uuid: string,
): Promise<CoreRecordResponse> {
  return fetchJson<CoreRecordResponse>(
    `${apiBase}/core/${encodeURIComponent(collection)}/${encodeURIComponent(uuid)}`,
  );
}

export async function getCoreSuperEntryByOrth(
  orth: string,
): Promise<CoreSuperEntryByOrth> {
  return fetchJson<CoreSuperEntryByOrth>(
    `${apiBase}/core/super-entries/by-orth/${encodeURIComponent(orth)}`,
  );
}

export async function getCoreSuperEntryByOrthFull(
  orth: string,
): Promise<CoreSuperEntryFull> {
  return fetchJson<CoreSuperEntryFull>(
    `${apiBase}/core/super-entries/by-orth/${encodeURIComponent(orth)}/full`,
  );
}

export async function getAnnotationsBySense(
  senseUuid: string,
): Promise<AnnotationsBySenseResponse> {
  return fetchJson<AnnotationsBySenseResponse>(
    `${apiBase}/annotations/by-sense/${encodeURIComponent(senseUuid)}`,
  );
}

export async function getAnnotationSenseCounts(
  senseUuids: string[],
): Promise<AnnotationsBySenseCountsResponse> {
  return fetchJson<AnnotationsBySenseCountsResponse>(
    `${apiBase}/annotations/by-senses/counts`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sense_uuids: senseUuids }),
    },
  );
}

export async function getCoreConceptWords(
  uuid: string,
): Promise<CoreConceptWordsResponse> {
  return fetchJson<CoreConceptWordsResponse>(
    `${apiBase}/core/concepts/${encodeURIComponent(uuid)}/words`,
  );
}

export async function getCoreBacklinks(
  collection: string,
  uuid: string,
): Promise<CoreBacklinksResponse> {
  return fetchJson<CoreBacklinksResponse>(
    `${apiBase}/core/${encodeURIComponent(collection)}/${encodeURIComponent(uuid)}/backlinks`,
  );
}

export async function searchTextids(params: {
  q: string;
  sort: SearchSort;
  textid?: string;
  textidNot?: string[];
  textids?: string[];
  witness?: string[];
  witnessNot?: string[];
  voice?: string[];
  voiceNot?: string[];
  category?: string[];
  categoryNot?: string[];
  categoryDescendants?: boolean;
  dateBefore?: number;
  dateAfter?: number;
  leftChar?: string[];
  leftCharNot?: string[];
  rightChar?: string[];
  rightCharNot?: string[];
  leftBigram?: string[];
  leftBigramNot?: string[];
  rightBigram?: string[];
  rightBigramNot?: string[];
  aroundBinom?: string[];
  aroundBinomNot?: string[];
  context?: number;
}): Promise<SearchTextidsResponse> {
  const q = new URLSearchParams();
  q.set("q", params.q);
  q.set("sort", params.sort);
  if (params.textid) q.set("textid", params.textid);
  if (params.textidNot) for (const id of params.textidNot) q.append("textid_not", id);
  if (params.textids) for (const id of params.textids) q.append("textids", id);
  if (params.witness) for (const w of params.witness) q.append("witness", w);
  if (params.witnessNot) for (const w of params.witnessNot) q.append("witness_not", w);
  if (params.voice) for (const v of params.voice) q.append("voice", v);
  if (params.voiceNot) for (const v of params.voiceNot) q.append("voice_not", v);
  if (params.category) for (const c of params.category) q.append("category", c);
  if (params.categoryNot) for (const c of params.categoryNot) q.append("category_not", c);
  if (params.categoryDescendants != null) {
    q.set("category_descendants", String(params.categoryDescendants));
  }
  if (params.dateBefore != null) q.set("date_before", String(params.dateBefore));
  if (params.dateAfter != null) q.set("date_after", String(params.dateAfter));
  if (params.leftChar) for (const v of params.leftChar) q.append("left_char", v);
  if (params.leftCharNot) for (const v of params.leftCharNot) q.append("left_char_not", v);
  if (params.rightChar) for (const v of params.rightChar) q.append("right_char", v);
  if (params.rightCharNot) for (const v of params.rightCharNot) q.append("right_char_not", v);
  if (params.leftBigram) for (const v of params.leftBigram) q.append("left_bigram", v);
  if (params.leftBigramNot) for (const v of params.leftBigramNot) q.append("left_bigram_not", v);
  if (params.rightBigram) for (const v of params.rightBigram) q.append("right_bigram", v);
  if (params.rightBigramNot) for (const v of params.rightBigramNot) q.append("right_bigram_not", v);
  if (params.aroundBinom) for (const v of params.aroundBinom) q.append("around_binom", v);
  if (params.aroundBinomNot) for (const v of params.aroundBinomNot) q.append("around_binom_not", v);
  if (params.context != null) q.set("context", String(params.context));
  return fetchJson<SearchTextidsResponse>(`${apiBase}/search/textids?${q.toString()}`);
}
