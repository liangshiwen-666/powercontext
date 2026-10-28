import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./lib/api";
import { prepareDocument } from "./lib/chunk";
import { Embedder } from "./lib/embedder";
import { median, percentile } from "./lib/metrics";
import type {
  BackendInfo,
  CollectionInfo,
  HealthInfo,
  SearchMode,
  SearchOutcome,
  TimingEntry,
} from "./lib/types";

const DOC_BATCH = 64;
const MODEL_ID = (import.meta.env.VITE_MODEL_ID as string | undefined) ?? "Xenova/bge-small-zh-v1.5";

type Phase = "idle" | "reading" | "embedding" | "writing" | "refreshing";

interface Progress {
  phase: Phase;
  done: number;
  total: number;
  note?: string;
}

interface Source {
  title: string;
  body: string;
}

export default function App() {
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [collections, setCollections] = useState<CollectionInfo[]>([]);
  const [current, setCurrent] = useState<string>("");
  const [backend, setBackend] = useState<BackendInfo | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const [modelNote, setModelNote] = useState<string>("");
  const [progress, setProgress] = useState<Progress | null>(null);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<SearchMode>("hybrid");
  const [result, setResult] = useState<SearchOutcome | null>(null);
  const [timings, setTimings] = useState<TimingEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastIngest, setLastIngest] = useState<{ docs: number; chunks: number; ms: number } | null>(null);
  const [bench, setBench] = useState<{
    runs: number[];
    p50: number;
    p95: number;
    backend: string;
    dtype: string;
    mode: SearchMode;
  } | null>(null);

  const embedderRef = useRef<Embedder | null>(null);
  const currentRef = useRef<string>("");
  currentRef.current = current;

  const addTiming = useCallback((label: string, ms: number, detail?: string) => {
    setTimings((prev) => [...prev.slice(-59), { ts: Date.now(), label, ms, detail }]);
  }, []);

  const refreshCollections = useCallback(async () => {
    const { collections } = await api.listCollections();
    setCollections(collections);
    return collections;
  }, []);

  useEffect(() => {
    let cancelled = false;
    const embedder = new Embedder();
    embedderRef.current = embedder;
    embedder.onProgress = (p) => {
      if (p.status === "progress" && p.file) {
        setModelNote(`下载 ${p.file} ${p.progress ? Math.round(p.progress) + "%" : ""}`);
      } else if (p.status === "ready") {
        setModelNote("模型已就绪");
      }
    };
    embedder.onEmbedProgress = (done, total) =>
      setProgress((prev) => (prev ? { ...prev, done, total } : prev));

    (async () => {
      try {
        const h = await api.health();
        if (cancelled) return;
        setHealth(h);
        const list = await refreshCollections();
        const name = list[0]?.name ?? "kb";
        setCurrent(name);
        currentRef.current = name;

        const t0 = performance.now();
        const info = await embedder.init(MODEL_ID);
        if (cancelled) return;
        addTiming("模型加载（首次）", performance.now() - t0, `${info.backend}/${info.dtype}`);
        setBackend(info);
        setModelNote("");
      } catch (err) {
        if (!cancelled) setBootError((err as Error).message);
      }
    })();

    return () => {
      cancelled = true;
      embedder.dispose();
    };
  }, [addTiming, refreshCollections]);

  const ingest = useCallback(
    async (sources: Source[]) => {
      const embedder = embedderRef.current;
      if (!embedder?.backend || !currentRef.current) return;
      if (sources.length === 0) return;
      setBusy(true);
      setError(null);
      const started = performance.now();
      try {
        setProgress({ phase: "reading", done: 0, total: sources.length });
        const prepared = sources.map((s) => prepareDocument(s.title, s.body));
        const chunks = prepared.flatMap((doc) =>
          doc.chunks.map((c) => ({ ...c, docId: doc.docId, title: doc.title })),
        );
        if (chunks.length === 0) throw new Error("没有可导入的文本内容");

        setProgress({ phase: "embedding", done: 0, total: chunks.length });
        const vectors: number[][] = [];
        const embedStarted = performance.now();
        for (let i = 0; i < chunks.length; i += DOC_BATCH) {
          const slice = chunks.slice(i, i + DOC_BATCH);
          const out = await embedder.embed(
            slice.map((c) => c.text),
            "doc",
          );
          vectors.push(...out.vectors);
          setProgress({ phase: "embedding", done: vectors.length, total: chunks.length });
        }
        const embedMs = performance.now() - embedStarted;
        addTiming("文档向量化", embedMs, `${chunks.length} chunks · ${(chunks.length / (embedMs / 1000)).toFixed(1)} chunks/s`);

        setProgress({ phase: "writing", done: 0, total: chunks.length });
        const writeStarted = performance.now();
        let written = 0;
        for (let i = 0; i < chunks.length; i += 128) {
          const slice = chunks.slice(i, i + 128);
          const docs = slice.map((c, k) => ({
            id: c.id,
            text: c.text,
            meta: {
              title: c.title,
              docId: c.docId,
              chunk: c.index,
              source: c.title,
            },
            vector: vectors[i + k],
          }));
          const isLast = i + 128 >= chunks.length;
          await api.addDocs(currentRef.current, docs, isLast);
          written += slice.length;
          setProgress({ phase: isLast ? "refreshing" : "writing", done: written, total: chunks.length });
        }
        const writeMs = performance.now() - writeStarted;
        addTiming("写入 + 索引刷新", writeMs, `${chunks.length} chunks`);

        await refreshCollections();
        setLastIngest({ docs: prepared.length, chunks: chunks.length, ms: performance.now() - started });
        setProgress({ phase: "idle", done: chunks.length, total: chunks.length });
      } catch (err) {
        setError((err as Error).message);
        setProgress(null);
      } finally {
        setBusy(false);
      }
    },
    [addTiming, refreshCollections],
  );

  const onFiles = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      const sources: Source[] = [];
      for (const file of Array.from(files)) {
        const body = await file.text();
        if (body.trim()) sources.push({ title: file.name, body });
      }
      await ingest(sources);
    },
    [ingest],
  );

  const loadSample = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/sample/corpus.json");
      if (!res.ok) throw new Error("示例语料缺失，请先运行 npm run seed 或上传自己的文件");
      const data = (await res.json()) as Source[];
      setBusy(false);
      await ingest(data);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }, [ingest]);

  const runSearch = useCallback(async () => {
    const embedder = embedderRef.current;
    const text = query.trim();
    if (!embedder?.backend || !text || !currentRef.current) return;
    setBusy(true);
    setError(null);
    try {
      const t0 = performance.now();
      const embedded = await embedder.embed([text], "query");
      const embedMs = performance.now() - t0;
      const outcome = await api.search(currentRef.current, {
        vector: embedded.vectors[0],
        text,
        topK: 10,
        mode,
      });
      const e2e = performance.now() - t0;
      setResult(outcome);
      addTiming("查询向量化", embedMs, "1 query");
      addTiming(`seekdb 检索（${outcome.mode}）`, outcome.searchMs, `topK=10`);
      addTiming("端到端（提交→结果可展示）", e2e);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }, [addTiming, mode, query]);

  /**
   * Repeat the current query N times and report P50/P95 of the end-to-end
   * latency (query encoding + HTTP + seekdb retrieval). One warm-up run is
   * discarded so the first cold call does not skew the distribution.
   */
  const runBenchmark = useCallback(
    async (runs = 10) => {
      const embedder = embedderRef.current;
      const text = query.trim();
      if (!embedder?.backend || !text || !currentRef.current) return;
      setBusy(true);
      setError(null);
      const samples: number[] = [];
      try {
        const one = async () => {
          const t0 = performance.now();
          const embedded = await embedder.embed([text], "query");
          await api.search(currentRef.current, {
            vector: embedded.vectors[0],
            text,
            topK: 10,
            mode,
          });
          return performance.now() - t0;
        };
        await one(); // warm-up, discarded
        for (let i = 0; i < runs; i++) samples.push(await one());
        const p50 = median(samples);
        const p95 = percentile(samples, 95);
        setBench({
          runs: samples,
          p50,
          p95,
          backend: embedder.backend.backend,
          dtype: embedder.backend.dtype,
          mode,
        });
        addTiming(`基准 ×${runs}（P50）`, p50, `${embedder.backend.backend}/${embedder.backend.dtype}`);
        addTiming(`基准 ×${runs}（P95）`, p95, "端到端");
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [addTiming, mode, query],
  );

  const switchCollection = useCallback(async (name: string) => {
    setCurrent(name);
    currentRef.current = name;
    setResult(null);
    setBench(null);
  }, []);

  const createCollection = useCallback(async () => {
    const name = window.prompt("新集合名称（字母/数字/下划线）", `kb_${Date.now().toString(36)}`);
    if (!name) return;
    try {
      await api.createCollection(name);
      await refreshCollections();
      await switchCollection(name);
    } catch (err) {
      setError((err as Error).message);
    }
  }, [refreshCollections, switchCollection]);

  const clearCollection = useCallback(async () => {
    if (!current) return;
    if (!window.confirm(`清空集合 ${current} 的全部数据？`)) return;
    setBusy(true);
    try {
      await api.clearCollection(current);
      await refreshCollections();
      setResult(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }, [current, refreshCollections]);

  const refreshIndex = useCallback(async () => {
    if (!current) return;
    try {
      const { ms } = await api.refresh(current);
      addTiming("手动刷新索引", ms);
      await refreshCollections();
    } catch (err) {
      setError((err as Error).message);
    }
  }, [addTiming, current, refreshCollections]);

  const exportReport = useCallback(() => {
    const report = {
      exportedAt: new Date().toISOString(),
      userAgent: navigator.userAgent,
      model: { id: MODEL_ID, dim: health?.model.dim },
      backend,
      db: health?.db,
      collection: current,
      lastIngest,
      bench,
      timings,
      result: result
        ? { mode: result.mode, searchMs: result.searchMs, fusedInDb: result.fusedInDb, hits: result.hits.length }
        : null,
    };
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `stepfun-kb-report-${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }, [backend, bench, current, health, lastIngest, result, timings]);

  const activeCollection = useMemo(
    () => collections.find((c) => c.name === current) ?? null,
    [collections, current],
  );

  if (bootError) {
    return (
      <div className="boot-error">
        <h1>无法启动</h1>
        <pre>{bootError}</pre>
        <p>
          请确认 seekdb 服务已就绪（默认嵌入式，或设置 SEEKDB_HOST 指向本地 seekdb 实例），
          然后刷新页面。
        </p>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="dot" />
          <h1>StepFun × seekdb 端侧知识库</h1>
        </div>
        <div className="badges">
          <Badge
            tone={backend?.backend === "webgpu" ? "good" : "warn"}
            label={
              backend
                ? `${backend.backend === "webgpu" ? "WebGPU" : "WASM"} · ${backend.dtype}`
                : "模型加载中…"
            }
            title={backend?.reason ?? `推理后端：${backend?.backend ?? "-"}`}
          />
          <Badge tone="neutral" label={`模型 ${MODEL_ID.split("/").pop()}`} title={MODEL_ID} />
          <Badge
            tone="neutral"
            label={
              health ? `seekdb ${health.db.mode}${health.db.mode === "server" ? ` ${health.db.host}` : ""}` : "seekdb ?"
            }
            title={
              (health?.db.path ?? health?.db.host ?? "") +
              (health?.db.detectedBy === "probe"
                ? "（启动时自动探测：未发现本地 2881 端口则用嵌入式）"
                : "（由环境变量显式指定）")
            }
          />
        </div>
      </header>

      {health?.db.mode === "embedded" && health.db.detectedBy === "probe" && (
        <div className="notice info">
          <strong>未检测到 127.0.0.1:2881 上的 seekdb，已自动切到嵌入式模式。</strong> 数据在{" "}
          <code>{health.db.path}</code>；想用 Docker 就先 <code>docker compose up -d</code> 再重启服务。
        </div>
      )}

      {backend?.reason && (
        <div className="notice">
          <strong>已自动降级到 WASM。</strong> 原因：{backend.reason}
          {backend.adapter ? `（适配器：${backend.adapter}）` : ""}
        </div>
      )}

      {error && (
        <div className="notice error">
          <strong>出错了：</strong> {error}
          <button className="link" onClick={() => setError(null)}>
            关闭
          </button>
        </div>
      )}

      <div className="layout">
        <aside className="panel sidebar">
          <div className="panel-head">
            <h2>集合</h2>
            <button className="mini" onClick={createCollection}>
              新建
            </button>
          </div>
          <ul className="collection-list">
            {collections.map((c) => (
              <li
                key={c.name}
                className={c.name === current ? "active" : ""}
                onClick={() => void switchCollection(c.name)}
              >
                <span className="name">{c.name}</span>
                <span className="count">{c.count}</span>
              </li>
            ))}
            {collections.length === 0 && <li className="empty">暂无集合</li>}
          </ul>

          <div className="sidebar-actions">
            <button onClick={refreshIndex} disabled={!current || busy}>
              刷新索引
              {activeCollection && activeCollection.pendingRefresh > 0
                ? `（${activeCollection.pendingRefresh} 条待刷新）`
                : ""}
            </button>
            <button className="danger" onClick={clearCollection} disabled={!current || busy}>
              清空当前集合
            </button>
          </div>

          <dl className="kv">
            <dt>数据库模式</dt>
            <dd>
              {health?.db.mode ?? "-"}
              {health?.db.detectedBy === "probe" ? " · 自动探测" : health ? " · 显式配置" : ""}
            </dd>
            <dt>库名</dt>
            <dd>{health?.db.database ?? "-"}</dd>
            <dt>路径 / 地址</dt>
            <dd className="mono">{health?.db.path ?? health?.db.host ?? "-"}</dd>
            <dt>向量维度</dt>
            <dd>{health?.db.dim ?? "-"}</dd>
            <dt>当前文档数</dt>
            <dd>{activeCollection?.count ?? 0}</dd>
          </dl>
        </aside>

        <main className="main">
          <section className="panel import">
            <div className="panel-head">
              <h2>导入语料</h2>
              <span className="hint">文本在浏览器内向量化，只把向量与原文写入 seekdb</span>
            </div>
            <label
              className={`dropzone ${busy ? "disabled" : ""}`}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                if (!busy) void onFiles(e.dataTransfer.files);
              }}
            >
              <input
                type="file"
                multiple
                accept=".txt,.md,.markdown,.csv,.json"
                disabled={busy}
                onChange={(e) => void onFiles(e.target.files)}
              />
              <span>拖入 .txt / .md 文件，或点击选择</span>
            </label>
            <div className="row">
              <button onClick={() => void loadSample()} disabled={busy || !backend}>
                载入示例语料
              </button>
              <span className="hint">
                {modelNote || (backend ? `加载耗时 ${Math.round(backend.loadMs)} ms` : "正在准备模型…")}
              </span>
            </div>
            {progress && (
              <div className="progress">
                <Bar value={progress.done} max={progress.total} />
                <span>
                  {phaseLabel(progress.phase)} {progress.done}/{progress.total}
                </span>
              </div>
            )}
            {lastIngest && (
              <p className="hint">
                上次导入：{lastIngest.docs} 篇 / {lastIngest.chunks} 块，耗时{" "}
                {(lastIngest.ms / 1000).toFixed(2)} s
              </p>
            )}
          </section>

          <section className="panel search">
            <div className="search-row">
              <input
                value={query}
                placeholder="输入问题，例如：seekdb 如何做混合检索？"
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void runSearch();
                }}
                disabled={busy}
              />
              <select value={mode} onChange={(e) => setMode(e.target.value as SearchMode)} disabled={busy}>
                <option value="hybrid">混合检索 (RRF)</option>
                <option value="vector">仅向量</option>
                <option value="fulltext">仅全文</option>
              </select>
              <button onClick={() => void runSearch()} disabled={busy || !backend || query.trim().length === 0}>
                检索
              </button>
              <button
                className="ghost"
                title="重复 10 次并给出 P50 / P95（首次预热丢弃）"
                onClick={() => void runBenchmark(10)}
                disabled={busy || !backend || query.trim().length === 0}
              >
                基准 ×10
              </button>
            </div>

            {bench && (
              <div className="bench">
                <span>
                  基准 ×{bench.runs.length}（{bench.mode}，{bench.backend}/{bench.dtype}）：P50{" "}
                  <strong>{bench.p50.toFixed(1)} ms</strong> · P95 <strong>{bench.p95.toFixed(1)} ms</strong>
                </span>
                <span className="hint">
                  min {Math.min(...bench.runs).toFixed(1)} / max {Math.max(...bench.runs).toFixed(1)}
                </span>
              </div>
            )}

            {result && (
              <div className="result-meta">
                <span>
                  {result.mode === "hybrid" ? "seekdb hybridSearch（向量 + 全文，库内 RRF 融合）" : result.mode === "vector" ? "seekdb 向量检索" : "seekdb 全文检索"}
                </span>
                <span>{result.searchMs.toFixed(1)} ms</span>
                <span>{result.hits.length} 条</span>
              </div>
            )}

            <ol className="results">
              {result?.hits.map((hit) => (
                <li key={hit.id}>
                  <div className="hit-head">
                    <span className="rank">#{hit.rank}</span>
                    <span className="title">{String(hit.meta.title ?? hit.meta.docId ?? hit.id)}</span>
                    <span className="score">
                      {hit.score === null ? "RRF" : `score ${hit.score.toFixed(3)}`}
                    </span>
                  </div>
                  <p dangerouslySetInnerHTML={{ __html: highlight(hit.text, query) }} />
                </li>
              ))}
              {result && result.hits.length === 0 && <li className="empty">没有命中，试试换个问法或换检索模式</li>}
            </ol>
          </section>
        </main>

        <aside className="panel timings">
          <div className="panel-head">
            <h2>耗时</h2>
            <button className="mini" onClick={exportReport}>
              导出
            </button>
          </div>
          <table>
            <tbody>
              {timings
                .slice()
                .reverse()
                .map((t, i) => (
                  <tr key={`${t.ts}-${i}`}>
                    <td className="label">
                      {t.label}
                      {t.detail && <span className="detail">{t.detail}</span>}
                    </td>
                    <td className="value">{t.ms < 1 ? "<1" : Math.round(t.ms)} ms</td>
                  </tr>
                ))}
              {timings.length === 0 && (
                <tr>
                  <td className="empty">导入或检索后这里会出现分阶段耗时</td>
                </tr>
              )}
            </tbody>
          </table>
        </aside>
      </div>

      <footer className="footer">
        <span>
          推理位置：本机浏览器（{backend?.backend ?? "-"}）｜服务端未配置任何 embedding 函数，只接收向量与原文
        </span>
        <span>{health ? `${health.db.mode} · ${health.db.database}` : ""}</span>
      </footer>
    </div>
  );
}

function Badge({ label, tone, title }: { label: string; tone: "good" | "warn" | "neutral"; title?: string }) {
  return (
    <span className={`badge ${tone}`} title={title}>
      {label}
    </span>
  );
}

function Bar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  return (
    <div className="bar">
      <div style={{ width: `${pct}%` }} />
    </div>
  );
}

function phaseLabel(phase: Phase): string {
  switch (phase) {
    case "reading":
      return "读取文件";
    case "embedding":
      return "本地向量化";
    case "writing":
      return "写入 seekdb";
    case "refreshing":
      return "刷新索引";
    default:
      return "完成";
  }
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string));
}

function highlight(text: string, query: string): string {
  const safe = escapeHtml(text);
  const terms = query
    .split(/[\s,，。?？!！]+/)
    .map((t) => t.trim())
    .filter((t) => t.length >= 2)
    .slice(0, 4);
  if (terms.length === 0) return safe;
  let out = safe;
  for (const term of terms) {
    const safeTerm = escapeHtml(term);
    out = out.split(safeTerm).join(`<mark>${safeTerm}</mark>`);
  }
  return out;
}
