#!/usr/bin/env node
/**
 * Download the embedding model into web/public/models/ so the app can run offline.
 *
 *   node scripts/fetch-model.mjs            # int8 + fp16 + tokenizer (~72 MB)
 *   node scripts/fetch-model.mjs --all      # also fp32 baseline (~167 MB)
 *   node scripts/fetch-model.mjs --host https://huggingface.co
 *
 * transformers.js resolves `Xenova/bge-small-zh-v1.5` against
 * web/public/models/Xenova/bge-small-zh-v1.5/<file> when the files exist.
 */
import { createWriteStream, existsSync, mkdirSync, statSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Readable } from "node:stream";
import { pipeline as streamPipeline } from "node:stream/promises";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const repo = process.env.EMBED_MODEL_ID ?? "Xenova/bge-small-zh-v1.5";

const argv = process.argv.slice(2);
const all = argv.includes("--all");
const hostArg = argv.indexOf("--host");
const host = (hostArg >= 0 ? argv[hostArg + 1] : process.env.MODEL_HOST) ?? "https://hf-mirror.com";

const baseFiles = [
  "config.json",
  "tokenizer.json",
  "tokenizer_config.json",
  "special_tokens_map.json",
  "vocab.txt",
];
const onnxFiles = all
  ? ["onnx/model.onnx", "onnx/model_fp16.onnx", "onnx/model_quantized.onnx"]
  : ["onnx/model_fp16.onnx", "onnx/model_quantized.onnx"];

const files = [...baseFiles, ...onnxFiles];
const target = resolve(root, "web/public/models", repo);

console.log(`[model] repo   : ${repo}`);
console.log(`[model] host   : ${host}`);
console.log(`[model] target : ${target}`);

async function download(file) {
  const url = `${host}/${repo}/resolve/main/${file}`;
  const destination = resolve(target, file);
  mkdirSync(dirname(destination), { recursive: true });

  if (existsSync(destination) && statSync(destination).size > 0) {
    const mb = (statSync(destination).size / 1024 / 1024).toFixed(1);
    console.log(`[model] skip ${file} (${mb} MB already present)`);
    return statSync(destination).size;
  }

  const res = await fetch(url, { redirect: "follow" });
  if (!res.ok || !res.body) throw new Error(`${file}: HTTP ${res.status} (${url})`);

  const total = Number(res.headers.get("content-length") ?? 0);
  let written = 0;
  const stream = Readable.fromWeb(res.body);
  stream.on("data", (chunk) => {
    written += chunk.length;
    if (total > 0) {
      const pct = ((written / total) * 100).toFixed(0);
      process.stdout.write(`\r[model] ${file} ${pct}%`);
    }
  });
  await streamPipeline(stream, createWriteStream(destination));
  process.stdout.write("\n");
  console.log(`[model] done ${file} (${(written / 1024 / 1024).toFixed(1)} MB)`);
  return written;
}

let bytes = 0;
for (const file of files) {
  bytes += await download(file);
}
console.log(`[model] total ${(bytes / 1024 / 1024).toFixed(1)} MB in ${target}`);
