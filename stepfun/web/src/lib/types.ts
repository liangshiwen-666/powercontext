export interface CollectionInfo {
  name: string;
  count: number;
  pendingRefresh: number;
  lastRefreshAt: number | null;
}

export interface Hit {
  id: string;
  text: string;
  meta: Record<string, unknown>;
  score: number | null;
  rank: number;
}

export type SearchMode = "hybrid" | "vector" | "fulltext";

export interface SearchOutcome {
  mode: SearchMode;
  hits: Hit[];
  searchMs: number;
  fusedInDb: boolean;
}

export interface BackendInfo {
  backend: "webgpu" | "wasm";
  dtype: string;
  loadMs: number;
  /** Human readable reason when we had to fall back to WASM. */
  reason?: string;
  adapter?: string;
}

export interface HealthInfo {
  ok: boolean;
  db: {
    mode: "embedded" | "server";
    /** "env" = 显式配置， "probe" = 启动时自动探测 */
    detectedBy?: "env" | "probe";
    database: string;
    path?: string;
    host?: string;
    dim: number;
  };
  model: { id: string; dim: number; dtype: Record<string, string> };
}

export interface DocPayload {
  id: string;
  text: string;
  meta: Record<string, unknown>;
  vector: number[];
}

export interface TimingEntry {
  ts: number;
  label: string;
  ms: number;
  detail?: string;
}
