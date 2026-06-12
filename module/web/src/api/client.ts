// Thin fetch wrappers around the BKK serve API.
//
// All backend routes live under `/api`. In dev the Vite proxy forwards
// `/api/*` to `http://127.0.0.1:8000/api/*` unchanged; in prod the SPA
// is mounted at the same origin as the API.

import type {
  AdminInfoResponse,
  AdminJob,
  Annotation,
  AnnotationPostRequest,
  AnnotationPostResponse,
  CommentPostRequest,
  PostResponse,
  TranslationPostRequest,
  AuthSession,
  BlueskyLoginRequest,
  BlueskyStatus,
  BundleSearchResponse,
  CatalogResponse,
  CategoriesResponse,
  ContributionsResponse,
  CoreBacklinksResponse,
  CoreCollectionsResponse,
  CoreConceptWordsResponse,
  CoreDeleteRequest,
  CoreDeleteResponse,
  CoreEditRequest,
  CoreEditResponse,
  CoreListResponse,
  CoreOpenPrRequest,
  CoreOpenPrResponse,
  CoreRecordResponse,
  SyntacticFunctionLintResponse,
  CoreSuperEntryByOrth,
  CoreSuperEntryExpansion,
  CoreSuperEntryFull,
  AnnotationsBySenseResponse,
  AnnotationsBySenseCountsResponse,
  AnnotationsByRhetoricalDeviceResponse,
  AnnotationsByRhetoricalDeviceCountsResponse,
  Juan,
  Manifest,
  ManifestPart,
  OverlaysResponse,
  ParallelBucket,
  ParallelSearchResponse,
  ParallelSort,
  Rating,
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

export const apiBase = "/api";

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
  // Refuse a 200 OK that isn't JSON — typically means the request fell through
  // to the SPA fallback (HTML) or a proxy interstitial, and returning that as
  // T would crash downstream consumers with confusing undefined-access errors.
  if (!ct.includes("application/json")) {
    throw new ApiError(res.status, `non-JSON response (content-type: ${ct || "none"}) for ${url}`, body);
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

// ---------- admin ----------

export async function getAdminInfo(): Promise<AdminInfoResponse> {
  return fetchJson<AdminInfoResponse>(`${apiBase}/admin/info`);
}

export async function getAdminJob(id: string): Promise<AdminJob> {
  return fetchJson<AdminJob>(`${apiBase}/admin/jobs/${encodeURIComponent(id)}`);
}

export async function postAdminIndex(): Promise<AdminJob> {
  return fetchJson<AdminJob>(`${apiBase}/admin/index`, { method: "POST" });
}

export async function postAdminIndexOne(textid: string): Promise<AdminJob> {
  return fetchJson<AdminJob>(
    `${apiBase}/admin/index/${encodeURIComponent(textid)}`,
    { method: "POST" },
  );
}

export async function postAdminCatalog(): Promise<AdminJob> {
  return fetchJson<AdminJob>(`${apiBase}/admin/catalog`, { method: "POST" });
}

export async function postAdminTranslations(): Promise<AdminJob> {
  return fetchJson<AdminJob>(`${apiBase}/admin/translations`, { method: "POST" });
}

export async function postAdminAnnotations(): Promise<AdminJob> {
  return fetchJson<AdminJob>(`${apiBase}/admin/annotations`, { method: "POST" });
}

export async function postAdminValidate(textid: string): Promise<AdminJob> {
  return fetchJson<AdminJob>(
    `${apiBase}/admin/validate/${encodeURIComponent(textid)}`,
    { method: "POST" },
  );
}

export async function postAdminCoreSync(): Promise<AdminJob> {
  return fetchJson<AdminJob>(`${apiBase}/admin/core/sync`, { method: "POST" });
}

export async function postAdminUpdate(): Promise<AdminJob> {
  return fetchJson<AdminJob>(`${apiBase}/admin/update`, { method: "POST" });
}

export async function postAdminRestart(): Promise<{ status: string }> {
  return fetchJson<{ status: string }>(`${apiBase}/admin/restart`, { method: "POST" });
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

export async function getBundleSearch(
  textid: string,
  q: string,
  opts: { signal?: AbortSignal; context?: number; limit?: number; masterOnly?: boolean } = {},
): Promise<BundleSearchResponse> {
  const params = new URLSearchParams({ q });
  if (opts.context != null) params.set("context", String(opts.context));
  if (opts.limit != null) params.set("limit", String(opts.limit));
  if (opts.masterOnly) params.set("master_only", "true");
  return fetchJson<BundleSearchResponse>(
    `${apiBase}/bundles/${encodeURIComponent(textid)}/search?${params.toString()}`,
    { signal: opts.signal },
  );
}

export async function getJuanList(textid: string): Promise<ManifestPart[]> {
  return fetchJson<ManifestPart[]>(`${apiBase}/bundles/${encodeURIComponent(textid)}/juan`);
}

export async function getJuan(textid: string, seq: number): Promise<Juan> {
  return fetchJson<Juan>(
    `${apiBase}/bundles/${encodeURIComponent(textid)}/juan/${seq}`,
  );
}

const annotationsCache = new Map<string, Promise<Annotation[]>>();

function annotationsKey(textid: string, seq: number): string {
  return `${textid}_${seq}`;
}

export function invalidateAnnotationsCache(textid: string, seq: number): void {
  annotationsCache.delete(annotationsKey(textid, seq));
}

export async function getAnnotations(
  textid: string,
  seq: number,
): Promise<Annotation[]> {
  const key = annotationsKey(textid, seq);
  const cached = annotationsCache.get(key);
  if (cached != null) return cached;
  const promise = fetchJson<Annotation[]>(
    `${apiBase}/bundles/${encodeURIComponent(textid)}/juan/${seq}/annotations`,
  ).catch((err) => {
    annotationsCache.delete(key);
    throw err;
  });
  annotationsCache.set(key, promise);
  return promise;
}

export interface ArchiveDeleteResponse {
  text_id: string;
  juan_seq: number;
  id: string;
  deleted: boolean;
}

export async function archiveDeleteAnnotation(
  textid: string,
  seq: number,
  id: string,
): Promise<ArchiveDeleteResponse> {
  const res = await fetchJson<ArchiveDeleteResponse>(
    `${apiBase}/bundles/${encodeURIComponent(textid)}/juan/${seq}/annotations/${encodeURIComponent(id)}`,
    { method: "DELETE" },
  );
  invalidateAnnotationsCache(textid, seq);
  return res;
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

export async function postComment(
  body: CommentPostRequest,
): Promise<PostResponse> {
  return fetchJson<PostResponse>(`${apiBase}/comments`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function postTranslation(
  body: TranslationPostRequest,
): Promise<PostResponse> {
  return fetchJson<PostResponse>(`${apiBase}/translations`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function getContributions(
  limit = 200,
): Promise<ContributionsResponse> {
  const q = new URLSearchParams({ limit: String(limit) });
  return fetchJson<ContributionsResponse>(`${apiBase}/contributions?${q}`);
}

export type CurationState =
  | "proposed"
  | "accepted"
  | "rejected"
  | "superseded";

export interface CurationStateResponse {
  uri: string;
  text_id: string;
  juan_seq: number | null;
  curation_state: CurationState;
  rating: Rating;
  curation_uri: string;
}

export async function patchContributionCuration(
  uri: string,
  patch: { state?: CurationState; rating?: Rating },
): Promise<CurationStateResponse> {
  const res = await fetchJson<CurationStateResponse>(
    `${apiBase}/annotations/curation-state`,
    {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ uri, ...patch }),
    },
  );
  if (res.juan_seq != null) {
    invalidateAnnotationsCache(res.text_id, res.juan_seq);
  }
  return res;
}

export interface LocalRatingResponse {
  text_id: string;
  juan_seq: number;
  id: string;
  rating: Rating;
}

export async function patchLocalRating(params: {
  text_id: string;
  juan_seq: number;
  id: string;
  rating: Rating;
}): Promise<LocalRatingResponse> {
  return fetchJson<LocalRatingResponse>(
    `${apiBase}/annotations/local-rating`,
    {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(params),
    },
  );
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
  masterOnly?: boolean;
  maxResults?: number;
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
  if (params.masterOnly) q.set("master_only", "true");
  if (params.maxResults != null) q.set("max_results", String(params.maxResults));
  return fetchJson<SearchResponse>(`${apiBase}/search?${q.toString()}`, {
    signal: params.signal,
  });
}

export async function searchParallel(params: {
  q: string;
  bucket?: ParallelBucket;
  minLength?: number;
  minOccurrences?: number;
  maxPostings?: number;
  context?: number;
  includeContained?: boolean;
  sort?: ParallelSort;
  limit?: number;
  offset?: number;
  signal?: AbortSignal;
}): Promise<ParallelSearchResponse> {
  const q = new URLSearchParams();
  q.set("q", params.q);
  if (params.bucket) q.set("bucket", params.bucket);
  if (params.minLength != null) q.set("min_length", String(params.minLength));
  if (params.minOccurrences != null) q.set("min_occurrences", String(params.minOccurrences));
  if (params.maxPostings != null) q.set("max_postings", String(params.maxPostings));
  if (params.context != null) q.set("context", String(params.context));
  if (params.includeContained) q.set("include_contained", "true");
  if (params.sort) q.set("sort", params.sort);
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.offset != null) q.set("offset", String(params.offset));
  return fetchJson<ParallelSearchResponse>(`${apiBase}/search/parallel?${q.toString()}`, {
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

const coreRecordCache = new Map<string, Promise<CoreRecordResponse>>();

export async function getCoreRecord(
  collection: string,
  uuid: string,
): Promise<CoreRecordResponse> {
  const key = `${collection}/${uuid}`;
  const cached = coreRecordCache.get(key);
  if (cached != null) return cached;
  const promise = fetchJson<CoreRecordResponse>(
    `${apiBase}/core/${encodeURIComponent(collection)}/${encodeURIComponent(uuid)}`,
  ).catch((err) => {
    coreRecordCache.delete(key);
    throw err;
  });
  coreRecordCache.set(key, promise);
  return promise;
}

type CoreRecordSavedListener = (event: { collection: string; uuid: string }) => void;
const coreRecordSavedListeners = new Set<CoreRecordSavedListener>();

export function subscribeCoreRecordSaved(listener: CoreRecordSavedListener): () => void {
  coreRecordSavedListeners.add(listener);
  return () => {
    coreRecordSavedListeners.delete(listener);
  };
}

export async function patchCoreRecord(
  collection: string,
  uuid: string,
  body: CoreEditRequest,
): Promise<CoreEditResponse> {
  const response = await fetchJson<CoreEditResponse>(
    `${apiBase}/core/${encodeURIComponent(collection)}/${encodeURIComponent(uuid)}`,
    {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  coreRecordCache.delete(`${collection}/${uuid}`);
  for (const listener of coreRecordSavedListeners) {
    listener({ collection, uuid });
  }
  return response;
}

export async function lintSyntacticFunctions(): Promise<SyntacticFunctionLintResponse> {
  return fetchJson<SyntacticFunctionLintResponse>(
    `${apiBase}/core/lint/syntactic-functions`,
  );
}

export async function acceptSyntacticFunctionWarning(
  uuid: string,
  code: string,
): Promise<CoreEditResponse> {
  const record = await getCoreRecord("syntactic-functions", uuid);
  const data = { ...record.data };
  const existing = Array.isArray(data.lint_accept)
    ? (data.lint_accept as string[]).filter((c): c is string => typeof c === "string")
    : [];
  if (existing.includes(code)) {
    data.lint_accept = existing;
  } else {
    data.lint_accept = [...existing, code];
  }
  return patchCoreRecord("syntactic-functions", uuid, { data });
}

export async function deleteCoreRecord(
  collection: string,
  uuid: string,
  body: CoreDeleteRequest = {},
): Promise<CoreDeleteResponse> {
  return fetchJson<CoreDeleteResponse>(
    `${apiBase}/core/${encodeURIComponent(collection)}/${encodeURIComponent(uuid)}`,
    {
      method: "DELETE",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

export async function openCoreRecordPr(
  collection: string,
  uuid: string,
  body: CoreOpenPrRequest,
): Promise<CoreOpenPrResponse> {
  return fetchJson<CoreOpenPrResponse>(
    `${apiBase}/core/${encodeURIComponent(collection)}/${encodeURIComponent(uuid)}/pr`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    },
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

export async function getAnnotationsByRhetoricalDevice(
  rhetDevUuid: string,
): Promise<AnnotationsByRhetoricalDeviceResponse> {
  return fetchJson<AnnotationsByRhetoricalDeviceResponse>(
    `${apiBase}/annotations/by-rhetorical-device/${encodeURIComponent(rhetDevUuid)}`,
  );
}

export async function getAnnotationRhetoricalDeviceCounts(
  rhetDevUuids: string[],
): Promise<AnnotationsByRhetoricalDeviceCountsResponse> {
  return fetchJson<AnnotationsByRhetoricalDeviceCountsResponse>(
    `${apiBase}/annotations/by-rhetorical-devices/counts`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rhet_dev_uuids: rhetDevUuids }),
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
