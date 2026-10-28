import { env, pipeline, type FeatureExtractionPipeline } from "@huggingface/transformers";

type InitMessage = { type: "init"; model?: string; dtypeWebgpu?: string; dtypeWasm?: string };
type EmbedMessage = { type: "embed"; id: number; texts: string[]; kind: "doc" | "query" };

type Incoming = InitMessage | EmbedMessage;

const DEFAULT_MODEL = "Xenova/bge-small-zh-v1.5";
/** BGE retrieval instruction (Chinese). Applied to queries only. */
const QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章：";
const SUB_BATCH = 8;

let extractor: FeatureExtractionPipeline | null = null;
let modelId = DEFAULT_MODEL;

const post = (msg: unknown) => (self as unknown as { postMessage(m: unknown): void }).postMessage(msg);

function configureEnv() {
  env.allowLocalModels = true;
  env.localModelPath = "/models/";
  env.allowRemoteModels = true;
  env.useBrowserCache = true;
  // Fall back to a mirror only when the weights are not vendored locally.
  env.remoteHost = (import.meta.env.VITE_MODEL_HOST as string | undefined)?.trim() || "https://hf-mirror.com";
}

async function load(dtype: string, device: "webgpu" | "wasm") {
  const started = performance.now();
  const instance = await pipeline("feature-extraction", modelId, {
    dtype: dtype as never,
    device,
    progress_callback: (p: { status?: string; file?: string; progress?: number; loaded?: number; total?: number }) => {
      post({ type: "progress", ...p });
    },
  });
  return { instance, ms: performance.now() - started };
}

async function describeAdapter(): Promise<string | undefined> {
  try {
    const gpu = (navigator as unknown as { gpu?: { requestAdapter(): Promise<unknown> } }).gpu;
    if (!gpu) return undefined;
    const adapter = (await gpu.requestAdapter()) as
      | { info?: { vendor?: string; architecture?: string } }
      | null;
    const info = adapter?.info;
    if (!info) return "webgpu";
    return [info.vendor, info.architecture].filter(Boolean).join(" ") || "webgpu";
  } catch {
    return undefined;
  }
}

async function init(msg: InitMessage) {
  modelId = msg.model?.trim() || DEFAULT_MODEL;
  const dtypeWebgpu = msg.dtypeWebgpu ?? "fp16";
  const dtypeWasm = msg.dtypeWasm ?? "q8";
  configureEnv();

  const reasons: string[] = [];
  const gpu = (navigator as unknown as { gpu?: unknown }).gpu;
  if (!gpu) {
    reasons.push("navigator.gpu 不存在（浏览器未启用 WebGPU）");
  } else {
    try {
      const { instance, ms } = await load(dtypeWebgpu, "webgpu");
      extractor = instance;
      post({
        type: "ready",
        info: { backend: "webgpu", dtype: dtypeWebgpu, loadMs: ms, adapter: await describeAdapter() },
      });
      return;
    } catch (err) {
      reasons.push(`WebGPU 初始化失败：${(err as Error)?.message ?? err}`);
    }
  }

  try {
    const { instance, ms } = await load(dtypeWasm, "wasm");
    extractor = instance;
    post({
      type: "ready",
      info: { backend: "wasm", dtype: dtypeWasm, loadMs: ms, reason: reasons.join("；") || undefined },
    });
  } catch (err) {
    post({ type: "init-error", message: `模型加载失败：${(err as Error)?.message ?? err}` });
  }
}

async function embed(msg: EmbedMessage) {
  if (!extractor) {
    post({ type: "error", id: msg.id, message: "模型尚未就绪" });
    return;
  }
  const started = performance.now();
  const vectors: number[][] = [];
  try {
    for (let i = 0; i < msg.texts.length; i += SUB_BATCH) {
      const slice = msg.texts.slice(i, i + SUB_BATCH);
      const prepared =
        msg.kind === "query" ? slice.map((t) => `${QUERY_PREFIX}${t}`) : slice;
      const output = await extractor(prepared, { pooling: "cls", normalize: true });
      const list = output.tolist() as number[][];
      for (const vec of list) vectors.push(vec);
      post({ type: "embed-progress", id: msg.id, done: Math.min(i + slice.length, msg.texts.length), total: msg.texts.length });
    }
    post({ type: "result", id: msg.id, vectors, ms: performance.now() - started });
  } catch (err) {
    post({ type: "error", id: msg.id, message: (err as Error)?.message ?? String(err) });
  }
}

self.onmessage = (event: MessageEvent<Incoming>) => {
  const data = event.data;
  if (data.type === "init") void init(data);
  else if (data.type === "embed") void embed(data);
};
