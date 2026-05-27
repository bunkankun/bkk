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
  AuthSession,
  CatalogResponse,
  CategoriesResponse,
  Juan,
  Manifest,
  ManifestPart,
  SearchResponse,
  SearchSort,
  ServerInfo,
  WorkspaceFile,
  WorkspaceFileList,
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

export async function getCatalog(params?: {
  limit?: number;
  offset?: number;
  filters?: Record<string, string[]>;
}): Promise<CatalogResponse> {
  const q = new URLSearchParams();
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

export async function searchCorpus(params: {
  q: string;
  sort: SearchSort;
  textid?: string;
  witness?: string[];
  voice?: string[];
  category?: string[];
  categoryDescendants?: boolean;
  dateBefore?: number;
  dateAfter?: number;
  pivotTextid?: string;
  leftChar?: string[];
  rightChar?: string[];
  leftBigram?: string[];
  rightBigram?: string[];
  aroundBinom?: string[];
  context?: number;
  limit?: number;
  offset?: number;
  signal?: AbortSignal;
}): Promise<SearchResponse> {
  const q = new URLSearchParams();
  q.set("q", params.q);
  q.set("sort", params.sort);
  if (params.textid) q.set("textid", params.textid);
  if (params.witness) for (const w of params.witness) q.append("witness", w);
  if (params.voice) for (const v of params.voice) q.append("voice", v);
  if (params.category) for (const c of params.category) q.append("category", c);
  if (params.categoryDescendants != null) {
    q.set("category_descendants", String(params.categoryDescendants));
  }
  if (params.dateBefore != null) q.set("date_before", String(params.dateBefore));
  if (params.dateAfter != null) q.set("date_after", String(params.dateAfter));
  if (params.pivotTextid) q.set("pivot_textid", params.pivotTextid);
  if (params.leftChar) for (const v of params.leftChar) q.append("left_char", v);
  if (params.rightChar) for (const v of params.rightChar) q.append("right_char", v);
  if (params.leftBigram) for (const v of params.leftBigram) q.append("left_bigram", v);
  if (params.rightBigram) for (const v of params.rightBigram) q.append("right_bigram", v);
  if (params.aroundBinom) for (const v of params.aroundBinom) q.append("around_binom", v);
  if (params.context != null) q.set("context", String(params.context));
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.offset != null) q.set("offset", String(params.offset));
  return fetchJson<SearchResponse>(`${apiBase}/search?${q.toString()}`, {
    signal: params.signal,
  });
}
