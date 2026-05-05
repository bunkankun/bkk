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
  CatalogResponse,
  Juan,
  Manifest,
  ManifestPart,
  SearchResponse,
  SearchSort,
  ServerInfo,
} from "./types";

export const apiBase = import.meta.env.DEV ? "/api" : "";

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

export async function getManifest(textid: string): Promise<Manifest> {
  return fetchJson<Manifest>(`${apiBase}/bundles/${encodeURIComponent(textid)}/manifest`);
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
  context?: number;
  limit?: number;
  offset?: number;
}): Promise<SearchResponse> {
  const q = new URLSearchParams();
  q.set("q", params.q);
  q.set("sort", params.sort);
  if (params.textid) q.set("textid", params.textid);
  if (params.witness) for (const w of params.witness) q.append("witness", w);
  if (params.context != null) q.set("context", String(params.context));
  if (params.limit != null) q.set("limit", String(params.limit));
  if (params.offset != null) q.set("offset", String(params.offset));
  return fetchJson<SearchResponse>(`${apiBase}/search?${q.toString()}`);
}
