import type { CollectionInfo, DocPayload, HealthInfo, SearchMode, SearchOutcome } from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { error: text };
  }
  if (!res.ok) {
    const message = (body as { error?: string })?.error ?? `HTTP ${res.status}`;
    throw new Error(message);
  }
  return body as T;
}

export const api = {
  health: () => request<HealthInfo>("/api/health"),

  listCollections: () => request<{ collections: CollectionInfo[] }>("/api/collections"),

  createCollection: (name: string) =>
    request<CollectionInfo>("/api/collections", { method: "POST", body: JSON.stringify({ name }) }),

  deleteCollection: (name: string) =>
    request<{ ok: true }>(`/api/collections/${encodeURIComponent(name)}`, { method: "DELETE" }),

  clearCollection: (name: string) =>
    request<CollectionInfo>(`/api/collections/${encodeURIComponent(name)}/clear`, { method: "POST" }),

  addDocs: (name: string, docs: DocPayload[], refresh = true) =>
    request<{ inserted: number; writeMs: number; refresh: { ms: number } | null; collection: CollectionInfo }>(
      `/api/collections/${encodeURIComponent(name)}/docs`,
      { method: "POST", body: JSON.stringify({ docs, refresh }) },
    ),

  refresh: (name: string) =>
    request<{ ms: number; refreshedAt: number; collection: CollectionInfo }>(
      `/api/collections/${encodeURIComponent(name)}/refresh`,
      { method: "POST" },
    ),

  search: (name: string, body: { vector?: number[]; text?: string; topK: number; mode: SearchMode }) =>
    request<SearchOutcome>(`/api/collections/${encodeURIComponent(name)}/search`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
