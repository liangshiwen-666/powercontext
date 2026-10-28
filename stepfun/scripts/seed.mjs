#!/usr/bin/env node
/**
 * Parse data/corpus.md (documents separated by `## Title`) and emit
 * web/public/sample/corpus.json so the UI can load a demo corpus offline.
 *
 * Add your own markdown files next to corpus.md, or pass paths on the CLI:
 *   node scripts/seed.mjs data/corpus.md data/my-notes.md
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outFile = resolve(root, "web/public/sample/corpus.json");

const args = process.argv.slice(2);
const files = args.length > 0 ? args : [resolve(root, "data/corpus.md")];

export function parseCorpus(markdown) {
  const sections = [];
  const re = /^##\s+(.+?)\s*$/gm;
  const hits = [...markdown.matchAll(re)];
  for (let i = 0; i < hits.length; i++) {
    const title = hits[i][1].trim();
    const start = hits[i].index + hits[i][0].length;
    const end = i + 1 < hits.length ? hits[i + 1].index : markdown.length;
    const body = markdown.slice(start, end).trim();
    if (body.length > 0) sections.push({ title, body });
  }
  return sections;
}

const all = [];
for (const file of files) {
  if (!existsSync(file)) {
    console.error(`[seed] missing file: ${file}`);
    continue;
  }
  const parsed = parseCorpus(readFileSync(file, "utf8"));
  console.log(`[seed] ${file}: ${parsed.length} documents`);
  all.push(...parsed);
}

if (all.length === 0) {
  console.error("[seed] no documents parsed; expected `## Title` sections");
  process.exit(1);
}

mkdirSync(dirname(outFile), { recursive: true });
writeFileSync(outFile, JSON.stringify(all, null, 2), "utf8");

const chars = all.reduce((sum, d) => sum + d.body.length, 0);
console.log(`[seed] wrote ${all.length} documents (${chars} chars) -> ${outFile}`);
