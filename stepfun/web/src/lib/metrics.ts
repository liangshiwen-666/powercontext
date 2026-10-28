/** Retrieval metrics used by scripts/eval.ts and by the in-app benchmark page. */

/** 1 when at least one relevant item is in the first k results. */
export function recallAtK(ranked: string[], relevant: Set<string>, k: number): number {
  return ranked.slice(0, k).some((id) => relevant.has(id)) ? 1 : 0;
}

/** Binary-relevance nDCG@k with a single relevance grade. */
export function ndcgAtK(ranked: string[], relevant: Set<string>, k: number): number {
  let dcg = 0;
  for (let i = 0; i < Math.min(k, ranked.length); i++) {
    if (relevant.has(ranked[i])) dcg += 1 / Math.log2(i + 2);
  }
  const idealHits = Math.min(k, relevant.size);
  let idcg = 0;
  for (let i = 0; i < idealHits; i++) idcg += 1 / Math.log2(i + 2);
  return idcg === 0 ? 0 : dcg / idcg;
}

/** Reciprocal rank of the first relevant item (0 when none is found). */
export function reciprocalRank(ranked: string[], relevant: Set<string>): number {
  const index = ranked.findIndex((id) => relevant.has(id));
  return index < 0 ? 0 : 1 / (index + 1);
}

export function mean(values: number[]): number {
  return values.length === 0 ? 0 : values.reduce((a, b) => a + b, 0) / values.length;
}

export function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1] + sorted[mid]) / 2 : sorted[mid];
}

/** Nearest-rank percentile (p in 0..100). */
export function percentile(values: number[], p: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const rank = Math.ceil((p / 100) * sorted.length) - 1;
  return sorted[Math.min(Math.max(rank, 0), sorted.length - 1)];
}
