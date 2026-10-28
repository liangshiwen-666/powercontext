import express, { type NextFunction, type Request, type Response } from "express";
import { appendFileSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { config } from "./config.js";
import { KbDatabase, type DocInput, type SearchMode } from "./db.js";

const here = dirname(fileURLToPath(import.meta.url));
const webDist = resolve(here, "../../web/dist");

function traceMain(message: string) {
  if (process.env.DEBUG_DB !== "1") return;
  try {
    appendFileSync("./db.trace.log", `[${new Date().toISOString()}] main:${message}\n`);
  } catch {
    /* ignore */
  }
}

const app = express();
const db = new KbDatabase();

app.use(express.json({ limit: "64mb" }));

const asyncRoute =
  (fn: (req: Request, res: Response) => Promise<unknown>) =>
  (req: Request, res: Response, next: NextFunction) => {
    fn(req, res).catch(next);
  };

const NAME_RE = /^[A-Za-z0-9_]{1,64}$/;

const collectionOf = (req: Request) => {
  const raw = req.params.name;
  const name = Array.isArray(raw) ? raw[0] : raw;
  if (!name || !NAME_RE.test(name)) {
    throw Object.assign(new Error("invalid collection name (letters, digits, underscore only)"), { status: 400 });
  }
  return name;
};

app.get(
  "/api/health",
  asyncRoute(async (_req, res) => {
    await db.init();
    res.json({
      ok: true,
      db: db.info,
      embedding: "browser-local",
      model: {
        id: process.env.EMBED_MODEL_ID ?? "Xenova/bge-small-zh-v1.5",
        dim: config.dim,
        dtype: { webgpu: "fp16", wasm: "q8" },
      },
      serverTime: Date.now(),
    });
  }),
);

app.get(
  "/api/collections",
  asyncRoute(async (_req, res) => {
    await db.init();
    res.json({ collections: await db.list() });
  }),
);

app.post(
  "/api/collections",
  asyncRoute(async (req, res) => {
    await db.init();
    const name = String(req.body?.name ?? "");
    if (!NAME_RE.test(name)) throw Object.assign(new Error("invalid name"), { status: 400 });
    res.json(await db.getOrCreate(name));
  }),
);

app.delete(
  "/api/collections/:name",
  asyncRoute(async (req, res) => {
    await db.init();
    await db.drop(collectionOf(req));
    res.json({ ok: true });
  }),
);

app.post(
  "/api/collections/:name/clear",
  asyncRoute(async (req, res) => {
    await db.init();
    const name = collectionOf(req);
    await db.clear(name);
    res.json(await db.describe(name));
  }),
);

app.post(
  "/api/collections/:name/docs",
  asyncRoute(async (req, res) => {
    await db.init();
    const name = collectionOf(req);
    const docs = (req.body?.docs ?? []) as DocInput[];
    if (!Array.isArray(docs) || docs.length === 0) {
      throw Object.assign(new Error("docs[] required"), { status: 400 });
    }
    if (docs.length > config.maxBatch) {
      throw Object.assign(new Error(`too many docs in one batch (max ${config.maxBatch})`), { status: 413 });
    }
    const write = await db.addBatch(name, docs);
    let refresh: { ms: number; refreshedAt: number } | null = null;
    if (req.body?.refresh !== false) {
      refresh = await db.refresh(name);
    }
    res.json({ ...write, refresh, collection: await db.describe(name) });
  }),
);

app.post(
  "/api/collections/:name/refresh",
  asyncRoute(async (req, res) => {
    await db.init();
    const name = collectionOf(req);
    const refresh = await db.refresh(name);
    res.json({ ...refresh, collection: await db.describe(name) });
  }),
);

app.post(
  "/api/collections/:name/search",
  asyncRoute(async (req, res) => {
    await db.init();
    const name = collectionOf(req);
    const mode = (req.body?.mode ?? "hybrid") as SearchMode;
    const topK = Math.min(Math.max(Number(req.body?.topK ?? 10), 1), 50);
    const vector = Array.isArray(req.body?.vector) ? (req.body.vector as number[]) : undefined;
    const text = typeof req.body?.text === "string" ? req.body.text : undefined;
    if (mode !== "fulltext" && !vector) {
      throw Object.assign(new Error("vector[] required for this mode"), { status: 400 });
    }
    if (mode !== "vector" && !text) {
      throw Object.assign(new Error("text required for this mode"), { status: 400 });
    }
    const outcome = await db.search(name, { vector, text, topK, mode });
    res.json(outcome);
  }),
);

app.get(
  "/api/collections/:name/docs",
  asyncRoute(async (req, res) => {
    await db.init();
    const name = collectionOf(req);
    const limit = Math.min(Math.max(Number(req.query.limit ?? 20), 1), 200);
    const coll = await db.search(name, { text: "", topK: limit, mode: "vector" });
    res.json({ docs: coll.hits });
  }),
);

// --- static frontend (production build) ---
if (existsSync(webDist)) {
  app.use(express.static(webDist));
  app.get(/^\/(?!api\/).*/, (_req, res) => res.sendFile(join(webDist, "index.html")));
}

app.use((err: Error & { status?: number }, _req: Request, res: Response, _next: NextFunction) => {
  const status = err.status ?? 500;
  if (status >= 500) console.error("[api]", err);
  res.status(status).json({ error: err.message });
});

async function main() {
  const timeout = new Promise<never>((_, reject) =>
    setTimeout(() => reject(new Error("seekdb 初始化超时（45s）")), 45_000),
  );
  try {
    await Promise.race([db.init(), timeout]);
  } catch (err) {
    console.error("[server] seekdb 初始化失败：", (err as Error)?.message);
    console.error("[server] 两种可选方式：");
    console.error("[server]   A. 有 Docker：`docker compose up -d` 后重启（默认连 127.0.0.1:2881）");
    console.error("[server]   B. 无 Docker：`SEEKDB_MODE=embedded npm run dev`（首次启动约 5–20 秒）");
    process.exit(1);
  }
  traceMain("db ready");
  app.listen(config.port, config.host, () => {
    traceMain(`listening on ${config.host}:${config.port}`);
    const info = db.info;
    const where = info.mode === "embedded" ? `path=${info.path}` : `host=${info.host}`;
    const how = info.detectedBy === "env" ? "显式指定" : "自动探测";
    console.log(
      `[server] http://${config.host}:${config.port}  db=${info.mode}(${how}) ${where} ` +
        `database=${info.database} dim=${info.dim}`,
    );
  });
}

for (const sig of ["SIGINT", "SIGTERM"] as const) {
  process.on(sig, () => {
    db.close().finally(() => process.exit(0));
  });
}

main().catch((err) => {
  console.error("[server] failed to start:", err);
  process.exit(1);
});
