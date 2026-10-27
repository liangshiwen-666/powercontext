"""业务编排：seekdb 找证据，PowerContext 记状态，模型负责表达。"""

from __future__ import annotations

import time

import httpx

from .config import settings
from .llm import LLMUnavailable, chat
from .ingest import Chunk, parse_file
from .store.db import AppDB
from .store.memory import PowerContext, PowerContextError
from .store.seek import SeekStore

KIND_WEAK = "weak-point"
KIND_PROGRESS = "progress"

SYSTEM_QA = (
    "你是用户的复习教练。只能依据给出的资料片段回答，禁止编造。\n"
    "规则：\n"
    "1. 每个关键结论后用 [1] [2] 这样的编号标注来源，编号必须来自资料片段。\n"
    "2. 资料不足时直接说「资料里没有提到」，并指出缺少什么。\n"
    "3. 如果「用户历史记忆」里有相关薄弱点，先用一句话提醒，再正常回答。\n"
    "4. 中文作答，控制在 400 字以内，先给结论再给依据。"
)

SYSTEM_PLAN = (
    "你是复习教练。根据用户的薄弱点记忆和资料片段，给出今天的复习计划。\n"
    "要求：按优先级列出 3-5 项；每项说明为什么排在前面（引用记忆编号 m1、m2）；"
    "每项给出一个自测问题。不要编造用户没记录过的薄弱点。中文作答。"
)


class Tutor:
    def __init__(self, store: SeekStore, db: AppDB) -> None:
        self.store = store
        self.db = db
        self.memory = PowerContext(db)

    # ---- 记忆 ----
    def recall(self, user_key: str, query: str, limit: int = 5) -> tuple[list[dict], str | None]:
        try:
            data = self.memory.search(user_key, query, limit=limit)
        except PowerContextError as exc:
            return [], str(exc)
        out = []
        for hit in data.get("hits", []):
            memory = hit.get("memory", {})
            artifact = memory.get("artifact", {})
            out.append(
                {
                    "text": memory.get("text", ""),
                    "kind": memory.get("kind", ""),
                    "state": memory.get("state", ""),
                    "artifact_id": artifact.get("artifact_id", ""),
                    "revision": artifact.get("revision", 1),
                    "score": round(hit.get("score", 0.0), 5),
                    "matched_by": hit.get("matched_by", []),
                }
            )
        return out, None

    def remember_weak_point(self, user_key: str, concept: str, detail: str, citation: str) -> dict:
        text = f"薄弱点：{concept}。{detail}".strip()
        if citation:
            text = f"{text}（出处：{citation}）"
        result = self.memory.remember(user_key, KIND_WEAK, text, reason="用户标记「没懂」")
        self.db.log_event(user_key, "memory_write", {"kind": KIND_WEAK, "concept": concept, "result": result})
        return result

    def record_progress(self, user_key: str, text: str) -> dict:
        result = self.memory.remember(user_key, KIND_PROGRESS, text, reason="复习进度")
        self.db.log_event(user_key, "memory_write", {"kind": KIND_PROGRESS, "text": text})
        return result

    def memory_entries(self, user_key: str) -> list[dict]:
        try:
            return self.memory.entries(user_key)
        except PowerContextError:
            return []

    # ---- 资料 ----
    def ingest_path(self, path, user_key: str) -> dict:
        chunks: list[Chunk] = parse_file(path)
        added = self.store.add_chunks(chunks)
        doc_id = chunks[0].doc_id if chunks else ""
        self.db.save_document(doc_id, path.stem, str(path), added, user_key)
        self.db.log_event(user_key, "ingest", {"title": path.stem, "chunks": added})
        return {"doc_id": doc_id, "title": path.stem, "chunks": added}

    # ---- 问答 ----
    def ask(self, user_key: str, question: str) -> dict:
        started = time.perf_counter()
        retrieval = self.store.search(question)
        memories, memory_error = self.recall(user_key, question)

        prompt = self._qa_prompt(question, retrieval["hits"], memories)
        llm_used = False
        try:
            answer = chat(
                [
                    {"role": "system", "content": SYSTEM_QA},
                    {"role": "user", "content": prompt},
                ]
            )
            llm_used = True
        except (LLMUnavailable, httpx.HTTPError, KeyError) as exc:
            answer = self._fallback_answer(question, retrieval["hits"], exc)

        citations = [
            {
                "index": index + 1,
                "title": hit["meta"].get("title", ""),
                "section": hit["meta"].get("section", ""),
                "page": hit["meta"].get("page", 0),
                "doc_id": hit["meta"].get("doc_id", ""),
                "chunk_id": hit["id"],
                "snippet": hit["text"][:180],
                "score": hit["score"],
            }
            for index, hit in enumerate(retrieval["hits"])
        ]

        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        self.db.log_retrieval(user_key, question, retrieval)
        self.db.log_qa(
            user_key,
            question,
            answer,
            [c["chunk_id"] for c in citations],
            [m["artifact_id"] for m in memories],
            llm_used,
            latency_ms,
        )
        self.db.log_event(user_key, "ask", {"question": question, "hits": len(citations)})

        return {
            "answer": answer,
            "citations": citations,
            "memories": memories,
            "memory_error": memory_error,
            "retrieval": {k: v for k, v in retrieval.items() if k != "hits"},
            "llm_used": llm_used,
            "latency_ms": latency_ms,
            "llm_model": settings.llm_model if llm_used else "模板降级",
        }

    def _qa_prompt(self, question: str, hits: list[dict], memories: list[dict]) -> str:
        material = []
        for index, hit in enumerate(hits, start=1):
            meta = hit["meta"]
            where = " · ".join(
                str(x) for x in [meta.get("title"), meta.get("section"), f"第 {meta.get('page')} 页" if meta.get("page") else ""] if x
            )
            material.append(f"[{index}] {where}\n{hit['text'][:600]}")
        memory_block = "\n".join(f"m{i + 1}. {m['text']}" for i, m in enumerate(memories)) or "（无）"
        material_block = "\n\n".join(material) or "（资料库为空）"
        return (
            f"资料片段：\n{material_block}\n\n"
            f"用户历史记忆：\n{memory_block}\n\n"
            f"用户的问题：{question}"
        )

    def _fallback_answer(self, question: str, hits: list[dict], exc: Exception) -> str:
        if not hits:
            return "资料库里没有检索到相关内容。请先上传笔记，或换个问法。"
        lines = ["（未配置模型，以下是按检索结果整理的依据）", ""]
        for index, hit in enumerate(hits[:3], start=1):
            lines.append(f"[{index}] {hit['citation']}")
            lines.append(hit["text"][:220].replace("\n", " "))
            lines.append("")
        lines.append(f"检索到 {len(hits)} 个相关片段，配置 LLM_API_KEY 后可生成归纳回答。")
        return "\n".join(lines)

    # ---- 复习计划 ----
    def plan(self, user_key: str) -> dict:
        started = time.perf_counter()
        entries = self.memory_entries(user_key)
        weak = [e for e in entries if e.get("kind") == KIND_WEAK]
        progress = [e for e in entries if e.get("kind") == KIND_PROGRESS]

        references = self.store.search("薄弱点 复习 重点", top_k=4) if weak else None
        snippets = ""
        if references:
            snippets = "\n\n".join(
                f"[{i + 1}] {h['citation']}\n{h['text'][:300]}" for i, h in enumerate(references["hits"])
            )

        weak_block = "\n".join(
            f"m{i + 1}. {e['text']}" for i, e in enumerate(weak)
        ) or "（还没有记录薄弱点）"
        progress_block = "\n".join(e["text"] for e in progress[-5:]) or "（无）"

        llm_used = False
        try:
            plan_text = chat(
                [
                    {"role": "system", "content": SYSTEM_PLAN},
                    {
                        "role": "user",
                        "content": (
                            f"薄弱点记忆：\n{weak_block}\n\n"
                            f"最近进度：\n{progress_block}\n\n"
                            f"可参考的资料片段：\n{snippets or '（无）'}"
                        ),
                    },
                ]
            )
            llm_used = True
        except (LLMUnavailable, httpx.HTTPError, KeyError):
            plan_text = self._fallback_plan(weak, progress)

        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        self.db.log_event(user_key, "plan", {"weak_points": len(weak)})
        return {
            "plan": plan_text,
            "weak_points": [
                {"text": e["text"], "artifact_id": e["artifact"]["artifact_id"], "revision": e["artifact"]["revision"]}
                for e in weak
            ],
            "progress": [e["text"] for e in progress[-5:]],
            "memory_total": len(entries),
            "llm_used": llm_used,
            "latency_ms": latency_ms,
        }

    def _fallback_plan(self, weak: list[dict], progress: list[dict]) -> str:
        if not weak:
            return "还没有记录薄弱点。先在问答里标记「没懂」，下次就能生成计划。"
        lines = ["按记录顺序安排复习：", ""]
        for index, item in enumerate(weak[:5], start=1):
            lines.append(f"{index}. {item['text']}")
        lines.append("")
        lines.append(f"（未配置模型：当前有 {len(weak)} 条薄弱点、{len(progress)} 条进度记忆）")
        return "\n".join(lines)
