export interface ChunkOptions {
  /** Target chunk length in characters. */
  size?: number;
  /** Characters of the previous chunk repeated at the start of the next one. */
  overlap?: number;
}

const SENTENCE_SPLIT = /(?<=[。！？!?；;\n])/u;

/**
 * Split a document into overlapping chunks, preferring sentence boundaries.
 * Character based (not tokeniser based) so it works identically for zh/en text.
 */
export function chunkText(input: string, { size = 400, overlap = 80 }: ChunkOptions = {}): string[] {
  const safeOverlap = Math.max(0, Math.min(overlap, Math.floor(size / 2)));
  const text = input.replace(/\r\n/g, "\n").replace(/[ \t]+/g, " ").trim();
  if (!text) return [];
  if (text.length <= size) return [text];

  const sentences = text.split(SENTENCE_SPLIT).filter((s) => s.trim().length > 0);
  const chunks: string[] = [];
  let buf = "";

  const flush = () => {
    const value = buf.trim();
    if (value) chunks.push(value);
  };

  for (const sentence of sentences) {
    if (buf.length > 0 && buf.length + sentence.length > size) {
      flush();
      buf = safeOverlap > 0 ? buf.slice(Math.max(0, buf.length - safeOverlap)) : "";
    }
    buf += sentence;
    while (buf.length > size * 1.5) {
      chunks.push(buf.slice(0, size).trim());
      buf = buf.slice(size - safeOverlap);
    }
  }
  flush();

  return chunks.filter((c) => c.trim().length > 0);
}

export interface PreparedDoc {
  docId: string;
  title: string;
  chunks: { id: string; text: string; index: number }[];
}

/** Chunk a single source file/text into a prepared document with stable ids. */
export function prepareDocument(title: string, body: string, opts?: ChunkOptions): PreparedDoc {
  const docId = `doc-${hash(title + body.length)}`;
  const parts = chunkText(body, opts);
  return {
    docId,
    title,
    chunks: parts.map((text, index) => ({ id: `${docId}-${index}`, text, index })),
  };
}

function hash(input: string): string {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h.toString(16);
}
