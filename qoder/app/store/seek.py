"""seekdb 存储与混合检索：向量通道 + 全文通道 + 标量过滤，客户端 RRF 融合。

这里刻意不用 pyseekdb 的服务端 hybrid_search：它一次只接受一个 `$contains` 条件，
而我们的问题通常能抽到多个关键词；客户端融合还能把两条通道的命中分别展示出来，
作为「seekdb 真的在干活」的证据。
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import jieba
import pyseekdb
from pyseekdb import HNSWConfiguration

from ..config import settings
from ..embedding import build_embedding_function, embedding_label
from ..ingest import Chunk

RUN_RE = re.compile(r"[\u4e00-\u9fa5]+|[A-Za-z0-9_]+")
STOPWORDS = {
    "怎么", "什么", "如何", "哪些", "可以", "需要", "这个", "那个", "今天", "请问",
    "一下", "为什么", "是不是", "有没有", "应该", "我们", "他们", "你们", "怎样",
    "多少", "之间", "以及", "但是", "因为", "所以", "还是", "就是", "一个", "这种",
    "问题", "时候",
}
RRF_K = 60


def extract_keywords(question: str, max_keywords: int = 4) -> list[str]:
    """用 jieba 切词后保留实词做全文通道，避免 2 字滑窗切出「锁的」这类噪声。"""
    keywords: list[str] = []
    for token in jieba.lcut(question):
        token = token.strip().lower()
        if len(token) < 2 or token in STOPWORDS:
            continue
        if not re.search(r"[\u4e00-\u9fa5A-Za-z0-9]", token):
            continue
        if token not in keywords:
            keywords.append(token)
    return keywords[:max_keywords]


def _flatten(payload: dict, key: str) -> list:
    value = payload.get(key)
    if not value:
        return []
    if isinstance(value[0], list):
        return value[0]
    return value


class SeekStore:
    def __init__(self) -> None:
        self.embedding = build_embedding_function()
        self.dimension = self.embedding.dimension
        self.embedding_name = embedding_label(self.embedding)

        if settings.seekdb_mode == "remote":
            self.client = pyseekdb.Client(
                host=settings.seekdb_host,
                port=settings.seekdb_port,
                database=settings.seekdb_database,
                user=settings.seekdb_user,
                password=settings.seekdb_password,
            )
        else:
            path = Path(settings.seekdb_path)
            if not path.is_absolute():
                path = Path(__file__).resolve().parents[2] / path
            path.parent.mkdir(parents=True, exist_ok=True)
            # 嵌入式模式下库名固定用 seekdb 默认库，不额外建 database
            self.client = pyseekdb.Client(path=str(path))

        self.collection_name = f"materials_{self.dimension}"
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=self.embedding,
            configuration=HNSWConfiguration(dimension=self.dimension, distance="cosine"),
        )

    # ---- 写入 ----
    def add_chunks(self, chunks: list[Chunk]) -> int:
        if not chunks:
            return 0
        self.collection.upsert(
            ids=[c.id for c in chunks],
            documents=[c.text for c in chunks],
            metadatas=[
                {
                    "doc_id": c.doc_id,
                    "title": c.title,
                    "section": c.section,
                    "page": c.page,
                    "tags": ",".join(c.tags),
                }
                for c in chunks
            ],
        )
        # 新写入的向量要等索引流水线落地，立刻查询可能为空
        refresh = getattr(self.collection, "refresh_index", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:  # 索引刷新失败不影响数据落库
                pass
        return len(chunks)

    def delete_doc(self, doc_id: str) -> None:
        self.collection.delete(where={"doc_id": {"$eq": doc_id}})

    def count(self) -> int:
        return int(self.collection.count())

    # ---- 检索 ----
    def search(self, question: str, top_k: int = 6, keywords: list[str] | None = None) -> dict:
        started = time.perf_counter()
        keywords = (keywords if keywords is not None else extract_keywords(question)) or []
        pool = max(top_k * 3, 12)

        vector_hits = self._vector_channel(question, pool)
        keyword_hits, keyword_map = self._keyword_channel(keywords, pool)

        # 本地嵌入模型对中文排序不可靠，此时把权重让给全文通道
        vector_weight = 1.0 if settings.use_api_embedding else 0.45
        fused: dict[str, dict] = {}
        for rank, hit in enumerate(vector_hits):
            entry = fused.setdefault(hit["id"], {**hit, "score": 0.0, "sources": [], "coverage": 0})
            entry["score"] += vector_weight / (RRF_K + rank + 1)
            entry["sources"].append("vector")
        for rank, hit in enumerate(keyword_hits):
            entry = fused.setdefault(hit["id"], {**hit, "score": 0.0, "sources": [], "coverage": 0})
            entry["score"] += 1.0 / (RRF_K + rank + 1)
            if "keyword" not in entry["sources"]:
                entry["sources"].append("keyword")
        for chunk_id, matched in keyword_map.items():
            if chunk_id in fused:
                fused[chunk_id]["coverage"] = len(matched)
                fused[chunk_id]["matched_keywords"] = matched
                fused[chunk_id]["score"] *= 1 + 0.35 * (len(matched) - 1)

        # 章节标题/文件名命中关键词是强信号，额外加权
        for entry in fused.values():
            meta = entry["meta"]
            heading = f"{meta.get('title', '')}{meta.get('section', '')}"
            heading_hits = sum(1 for kw in keywords if kw and kw in heading)
            if heading_hits:
                entry["score"] *= 1 + 0.25 * heading_hits
                entry["heading_hits"] = heading_hits

        hits = sorted(fused.values(), key=lambda x: x["score"], reverse=True)[:top_k]
        for hit in hits:
            hit["score"] = round(hit["score"], 5)
            hit["citation"] = self._citation(hit["meta"])

        return {
            "question": question,
            "keywords": keywords,
            "hits": hits,
            "channels": {
                "vector": [
                    {"id": h["id"], "distance": h.get("distance"), "title": h["meta"].get("title")}
                    for h in vector_hits[:5]
                ],
                "keyword": [
                    {
                        "id": h["id"],
                        "title": h["meta"].get("title"),
                        "matched_keywords": keyword_map.get(h["id"], []),
                    }
                    for h in keyword_hits[:5]
                ],
            },
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "collection": self.collection_name,
            "dimension": self.dimension,
            "embedding": self.embedding_name,
            "total_chunks": self.count(),
        }

    def _vector_channel(self, question: str, pool: int) -> list[dict]:
        result = self.collection.query(
            query_texts=[question],
            n_results=pool,
            include=["documents", "metadatas"],
        )
        ids = _flatten(result, "ids")
        docs = _flatten(result, "documents")
        metas = _flatten(result, "metadatas")
        dists = _flatten(result, "distances")
        out = []
        for index, chunk_id in enumerate(ids):
            out.append(
                {
                    "id": chunk_id,
                    "text": docs[index] if index < len(docs) else "",
                    "meta": metas[index] if index < len(metas) else {},
                    "distance": round(dists[index], 4) if index < len(dists) else None,
                }
            )
        return out

    def _keyword_channel(self, keywords: list[str], pool: int) -> tuple[list[dict], dict[str, list[str]]]:
        if not keywords:
            return [], {}
        merged: dict[str, dict] = {}
        matched: dict[str, list[str]] = {}
        for kw in keywords:
            try:
                result = self.collection.get(
                    where_document={"$contains": kw},
                    limit=pool,
                    include=["documents", "metadatas"],
                )
            except Exception:
                continue
            ids = _flatten(result, "ids")
            docs = _flatten(result, "documents")
            metas = _flatten(result, "metadatas")
            for index, chunk_id in enumerate(ids):
                merged.setdefault(
                    chunk_id,
                    {
                        "id": chunk_id,
                        "text": docs[index] if index < len(docs) else "",
                        "meta": metas[index] if index < len(metas) else {},
                    },
                )
                matched.setdefault(chunk_id, []).append(kw)
        ordered = sorted(merged.values(), key=lambda h: len(matched.get(h["id"], [])), reverse=True)
        return ordered, matched

    @staticmethod
    def _citation(meta: dict) -> str:
        parts = [str(meta.get("title") or "")]
        if meta.get("section"):
            parts.append(str(meta["section"]))
        if meta.get("page"):
            parts.append(f"第 {meta['page']} 页")
        return " · ".join(p for p in parts if p)
