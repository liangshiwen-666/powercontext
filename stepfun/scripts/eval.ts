/**
 * Retrieval quality + throughput evaluation harness.
 *
 *   npx tsx scripts/eval.ts --dtype q8  --label int8
 *   npx tsx scripts/eval.ts --dtype fp16 --label fp16
 *   npx tsx scripts/eval.ts --dtype fp32 --label fp32 --all
 *
 * By default it writes vectors into seekdb through the running server
 * (`npm run dev`) and measures retrieval exactly like the app does.
 * Use `--inproc` to skip the database and score with in-memory cosine
 * similarity (useful when no seekdb instance is available).
 */
import { env, pipeline } from "@huggingface/transformers";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { prepareDocument } from "../web/src/lib/chunk.ts";
import { mean, ndcgAtK, recallAtK, reciprocalRank } from "../web/src/lib/metrics.ts";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

type Args = Record<string, string | boolean>;
const args: Args = {};
for (let i = 2; i < process.argv.length; i++) {
  const token = process.argv[i];
  if (!token.startsWith("--")) continue;
  const key = token.slice(2);
  const next = process.argv[i + 1];
  if (next && !next.startsWith("--")) {
    args[key] = next;
    i++;
  } else {
    args[key] = true;
  }
}

const modelId = (args.model as string) ?? process.env.EMBED_MODEL_ID ?? "Xenova/bge-small-zh-v1.5";
const dtype = ((args.dtype as string) ?? "q8") as "q8" | "fp16" | "fp32";
const label = (args.label as string) ?? dtype;
const apiBase = ((args.api as string) ?? "http://127.0.0.1:8787").replace(/\/$/, "");
const inproc = Boolean(args.inproc);
const topK = Number((args.topk as string) ?? 10);
const batchSize = Number((args.batch as string) ?? 16);
const collection = (args.collection as string) ?? `eval_${label.replace(/[^A-Za-z0-9]/g, "_")}_${Date.now().toString(36)}`;
const QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章：";

interface SourceDoc {
  title: string;
  body: string;
}

function parseCorpus(markdown: string): SourceDoc[] {
  const hits = [...markdown.matchAll(/^##\s+(.+?)\s*$/gm)];
  return hits
    .map((hit, i) => {
      const start = (hit.index ?? 0) + hit[0].length;
      const end = i + 1 < hits.length ? hits[i + 1].index : markdown.length;
      return { title: hit[1].trim(), body: markdown.slice(start, end).trim() };
    })
    .filter((doc) => doc.body.length > 0);
}

function cosine(a: number[], b: number[]): number {
  let dot = 0;
  for (let i = 0; i < a.length; i++) dot += a[i] * b[i];
  return dot; // vectors are normalised
}

async function main() {
  env.allowLocalModels = false;
  env.allowRemoteModels = true;
  env.cacheDir = resolve(root, ".models-cache");
  env.remoteHost = process.env.MODEL_HOST ?? "https://hf-mirror.com";

  const corpus = parseCorpus(readFileSync(resolve(root, "data/corpus.md"), "utf8"));
  const prepared = corpus.map((doc) => prepareDocument(doc.title, doc.body));
  const chunks = prepared.flatMap((doc) =>
    doc.chunks.map((chunk) => ({ id: chunk.id, docId: doc.docId, title: doc.title, text: chunk.text })),
  );
  const titleToDocId = new Map(prepared.map((doc) => [doc.title, doc.docId]));
  const queries = readFileSync(resolve(root, "data/eval/queries.jsonl"), "utf8")
    .split("\n")
    .filter((line) => line.trim().length > 0)
    .map((line) => JSON.parse(line) as { query: string; target: string });

  console.log(`[eval] model=${modelId} dtype=${dtype} docs=${corpus.length} chunks=${chunks.length} queries=${queries.length}`);
  console.log(`[eval] mode=${inproc ? "in-proc cosine" : `seekdb @ ${apiBase}`} collection=${inproc ? "-" : collection}`);

  const started = performance.now();
  const extractor = await pipeline("feature-extraction", modelId, { dtype: dtype as never, device: "cpu" });
  const loadMs = performance.now() - started;

  const embedStart = performance.now();
  const docVectors: number[][] = [];
  for (let i = 0; i < chunks.length; i += batchSize) {
    const slice = chunks.slice(i, i + batchSize).map((c) => `${c.title}\n${c.text}`);
    const out = await extractor(slice, { pooling: "cls", normalize: true });
    docVectors.push(...(out.tolist() as number[][]));
  }
  const embedMs = performance.now() - embedStart;
  const docsPerSecond = chunks.length / (embedMs / 1000);
  console.log(`[eval] model load ${loadMs.toFixed(0)} ms; ${chunks.length} chunks in ${embedMs.toFixed(0)} ms (${docsPerSecond.toFixed(1)} chunks/s)`);

  let searchFn: (queryVector: number[], text: string) => Promise<string[]>;
  let writeMs = 0;
  let refreshMs = 0;

  if (inproc) {
    searchFn = async (vector) => {
      const scored = chunks
        .map((chunk, index) => ({ id: chunk.docId, score: cosine(vector, docVectors[index]) }))
        .sort((a, b) => b.score - a.score);
      return scored.slice(0, topK).map((item) => item.id);
    };
  } else {
    const health = await fetch(`${apiBase}/api/health`).then((r) => r.json());
    if (!health?.ok) throw new Error(`server not healthy: ${JSON.stringify(health)}`);
    await fetch(`${apiBase}/api/collections`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name: collection }),
    });

    const writeStart = performance.now();
    for (let i = 0; i < chunks.length; i += 64) {
      const slice = chunks.slice(i, i + 64);
      const isLast = i + 64 >= chunks.length;
      const res = await fetch(`${apiBase}/api/collections/${collection}/docs`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          docs: slice.map((chunk, k) => ({
            id: chunk.id,
            text: `${chunk.title}\n${chunk.text}`,
            meta: { title: chunk.title, docId: chunk.docId },
            vector: docVectors[i + k],
          })),
          refresh: isLast,
        }),
      });
      if (!res.ok) throw new Error(`write failed: ${await res.text()}`);
      const body = (await res.json()) as { refresh?: { ms: number } };
      if (body.refresh) refreshMs = body.refresh.ms;
    }
    writeMs = performance.now() - writeStart;

    searchFn = async (vector, text) => {
      const res = await fetch(`${apiBase}/api/collections/${collection}/search`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ vector, text, topK, mode: "hybrid" }),
      });
      if (!res.ok) throw new Error(`search failed: ${await res.text()}`);
      const body = (await res.json()) as { hits: { id: string; meta: { docId?: string } }[] };
      return body.hits.map((hit) => String(hit.meta?.docId ?? ""));
    };
  }

  const queryVectors: number[][] = [];
  const queryStart = performance.now();
  for (const item of queries) {
    const out = await extractor([`${QUERY_PREFIX}${item.query}`], { pooling: "cls", normalize: true });
    queryVectors.push((out.tolist() as number[][])[0]);
  }
  const queryEmbedMs = performance.now() - queryStart;

  const recalls: number[] = [];
  const ndcgs: number[] = [];
  const rrs: number[] = [];
  const perQuery: unknown[] = [];

  for (let i = 0; i < queries.length; i++) {
    const target = titleToDocId.get(queries[i].target);
    if (!target) {
      console.warn(`[eval] unknown target title: ${queries[i].target}`);
      continue;
    }
    const rawRanked = await searchFn(queryVectors[i], queries[i].query);
    // Document-level ranking: several chunks may map to the same document.
    const ranked = [...new Set(rawRanked)];
    const relevant = new Set([target]);

    const recall = recallAtK(ranked, relevant, 5);
    recalls.push(recall);
    ndcgs.push(ndcgAtK(ranked, relevant, 10));
    rrs.push(reciprocalRank(ranked, relevant));
    perQuery.push({
      query: queries[i].query,
      target: queries[i].target,
      rank: ranked.indexOf(target) + 1,
      inTop5: recall,
    });
  }

  const n = perQuery.length;
  const result = {
    label,
    dtype,
    model: modelId,
    mode: inproc ? "in-process" : "seekdb-hybrid",
    docs: corpus.length,
    chunks: chunks.length,
    queries: n,
    loadMs: Number(loadMs.toFixed(0)),
    docEmbedMs: Number(embedMs.toFixed(0)),
    chunksPerSecond: Number(docsPerSecond.toFixed(2)),
    queryEmbedMsAvg: Number((queryEmbedMs / queries.length).toFixed(2)),
    writeMs: Number(writeMs.toFixed(0)),
    refreshMs: Number(refreshMs.toFixed(0)),
    recall5: Number(mean(recalls).toFixed(4)),
    ndcg10: Number(mean(ndcgs).toFixed(4)),
    mrr: Number(mean(rrs).toFixed(4)),
    perQuery,
  };

  mkdirSync(resolve(root, "results"), { recursive: true });
  const outFile = resolve(root, `results/eval-${label}.json`);
  writeFileSync(outFile, JSON.stringify(result, null, 2), "utf8");

  console.log("\n=== summary ===");
  console.table([
    {
      config: label,
      dtype,
      chunks: result.chunks,
      "chunks/s": result.chunksPerSecond,
      "Recall@5": result.recall5,
      "nDCG@10": result.ndcg10,
      MRR: result.mrr,
    },
  ]);
  console.log(`[eval] wrote ${outFile}`);

  if (!inproc && !args.keep) {
    await fetch(`${apiBase}/api/collections/${collection}`, { method: "DELETE" }).catch(() => undefined);
    console.log(`[eval] dropped evaluation collection ${collection}`);
  }
}

main().catch((err) => {
  console.error("[eval] fatal:", err);
  process.exit(1);
});
