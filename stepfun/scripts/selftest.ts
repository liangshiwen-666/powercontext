/**
 * End-to-end self test for the seekdb data layer (no HTTP, no browser).
 *
 *   npm run selftest                 # real model + seekdb (server mode / Docker)
 *   npx tsx scripts/selftest.ts --no-model
 *
 * `--no-model` replaces the embedding model with deterministic pseudo vectors.
 * Use it when seekdb runs embedded: loading ONNX in the same process as the
 * embedded engine is what makes the native layer block (the app itself never
 * loads a model server-side, so this only affects this harness).
 */
import { env, pipeline } from "@huggingface/transformers";
import { mkdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { KbDatabase } from "../server/src/db.ts";
import { prepareDocument } from "../web/src/lib/chunk.ts";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章：";
const NO_MODEL = process.argv.includes("--no-model");
const DIM = Number(process.env.EMBED_DIM ?? 512);

/** Deterministic stand-in vector: same input length/characters → same vector. */
function pseudoVector(text: string): number[] {
  let seed = 2166136261 >>> 0;
  for (let i = 0; i < text.length; i++) {
    seed ^= text.charCodeAt(i);
    seed = Math.imul(seed, 16777619) >>> 0;
  }
  const vector = new Array<number>(DIM);
  let sum = 0;
  for (let i = 0; i < DIM; i++) {
    seed = (Math.imul(seed, 1103515245) + 12345) >>> 0;
    const value = (seed / 0xffffffff) * 2 - 1;
    vector[i] = value;
    sum += value * value;
  }
  const norm = Math.sqrt(sum) || 1;
  return vector.map((value) => value / norm);
}

function parseCorpus(markdown: string) {
  const hits = [...markdown.matchAll(/^##\s+(.+?)\s*$/gm)];
  return hits
    .map((hit, i) => {
      const start = (hit.index ?? 0) + hit[0].length;
      const end = i + 1 < hits.length ? hits[i + 1].index : markdown.length;
      return { title: hit[1].trim(), body: markdown.slice(start, end).trim() };
    })
    .filter((doc) => doc.body.length > 0);
}

function check(condition: unknown, message: string) {
  if (!condition) throw new Error(`ASSERT FAILED: ${message}`);
  console.log(`  ok  ${message}`);
}

async function main() {
  env.allowLocalModels = false;
  env.allowRemoteModels = true;
  env.cacheDir = resolve(root, ".models-cache");
  env.remoteHost = process.env.MODEL_HOST ?? "https://hf-mirror.com";

  let extractor: ((texts: string[], opts: unknown) => Promise<{ tolist(): number[][] }>) | null = null;
  const embedTexts = async (texts: string[]): Promise<number[][]> => {
    if (!extractor) return texts.map(pseudoVector);
    const out = await extractor(texts, { pooling: "cls", normalize: true });
    return out.tolist();
  };

  if (NO_MODEL) {
    console.log("[selftest] --no-model: using deterministic pseudo vectors");
  } else {
    console.log("[selftest] loading model…");
    extractor = (await pipeline("feature-extraction", "Xenova/bge-small-zh-v1.5", {
      dtype: "q8" as never,
      device: "cpu",
    })) as unknown as typeof extractor;
  }

  const corpus = parseCorpus(readFileSync(resolve(root, "data/corpus.md"), "utf8"));
  const docs = corpus.map((doc) => prepareDocument(doc.title, doc.body));
  const chunks = docs.flatMap((doc) =>
    doc.chunks.map((chunk) => ({ id: chunk.id, docId: doc.docId, title: doc.title, text: chunk.text })),
  );
  console.log(`[selftest] ${corpus.length} documents, ${chunks.length} chunks`);

  const embedStart = performance.now();
  const vectors: number[][] = [];
  for (let i = 0; i < chunks.length; i += 16) {
    const slice = chunks.slice(i, i + 16).map((c) => `${c.title}\n${c.text}`);
    vectors.push(...(await embedTexts(slice)));
  }
  console.log(`[selftest] embedded in ${(performance.now() - embedStart).toFixed(0)} ms`);

  const db = new KbDatabase();
  const t0 = performance.now();
  await db.init();
  console.log(`[selftest] db init in ${(performance.now() - t0).toFixed(0)} ms (${db.info.mode})`);
  if (db.info.mode === "embedded") {
    console.warn(
      "[selftest] 当前是嵌入式 seekdb：本脚本与原生引擎同进程，检索阶段可能阻塞（应用本身不受影响）。" +
        "权威验证请用 `npm run browser:check`，或起 Docker 后再跑本脚本。",
    );
  }

  const name = `selftest_${Date.now().toString(36)}`;
  await db.getOrCreate(name);

  const writeStart = performance.now();
  for (let i = 0; i < chunks.length; i += 64) {
    const slice = chunks.slice(i, i + 64);
    await db.addBatch(
      name,
      slice.map((chunk, k) => ({
        id: chunk.id,
        text: `${chunk.title}\n${chunk.text}`,
        meta: { title: chunk.title, docId: chunk.docId },
        vector: vectors[i + k],
      })),
    );
  }
  const writeMs = performance.now() - writeStart;

  const refreshStart = performance.now();
  await db.refresh(name);
  const refreshMs = performance.now() - refreshStart;
  console.log(`[selftest] write ${writeMs.toFixed(0)} ms, refresh ${refreshMs.toFixed(0)} ms`);

  const info = await db.describe(name);
  check(info.count === chunks.length, `count == ${chunks.length} (got ${info.count})`);

  const existing = await db.list();
  check(
    existing.some((c) => c.name === name),
    "collection appears in list()",
  );

  const queries: { query: string; expect: string }[] = [
    { query: "刚导入的文档搜不到是什么原因", expect: "索引刷新与最终一致性" },
    { query: "浏览器里怎么运行 embedding 模型", expect: "端侧推理与 Transformers.js" },
    { query: "HNSW 的 ef_search 参数是干什么的", expect: "HNSW 向量索引" },
  ];

  for (const item of queries) {
    const [vector] = await embedTexts([`${QUERY_PREFIX}${item.query}`]);

    const t = performance.now();
    const result = await db.search(name, { vector, text: item.query, topK: 10, mode: "hybrid" });
    const ms = performance.now() - t;
    const titles = result.hits.map((hit) => String(hit.meta.title ?? ""));
    console.log(
      `[selftest] "${item.query}" -> ${ms.toFixed(1)} ms, top3: ${titles.slice(0, 3).join(" | ")}`,
    );
    check(result.fusedInDb, "hybrid search used a single fused seekdb query");
    // Pseudo vectors carry no semantics, so only assert containment with the real model.
    if (!NO_MODEL) check(titles.includes(item.expect), `top-10 contains "${item.expect}"`);
  }

  // vector-only and fulltext-only paths
  const [probe] = await embedTexts([`${QUERY_PREFIX}把知识库向量和全文一起查`]);
  const vectorOnly = await db.search(name, { vector: probe, topK: 5, mode: "vector" });
  check(vectorOnly.hits.length > 0, "vector-only search returns hits");

  const fulltextOnly = await db.search(name, { text: "索引", topK: 5, mode: "fulltext" });
  check(fulltextOnly.hits.length > 0, "fulltext-only search returns hits");

  // incremental write + refresh visibility
  const extra = prepareDocument("增量导入测试文档", "这是一篇用于验证增量写入与索引刷新的测试文档，内容包含 seekdb 与向量检索。");
  const extraVector = await embedTexts([`${extra.title}\n${extra.chunks[0].text}`]);
  await db.addBatch(name, [
    {
      id: extra.chunks[0].id,
      text: `${extra.title}\n${extra.chunks[0].text}`,
      meta: { title: extra.title, docId: extra.docId },
      vector: extraVector[0],
    },
  ]);
  await db.refresh(name);
  const after = await db.describe(name);
  check(after.count === chunks.length + 1, "incremental write visible after refresh");
  const incremental = await db.search(name, { text: "增量导入测试", topK: 3, mode: "fulltext" });
  check(
    incremental.hits.some((hit) => String(hit.meta.title ?? "").includes("增量导入测试")),
    "newly written chunk is searchable after refresh_index()",
  );

  await db.drop(name);
  const afterDrop = await db.list();
  check(!afterDrop.some((c) => c.name === name), "collection removed from list() after drop");

  await db.close();
  console.log("[selftest] PASSED");
  process.exit(0);
}

main().catch(async (err) => {
  console.error("[selftest] FAILED:", err?.stack ?? err);
  process.exit(1);
});
