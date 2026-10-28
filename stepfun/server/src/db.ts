import { SeekdbClient, Schema, FulltextIndexConfig, VectorIndexConfig } from "seekdb";
import { appendFileSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { connect } from "node:net";
import { dirname, join } from "node:path";
import { config, type DbMode } from "./config.js";

type CollectionHandle = Awaited<ReturnType<SeekdbClient["getCollection"]>>;
export type SearchMode = "hybrid" | "vector" | "fulltext";

export interface DocInput {
  id: string;
  text: string;
  meta: Record<string, unknown>;
  vector: number[];
}

export interface Hit {
  id: string;
  text: string;
  meta: Record<string, unknown>;
  /** Vector distance in `vector` mode; null when seekdb reports none (RRF). */
  score: number | null;
  rank: number;
}

export interface CollectionInfo {
  name: string;
  count: number;
  pendingRefresh: number;
  lastRefreshAt: number | null;
}

export interface SearchOutcome {
  mode: SearchMode;
  hits: Hit[];
  searchMs: number;
  /** True when seekdb performed a single fused query (no client-side merging). */
  fusedInDb: boolean;
}

const WRITE_TIMEOUT_MS = Number(process.env.SEEKDB_WRITE_TIMEOUT_MS ?? 60_000);
const SEARCH_TIMEOUT_MS = Number(process.env.SEEKDB_SEARCH_TIMEOUT_MS ?? 30_000);

/** Turn a wedged native call into an actionable error instead of a frozen request. */
function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  return Promise.race([
    promise,
    new Promise<never>((_, reject) =>
      setTimeout(
        () =>
          reject(
            new Error(
              `${label} 超过 ${Math.round(ms / 1000)}s 未返回。嵌入式 seekdb 在部分平台上可能阻塞，` +
                `请改用 Docker：docker compose up -d 且 SEEKDB_HOST=127.0.0.1`,
            ),
          ),
        ms,
      ),
    ),
  ]);
}

const TRACE = process.env.DEBUG_DB === "1";

/** True when something is listening on host:port (used to detect a local seekdb). */
function probePort(host: string, port: number, timeoutMs: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = connect({ host, port });
    let settled = false;
    const finish = (open: boolean) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolve(open);
    };
    socket.setTimeout(timeoutMs);
    socket.once("connect", () => finish(true));
    socket.once("timeout", () => finish(false));
    socket.once("error", () => finish(false));
  });
}

/** Line-buffered trace: stdout is often piped when the server runs as a background job. */
function trace(message: string) {
  if (!TRACE) return;
  const line = `[${new Date().toISOString()}] ${message}\n`;
  try {
    appendFileSync("./db.trace.log", line);
  } catch {
    /* ignore */
  }
}

function buildSchema(dim: number, syncMode: "immediate" | "async"): Schema {
  // ik analyzer handles Chinese full-text search; HNSW holds the dense vectors.
  // embeddingFunction: null is deliberate — this process never embeds text.
  return new Schema({
    fulltextIndex: new FulltextIndexConfig("ik", { ik_mode: "smart" } as never),
    vectorIndex: new VectorIndexConfig({
      hnsw: {
        dimension: dim,
        distance: "cosine",
        M: 16,
        ef_construction: 200,
        ef_search: 64,
        syncMode,
      },
      embeddingFunction: null,
    } as never),
  });
}

interface RegistryEntry {
  name: string;
  count: number;
  createdAt: number;
}

/**
 * seekdb access layer.
 *
 * Design note: embedded seekdb (SDK 1.4.0) deadlocks when a *second* collection
 * is created inside one process. The app therefore keeps exactly one physical
 * collection and partitions it by a `kb` metadata field — logical collections are
 * switched with a metadata filter, which seekdb applies inside hybridSearch().
 */
export class KbDatabase {
  private client!: SeekdbClient;
  private handle_: CollectionHandle | null = null;
  private tableName: string | null = null;
  private ready = false;

  private pending = new Map<string, number>();
  private refreshedAt = new Map<string, number>();

  get mode() {
    return config.db.mode;
  }

  get info() {
    return {
      mode: config.db.mode,
      detectedBy: config.db.detectedBy,
      database: config.db.database,
      path: config.db.mode === "embedded" ? config.db.path : undefined,
      host: config.db.mode === "server" ? `${config.db.host}:${config.db.port}` : undefined,
      dim: config.dim,
      physicalCollection: config.physicalCollection,
    };
  }

  /**
   * Pick the backend: explicit env wins, otherwise a local seekdb on 2881 is used
   * when reachable, and embedded seekdb is the zero-dependency fallback.
   */
  private async resolveMode(): Promise<void> {
    if (config.db.modeOverride) {
      config.db.mode = config.db.modeOverride;
      config.db.detectedBy = "env";
      trace(`mode:${config.db.mode}:env`);
      return;
    }
    if (process.env.SEEKDB_HOST) {
      config.db.mode = "server";
      config.db.detectedBy = "env";
      trace("mode:server:env(host)");
      return;
    }
    const open = await probePort(config.db.host, config.db.port, config.db.probeTimeoutMs);
    config.db.mode = open ? "server" : "embedded";
    config.db.detectedBy = "probe";
    trace(`mode:${config.db.mode}:probe(${config.db.host}:${config.db.port} ${open ? "open" : "closed"})`);
  }

  // --- registry -----------------------------------------------------------
  private registryPath(): string {
    // Keep this *outside* the seekdb data directory: writing into the embedded
    // instance's own directory has been observed to wedge the native layer.
    const base = process.env.STEPFUN_DATA_DIR ?? "./stepfun-data";
    return join(base, "collections.json");
  }

  private readRegistry(): RegistryEntry[] {
    try {
      const parsed = JSON.parse(readFileSync(this.registryPath(), "utf8")) as { collections?: RegistryEntry[] };
      return Array.isArray(parsed.collections) ? parsed.collections : [];
    } catch {
      return [];
    }
  }

  private writeRegistry(entries: RegistryEntry[]): void {
    try {
      mkdirSync(dirname(this.registryPath()), { recursive: true });
      writeFileSync(this.registryPath(), JSON.stringify({ collections: entries }, null, 2));
    } catch (err) {
      console.warn("[db] could not persist collection registry:", (err as Error)?.message);
    }
  }

  private record(name: string, count: number): void {
    const entries = this.readRegistry();
    const existing = entries.find((entry) => entry.name === name);
    if (existing) {
      existing.count = count;
    } else {
      entries.push({ name, count, createdAt: Date.now() });
    }
    this.writeRegistry(entries);
  }

  private forget(name: string): void {
    this.writeRegistry(this.readRegistry().filter((entry) => entry.name !== name));
  }

  // --- connection ---------------------------------------------------------
  async init(): Promise<void> {
    if (this.ready) return;
    await this.resolveMode();
    trace(`init:start mode=${config.db.mode} detectedBy=${config.db.detectedBy}`);

    try {
      await this.connect();
    } catch (err) {
      // A port can be open without being seekdb. When the choice was only a
      // guess (probe) and we still have no DB, fall back to embedded once.
      const canFallback =
        config.db.mode === "server" && !config.db.modeOverride && !process.env.SEEKDB_HOST && config.db.detectedBy === "probe";
      if (!canFallback) throw err;
      console.warn(
        `[db] 127.0.0.1:${config.db.port} 可连接但不是可用的 seekdb（${(err as Error)?.message}），改用嵌入式 seekdb`,
      );
      config.db.mode = "embedded";
      config.db.detectedBy = "probe";
      this.client = undefined as never;
      this.handle_ = null;
      await this.connect();
    }

    if (this.readRegistry().length === 0) this.record(config.defaultCollection, 0);
    this.ready = true;
    trace("init:done");
  }

  private async connect(): Promise<void> {
    if (config.db.mode === "embedded") {
      this.ensureDataDirUnlocked();
      const t0 = performance.now();
      await this.bootstrapEmbedded();
      trace(`init:database-ready ${(performance.now() - t0).toFixed(0)}ms`);
      this.client = new SeekdbClient({
        path: config.db.path,
        database: config.db.database,
      } as never);
    } else {
      const t0 = performance.now();
      await this.bootstrapServer();
      trace(`init:server-database-ready ${(performance.now() - t0).toFixed(0)}ms`);
      this.client = new SeekdbClient({
        host: config.db.host,
        port: config.db.port,
        user: config.db.user,
        password: config.db.password,
        database: config.db.database,
        ...(config.db.tenant ? { tenant: config.db.tenant } : {}),
      } as never);
    }

    const t1 = performance.now();
    await this.client.execute("SELECT 1");
    trace(`init:connected ${(performance.now() - t1).toFixed(0)}ms`);

    await this.ensurePhysicalCollection();
    trace("init:physical-collection-ready");
  }

  /**
   * An embedded instance is single-writer: a crashed run can leave a live child
   * process holding `run/seekdb.pid`, and a new client then blocks inside the
   * native layer forever. Fail fast with actionable advice instead.
   */
  private ensureDataDirUnlocked(): void {
    const pidFile = join(config.db.path, "run", "seekdb.pid");
    let pid = 0;
    try {
      pid = Number(readFileSync(pidFile, "utf8").trim());
    } catch {
      return; // no pid file — normal first boot
    }
    if (!Number.isFinite(pid) || pid <= 0 || pid === process.pid) return;

    let alive = true;
    try {
      process.kill(pid, 0);
    } catch {
      alive = false;
    }
    if (!alive) {
      try {
        unlinkSync(pidFile);
        trace(`cleared stale pid file (${pid})`);
      } catch {
        /* ignore */
      }
      return;
    }

    if (process.env.SEEKDB_FORCE_UNLOCK === "1") {
      try {
        process.kill(pid, "SIGKILL");
        unlinkSync(pidFile);
        trace(`force-unlocked pid ${pid}`);
        return;
      } catch (err) {
        trace(`force-unlock failed: ${(err as Error)?.message}`);
      }
    }

    throw new Error(
      `数据目录 ${config.db.path} 正被进程 ${pid} 占用（通常是上次异常退出的嵌入式 seekdb）。` +
        `请结束该进程后重试，或设置 SEEKDB_FORCE_UNLOCK=1 让本服务强制接管；` +
        `也可以改用 Docker：docker compose up -d`,
    );
  }

  private async bootstrapEmbedded(): Promise<void> {
    let bootstrap: SeekdbClient | undefined;
    try {
      bootstrap = new SeekdbClient({ path: config.db.path } as never);
      await this.ensureDatabase(bootstrap);
    } catch (err) {
      console.warn(`[db] embedded bootstrap failed (continuing):`, (err as Error)?.message);
    } finally {
      try {
        await bootstrap?.close();
      } catch {
        /* ignore */
      }
    }
  }

  private async bootstrapServer(): Promise<void> {
    let bootstrap: SeekdbClient | undefined;
    try {
      bootstrap = new SeekdbClient({
        host: config.db.host,
        port: config.db.port,
        user: config.db.user,
        password: config.db.password,
        ...(config.db.tenant ? { tenant: config.db.tenant } : {}),
      } as never);
      await this.ensureDatabase(bootstrap);
    } catch (err) {
      console.warn(`[db] server bootstrap failed (continuing):`, (err as Error)?.message);
    } finally {
      try {
        await bootstrap?.close();
      } catch {
        /* ignore */
      }
    }
  }

  private async ensureDatabase(client: SeekdbClient): Promise<void> {
    const databases = await client.listDatabases();
    const names = (databases ?? []).map((db: { name?: string }) => db?.name ?? "");
    if (!names.includes(config.db.database)) {
      await client.createDatabase(config.db.database);
      trace(`created database ${config.db.database}`);
    }
  }

  private async ensurePhysicalCollection(): Promise<void> {
    const name = config.physicalCollection;
    // getOrCreateCollection() is the only creation path that behaves in embedded
    // mode: createCollection() + a later getCollection() deadlocks the SDK.
    this.handle_ = await this.client.getOrCreateCollection({
      name,
      schema: buildSchema(config.dim, config.db.syncMode),
      embeddingFunction: null,
    } as never);
    trace(`ensurePhysical:ready ${name}`);
  }

  private async handle(): Promise<CollectionHandle> {
    if (this.handle_) return this.handle_;
    // embeddingFunction: null keeps seekdb from looking up a registered default
    // embedding function — this process never embeds text.
    this.handle_ = await this.client.getCollection({
      name: config.physicalCollection,
      embeddingFunction: null,
    } as never);
    return this.handle_;
  }

  // --- logical collections ------------------------------------------------
  async getOrCreate(name: string): Promise<CollectionInfo> {
    const entries = this.readRegistry();
    if (!entries.some((entry) => entry.name === name)) this.record(name, 0);
    return this.describe(name);
  }

  async list(): Promise<CollectionInfo[]> {
    const entries = this.readRegistry();
    if (entries.length === 0) return [await this.getOrCreate(config.defaultCollection)];
    return entries.map((entry) => this.infoFor(entry.name, entry.count));
  }

  private infoFor(name: string, count: number): CollectionInfo {
    return {
      name,
      count,
      pendingRefresh: this.pending.get(name) ?? 0,
      lastRefreshAt: this.refreshedAt.get(name) ?? null,
    };
  }

  /**
   * Physical table backing the collection (`c$v2$<id>`). Resolved once from
   * seekdb's own metadata table so we can count a logical partition exactly.
   */
  private async resolveTable(): Promise<string | null> {
    if (this.tableName) return this.tableName;
    try {
      const rows = await this.client.execute("SELECT collection_id, collection_name FROM sdk_collections");
      const row = (rows ?? []).find(
        (item) => String((item as Record<string, unknown>).collection_name ?? "") === config.physicalCollection,
      );
      const id = row ? String((row as Record<string, unknown>).collection_id ?? "") : "";
      if (!id) return null;
      this.tableName = `c$v2$${id}`;
      return this.tableName;
    } catch (err) {
      trace(`resolveTable:failed ${(err as Error)?.message}`);
      return null;
    }
  }

  /** Exact row count for one logical collection (falls back to the registry). */
  private async countRows(name: string): Promise<number | null> {
    const table = await this.resolveTable();
    if (!table) return null;
    try {
      const rows = await this.client.execute(
        `SELECT COUNT(*) AS c FROM \`${table}\` WHERE JSON_EXTRACT(metadata, '$.kb') = '${name}'`,
      );
      const value = Number((rows?.[0] as Record<string, unknown>)?.c ?? NaN);
      return Number.isFinite(value) ? value : null;
    } catch (err) {
      trace(`countRows:${name}:failed ${(err as Error)?.message}`);
      return null;
    }
  }

  async describe(name: string): Promise<CollectionInfo> {
    const entry = this.readRegistry().find((item) => item.name === name);
    if (!entry) throw new Error(`collection not found: ${name}`);
    const exact = await this.countRows(name);
    if (exact !== null && exact !== entry.count) this.record(name, exact);
    return this.infoFor(name, exact ?? entry.count);
  }

  /** Drop a logical collection: delete its rows inside the physical collection. */
  async drop(name: string): Promise<void> {
    const coll = await this.handle();
    const t0 = performance.now();
    await withTimeout(
      coll.delete({ where: { kb: { $eq: name } } } as never),
      WRITE_TIMEOUT_MS,
      `删除集合 ${name}`,
    );
    trace(`drop:${name}:rows-deleted ${(performance.now() - t0).toFixed(0)}ms`);
    this.pending.delete(name);
    this.refreshedAt.delete(name);
    this.forget(name);
    // Deleted rows leave tombstones in the vector index; compact in server mode.
    await this.refresh(name).catch(() => undefined);
  }

  async clear(name: string): Promise<void> {
    const coll = await this.handle();
    await withTimeout(
      coll.delete({ where: { kb: { $eq: name } } } as never),
      WRITE_TIMEOUT_MS,
      `清空集合 ${name}`,
    );
    this.pending.set(name, 0);
    this.record(name, 0);
    await this.refresh(name).catch(() => undefined);
  }

  // --- writes -------------------------------------------------------------
  async addBatch(name: string, docs: DocInput[]): Promise<{ inserted: number; writeMs: number }> {
    if (docs.length === 0) return { inserted: 0, writeMs: 0 };
    for (const doc of docs) {
      if (!Array.isArray(doc.vector) || doc.vector.length !== config.dim) {
        throw new Error(
          `document ${doc.id}: expected embedding of dimension ${config.dim}, got ${doc.vector?.length ?? 0}`,
        );
      }
    }
    const coll = await this.handle();
    const t0 = performance.now();
    // upsert keeps re-importing the same file idempotent (chunk ids are stable).
    await withTimeout(
      coll.upsert({
        ids: docs.map((doc) => doc.id),
        documents: docs.map((doc) => doc.text),
        metadatas: docs.map((doc) => ({ ...doc.meta, kb: name })),
        embeddings: docs.map((doc) => doc.vector),
      } as never),
      WRITE_TIMEOUT_MS,
      `写入 ${docs.length} 条到 seekdb`,
    );
    const writeMs = performance.now() - t0;

    const exact = await this.countRows(name);
    const entry = this.readRegistry().find((item) => item.name === name);
    this.record(name, exact ?? (entry?.count ?? 0) + docs.length);
    this.pending.set(name, (this.pending.get(name) ?? 0) + docs.length);
    trace(`add:${name}:n=${docs.length} ${writeMs.toFixed(0)}ms`);
    return { inserted: docs.length, writeMs };
  }

  async deleteDocs(name: string, ids: string[]): Promise<void> {
    const coll = await this.handle();
    await coll.delete({ ids } as never);
    const entry = this.readRegistry().find((item) => item.name === name);
    this.record(name, Math.max(0, (entry?.count ?? 0) - ids.length));
    this.pending.set(name, (this.pending.get(name) ?? 0) + ids.length);
  }

  async refresh(name: string): Promise<{ ms: number; refreshedAt: number }> {
    const coll = await this.handle();
    const t0 = performance.now();
    if (config.db.syncMode === "immediate") {
      // Writes are already indexed synchronously; the async merge routine is not
      // available in embedded mode, so calling it would just block.
      const ms = performance.now() - t0;
      this.pending.set(name, 0);
      this.refreshedAt.set(name, Date.now());
      return { ms, refreshedAt: this.refreshedAt.get(name) as number };
    }
    // A stale/unsupported refresh path can block forever, so bound it.
    await Promise.race([
      coll.refresh_index(),
      new Promise((_, reject) =>
        setTimeout(() => reject(new Error("refresh_index timed out after 15s")), 15_000),
      ),
    ]).catch((err) => {
      console.warn(`[db] refresh_index(${name}) skipped:`, (err as Error)?.message);
    });
    const ms = performance.now() - t0;
    const refreshedAt = Date.now();
    this.pending.set(name, 0);
    this.refreshedAt.set(name, refreshedAt);
    return { ms, refreshedAt };
  }

  // --- reads --------------------------------------------------------------
  async search(
    name: string,
    opts: { vector?: number[]; text?: string; topK: number; mode: SearchMode },
  ): Promise<SearchOutcome> {
    const coll = await this.handle();
    const include = ["documents", "metadatas", "distances"] as const;
    const { vector, text, topK } = opts;
    const mode: SearchMode =
      opts.mode === "fulltext" && !text ? "vector" : opts.mode === "vector" && !vector ? "fulltext" : opts.mode;

    if (mode !== "vector" && vector && vector.length !== config.dim) {
      throw new Error(`query embedding must have dimension ${config.dim}`);
    }

    const partition = { kb: { $eq: name } };
    const t0 = performance.now();
    let result: {
      ids: readonly (readonly string[])[];
      documents?: readonly (readonly (string | null)[])[];
      metadatas?: readonly (readonly unknown[])[];
      distances?: readonly (readonly (number | null)[])[];
    };
    let fusedInDb = false;

    const run = async () => {
      if (mode === "vector") {
        return coll.query({
          queryEmbeddings: [vector as number[]],
          where: partition,
          nResults: topK,
          include: [...include],
        } as never);
      }
      if (mode === "fulltext" || !vector) {
        return coll.hybridSearch({
          query: { whereDocument: { $contains: text as string }, where: partition, nResults: topK },
          nResults: topK,
          include: [...include],
        } as never);
      }
      // One fused query inside seekdb: full-text + vector + RRF ranking.
      fusedInDb = true;
      return coll.hybridSearch({
        query: { whereDocument: { $contains: text as string }, where: partition, nResults: topK * 2 },
        knn: { queryEmbeddings: [vector], where: partition, nResults: topK * 2 },
        rank: { rrf: {} },
        nResults: topK,
        include: [...include],
      } as never);
    };

    const queried = await withTimeout(run(), SEARCH_TIMEOUT_MS, "seekdb 检索");
    result = queried;
    const searchMs = performance.now() - t0;

    const ids = result?.ids?.[0] ?? [];
    const docs = result?.documents?.[0] ?? [];
    const metas = (result?.metadatas?.[0] ?? []) as (Record<string, unknown> | null)[];
    const distances = result?.distances?.[0] ?? [];

    const hits: Hit[] = ids.map((id, i) => ({
      id,
      text: docs[i] ?? "",
      meta: (metas[i] ?? {}) as Record<string, unknown>,
      score: distances[i] ?? null,
      rank: i + 1,
    }));

    return { mode, hits, searchMs, fusedInDb };
  }

  async close(): Promise<void> {
    try {
      await this.client?.close();
    } catch {
      /* ignore */
    }
  }
}
