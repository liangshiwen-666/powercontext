"""FastAPI 入口：资料入库、带引用问答、记忆写入、复习计划、证据面板。"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import settings
from .store.db import AppDB
from .store.memory import PowerContextError
from .store.seek import SeekStore
from .tutor import Tutor

app = FastAPI(title="知返 · 复习教练", version="0.1.0")

DB = AppDB(settings.data_dir / "app.db")
_state: dict = {"store": None, "tutor": None, "error": None}


def get_tutor() -> Tutor:
    if _state["tutor"] is None:
        if _state["error"]:
            raise HTTPException(status_code=503, detail=_state["error"])
        try:
            store = SeekStore()
        except Exception as exc:  # seekdb 不可用时给出可读的错误
            _state["error"] = f"seekdb 初始化失败：{exc}"
            raise HTTPException(status_code=503, detail=_state["error"]) from exc
        _state["store"] = store
        _state["tutor"] = Tutor(store, DB)
    return _state["tutor"]


class AskRequest(BaseModel):
    user_key: str = "demo"
    question: str


class FlagRequest(BaseModel):
    user_key: str = "demo"
    concept: str
    detail: str = ""
    citation: str = ""


class ProgressRequest(BaseModel):
    user_key: str = "demo"
    text: str


@app.get("/api/health")
def health() -> dict:
    info: dict = {
        "llm": settings.llm_enabled,
        "llm_model": settings.llm_model or None,
        "embedding_mode": "api" if settings.use_api_embedding else "local",
        "seekdb_mode": settings.seekdb_mode,
        "powercontext": settings.powercontext_base_url,
    }
    try:
        tutor = get_tutor()
        info["seekdb"] = {
            "ok": True,
            "collection": tutor.store.collection_name,
            "dimension": tutor.store.dimension,
            "embedding": tutor.store.embedding_name,
            "chunks": tutor.store.count(),
        }
    except HTTPException as exc:
        info["seekdb"] = {"ok": False, "error": exc.detail}
    try:
        info["powercontext"] = get_tutor().memory.health()
    except (HTTPException, PowerContextError) as exc:
        info["powercontext"] = {"ok": False, "error": str(exc)}
    return info


@app.post("/api/ingest")
async def ingest(file: UploadFile = File(...), user_key: str = Form("demo")) -> dict:
    upload_dir = settings.data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    name = Path(file.filename or "notes.md").name
    target = upload_dir / name
    with target.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)
    return get_tutor().ingest_path(target, user_key)


@app.post("/api/seed")
def seed(user_key: str = Form("demo")) -> dict:
    files = sorted(settings.sample_dir.glob("*"))
    if not files:
        raise HTTPException(status_code=404, detail="没有样例资料")
    results = [get_tutor().ingest_path(path, user_key) for path in files]
    return {"ingested": results, "files": len(results)}


@app.get("/api/documents")
def documents() -> dict:
    return {"documents": DB.documents()}


@app.post("/api/ask")
def ask(payload: AskRequest) -> dict:
    if not payload.question.strip():
        raise HTTPException(status_code=400, detail="问题不能为空")
    return get_tutor().ask(payload.user_key.strip() or "demo", payload.question.strip())


@app.post("/api/flag")
def flag(payload: FlagRequest) -> dict:
    if not payload.concept.strip():
        raise HTTPException(status_code=400, detail="请填写没懂的概念")
    return get_tutor().remember_weak_point(
        payload.user_key.strip() or "demo",
        payload.concept.strip(),
        payload.detail.strip(),
        payload.citation.strip(),
    )


@app.post("/api/progress")
def progress(payload: ProgressRequest) -> dict:
    if not payload.text.strip():
        raise HTTPException(status_code=400, detail="进度内容不能为空")
    return get_tutor().record_progress(payload.user_key.strip() or "demo", payload.text.strip())


@app.get("/api/plan")
def plan(user_key: str = "demo") -> dict:
    return get_tutor().plan(user_key.strip() or "demo")


@app.get("/api/memories")
def memories(user_key: str = "demo") -> dict:
    entries = get_tutor().memory_entries(user_key.strip() or "demo")
    return {"entries": entries, "count": len(entries)}


@app.get("/api/evidence")
def evidence(limit: int = 10) -> dict:
    return {
        "retrievals": DB.recent_retrievals(limit=limit),
        "stats": DB.stats(),
    }


@app.get("/api/stats")
def stats() -> dict:
    return DB.stats()


@app.get("/api/export/events.csv")
def export_events() -> PlainTextResponse:
    return PlainTextResponse(
        DB.export_events_csv(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=events.csv"},
    )


@app.get("/api/powercontext/prepare")
def prepare(user_key: str = "demo", query: str = "今天复习什么") -> dict:
    """可选能力：配置模型后 PowerContext 才会返回非空上下文，这里如实展示。"""
    return get_tutor().memory.prepare(user_key, query)


STATIC = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")


@app.middleware("http")
async def add_timing(request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Elapsed-Ms"] = str(round((time.perf_counter() - started) * 1000, 1))
    return response
