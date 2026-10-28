/**
 * Runtime configuration for the seekdb side of the app.
 *
 * The whole design assumes embeddings are computed in the browser; this process
 * never holds an embedding function and never calls a model.
 *
 * Database selection (resolved in KbDatabase.init):
 *   1. SEEKDB_MODE=server|embedded  — explicit, wins
 *   2. SEEKDB_HOST set              — server
 *   3. nothing set                  — probe 127.0.0.1:2881, else embedded
 */
export type DbMode = "embedded" | "server";
export type ModeSource = "env" | "probe";

const env = process.env;
const argvMode = process.argv.includes("--embedded")
  ? "embedded"
  : process.argv.includes("--server")
    ? "server"
    : undefined;
const envMode =
  env.SEEKDB_MODE === "embedded" || env.SEEKDB_MODE === "server" ? (env.SEEKDB_MODE as DbMode) : undefined;
const explicitMode = argvMode ?? envMode;

export const config = {
  port: Number(env.PORT ?? 8787),
  host: env.HOST ?? "127.0.0.1",

  db: {
    /** Explicit override; undefined means "detect at startup". */
    modeOverride: explicitMode,
    /** Resolved during KbDatabase.init(). */
    mode: (explicitMode ?? "server") as DbMode,
    /** How the mode was chosen; surfaced by /api/health. */
    detectedBy: "env" as ModeSource,

    /** Embedded mode: directory that holds the seekdb instance. */
    path: env.SEEKDB_PATH ?? "./stepfun-data/seekdb",
    database: env.SEEKDB_DATABASE ?? "stepfun",
    /** Server mode: address of a running `seekdb` (or OceanBase) instance. */
    host: env.SEEKDB_HOST ?? "127.0.0.1",
    port: Number(env.SEEKDB_PORT ?? 2881),
    user: env.SEEKDB_USER ?? "root",
    password: env.SEEKDB_PASSWORD ?? "",
    tenant: env.SEEKDB_TENANT ?? undefined,
    /** How long to wait for the TCP probe before falling back to embedded. */
    probeTimeoutMs: Number(env.SEEKDB_PROBE_TIMEOUT_MS ?? 600),
    /**
     * HNSW maintenance mode. "immediate" makes writes searchable without a
     * background merge, which is what embedded mode needs (its async refresh
     * routine is not implemented and blocks). Use "async" + refresh_index for
     * large bulk imports against a standalone server.
     */
    syncMode: (env.SEEKDB_SYNC_MODE === "async" ? "async" : "immediate") as "immediate" | "async",
  },

  /** Dimension of the embedding model used in the browser (bge-small-zh-v1.5 = 512). */
  dim: Number(env.EMBED_DIM ?? 512),

  /** Default (logical) collection created on first boot. */
  defaultCollection: env.DEFAULT_COLLECTION ?? "kb",

  /**
   * Physical seekdb collection. All logical collections share it and are told
   * apart by the `kb` metadata field (embedded seekdb deadlocks on a second
   * createCollection call inside one process).
   */
  physicalCollection: env.SEEKDB_COLLECTION ?? "kb_docs",

  /** Max documents accepted in a single batch write. */
  maxBatch: Number(env.MAX_BATCH ?? 256),
};

export type AppConfig = typeof config;
