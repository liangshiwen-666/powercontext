"""应用侧轻量记录库（SQLite）：用户↔Scope 映射、资料台账、检索与使用日志。

资料片段与向量本身存在 seekdb；这里只放关系型的运营数据，便于统计与导出。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_scope (
    user_key TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    chunk_count INTEGER NOT NULL,
    user_key TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS retrieval_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key TEXT NOT NULL,
    question TEXT NOT NULL,
    keywords TEXT NOT NULL,
    hit_ids TEXT NOT NULL,
    channels TEXT NOT NULL,
    top_score REAL NOT NULL,
    latency_ms REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS qa_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    citations TEXT NOT NULL,
    memory_used TEXT NOT NULL,
    llm_used INTEGER NOT NULL,
    latency_ms REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class AppDB:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ---- 用户 ↔ Scope ----
    def get_scope(self, user_key: str) -> str | None:
        row = self._conn.execute(
            "SELECT scope_id FROM user_scope WHERE user_key = ?", (user_key,)
        ).fetchone()
        return row["scope_id"] if row else None

    def save_scope(self, user_key: str, scope_id: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO user_scope (user_key, scope_id, created_at) VALUES (?, ?, ?)",
            (user_key, scope_id, _now()),
        )
        self._conn.commit()

    # ---- 资料台账 ----
    def save_document(self, doc_id: str, title: str, source: str, chunk_count: int, user_key: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO documents (doc_id, title, source, chunk_count, user_key, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (doc_id, title, source, chunk_count, user_key, _now()),
        )
        self._conn.commit()

    def documents(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT doc_id, title, source, chunk_count, user_key, created_at FROM documents ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- 日志 ----
    def log_retrieval(self, user_key: str, question: str, result: dict) -> None:
        self._conn.execute(
            "INSERT INTO retrieval_log (user_key, question, keywords, hit_ids, channels, top_score, latency_ms, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_key,
                question,
                json.dumps(result["keywords"], ensure_ascii=False),
                json.dumps([h["id"] for h in result["hits"]], ensure_ascii=False),
                json.dumps(result["channels"], ensure_ascii=False),
                result["hits"][0]["score"] if result["hits"] else 0.0,
                result["latency_ms"],
                _now(),
            ),
        )
        self._conn.commit()

    def log_qa(
        self,
        user_key: str,
        question: str,
        answer: str,
        citations: list[str],
        memory_used: list[str],
        llm_used: bool,
        latency_ms: float,
    ) -> None:
        self._conn.execute(
            "INSERT INTO qa_log (user_key, question, answer, citations, memory_used, llm_used, latency_ms, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_key,
                question,
                answer[:4000],
                json.dumps(citations, ensure_ascii=False),
                json.dumps(memory_used, ensure_ascii=False),
                int(llm_used),
                latency_ms,
                _now(),
            ),
        )
        self._conn.commit()

    def log_event(self, user_key: str, kind: str, payload: dict[str, Any] | None = None) -> None:
        self._conn.execute(
            "INSERT INTO event_log (user_key, kind, payload, created_at) VALUES (?, ?, ?, ?)",
            (user_key, kind, json.dumps(payload or {}, ensure_ascii=False), _now()),
        )
        self._conn.commit()

    def recent_retrievals(self, limit: int = 10) -> list[dict]:
        rows = self._conn.execute(
            "SELECT user_key, question, keywords, hit_ids, top_score, latency_ms, created_at"
            " FROM retrieval_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["keywords"] = json.loads(item["keywords"])
            item["hit_ids"] = json.loads(item["hit_ids"])
            out.append(item)
        return out

    def stats(self) -> dict:
        def scalar(sql: str, params: tuple = ()) -> Any:
            row = self._conn.execute(sql, params).fetchone()
            return row[0] if row else 0

        week_ago = (datetime.now(timezone.utc).astimezone() - timedelta(days=7)).isoformat(timespec="seconds")
        return {
            "questions": scalar("SELECT COUNT(*) FROM qa_log"),
            "retrievals": scalar("SELECT COUNT(*) FROM retrieval_log"),
            "memories_written": scalar("SELECT COUNT(*) FROM event_log WHERE kind = 'memory_write'"),
            "active_users": scalar("SELECT COUNT(DISTINCT user_key) FROM event_log"),
            "documents": scalar("SELECT COUNT(*) FROM documents"),
            "chunks": scalar("SELECT COALESCE(SUM(chunk_count), 0) FROM documents"),
            "avg_retrieval_ms": round(scalar("SELECT COALESCE(AVG(latency_ms), 0) FROM retrieval_log"), 1),
            "avg_answer_ms": round(scalar("SELECT COALESCE(AVG(latency_ms), 0) FROM qa_log"), 1),
            "week_questions": scalar("SELECT COUNT(*) FROM qa_log WHERE created_at >= ?", (week_ago,)),
            "first_use": scalar("SELECT COALESCE(MIN(created_at), '') FROM event_log"),
        }

    def export_events_csv(self) -> str:
        rows = self._conn.execute(
            "SELECT created_at, user_key, kind, payload FROM event_log ORDER BY id"
        ).fetchall()
        lines = ["created_at,user_key,kind,payload"]
        for row in rows:
            payload = row["payload"].replace('"', '""')
            lines.append(f'{row["created_at"]},{row["user_key"]},{row["kind"]},"{payload}"')
        return "\n".join(lines)
