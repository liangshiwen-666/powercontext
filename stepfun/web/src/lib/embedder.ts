import type { BackendInfo } from "./types";

export interface ModelProgress {
  status?: string;
  file?: string;
  progress?: number;
  loaded?: number;
  total?: number;
}

interface PendingEntry {
  resolve: (value: { vectors: number[][]; ms: number }) => void;
  reject: (err: Error) => void;
}

/** Thin RPC wrapper around the embedding Web Worker. */
export class Embedder {
  private worker: Worker;
  private seq = 1;
  private pending = new Map<number, PendingEntry>();
  private readyResolve!: (info: BackendInfo) => void;
  private readyReject!: (err: Error) => void;

  readonly ready: Promise<BackendInfo>;
  backend: BackendInfo | null = null;

  onProgress?: (p: ModelProgress) => void;
  onEmbedProgress?: (done: number, total: number) => void;

  constructor() {
    this.worker = new Worker(new URL("../worker/embed.worker.ts", import.meta.url), { type: "module" });
    this.worker.onmessage = (event: MessageEvent) => this.handle(event.data);
    this.worker.onerror = (event) => this.readyReject(new Error(event.message || "worker 启动失败"));
    this.ready = new Promise<BackendInfo>((resolve, reject) => {
      this.readyResolve = resolve;
      this.readyReject = reject;
    });
  }

  init(model?: string): Promise<BackendInfo> {
    this.worker.postMessage({ type: "init", model });
    return this.ready;
  }

  embed(texts: string[], kind: "doc" | "query"): Promise<{ vectors: number[][]; ms: number }> {
    const id = this.seq++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.worker.postMessage({ type: "embed", id, texts, kind });
    });
  }

  private handle(msg: { type: string; [key: string]: unknown }) {
    switch (msg.type) {
      case "progress":
        this.onProgress?.(msg as unknown as ModelProgress);
        break;
      case "embed-progress":
        this.onEmbedProgress?.(Number(msg.done), Number(msg.total));
        break;
      case "ready":
        this.backend = msg.info as BackendInfo;
        this.readyResolve(this.backend);
        break;
      case "init-error":
        this.readyReject(new Error(String(msg.message)));
        break;
      case "result": {
        const entry = this.pending.get(Number(msg.id));
        this.pending.delete(Number(msg.id));
        entry?.resolve({ vectors: msg.vectors as number[][], ms: Number(msg.ms) });
        break;
      }
      case "error": {
        const entry = this.pending.get(Number(msg.id));
        this.pending.delete(Number(msg.id));
        entry?.reject(new Error(String(msg.message)));
        break;
      }
      default:
        break;
    }
  }

  dispose() {
    this.worker.terminate();
  }
}
