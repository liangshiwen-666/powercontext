"""向量化策略：优先用 OpenAI 兼容接口，退回 pyseekdb 内置本地模型。"""

from __future__ import annotations

from typing import Any

import httpx
from pyseekdb import DefaultEmbeddingFunction, EmbeddingFunction

from .config import settings

LOCAL_MODEL = "all-MiniLM-L6-v2"


class ApiEmbeddingFunction(EmbeddingFunction):
    """OpenAI 兼容的 /embeddings 接口，中文语义检索效果明显更好。"""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._dimension: int | None = None

    @staticmethod
    def name() -> str:
        return "study-coach-api"

    def get_config(self) -> dict[str, Any]:
        # 不落盘凭据：还原时从环境变量重新读取
        return {"base_url": self._base_url, "model": self._model}

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "ApiEmbeddingFunction":
        return ApiEmbeddingFunction(
            base_url=config.get("base_url") or settings.embedding_base_url or settings.llm_base_url,
            api_key=settings.embedding_api_key,
            model=config.get("model") or settings.embedding_model,
        )

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            self._dimension = len(self(["维度探测"])[0])
        return self._dimension

    def __call__(self, input):  # noqa: A002 - 与 pyseekdb 协议保持一致
        texts = [input] if isinstance(input, str) else list(input)
        if not texts:
            return []
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{self._base_url}/embeddings",
                json={"model": self._model, "input": texts},
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            resp.raise_for_status()
            items = sorted(resp.json()["data"], key=lambda x: x.get("index", 0))
        return [item["embedding"] for item in items]


def build_embedding_function() -> EmbeddingFunction:
    if settings.use_api_embedding:
        return ApiEmbeddingFunction(
            base_url=settings.embedding_base_url or settings.llm_base_url,
            api_key=settings.embedding_api_key,
            model=settings.embedding_model,
        )
    return DefaultEmbeddingFunction(model_name=LOCAL_MODEL)


def embedding_label(ef: EmbeddingFunction) -> str:
    if isinstance(ef, DefaultEmbeddingFunction):
        return f"local:{LOCAL_MODEL}"
    return f"api:{getattr(ef, '_model', type(ef).__name__)}"
