"""OpenAI 兼容的对话接口封装。没有配置 Key 时给出明确的可降级信号。"""

from __future__ import annotations

import httpx

from .config import settings


class LLMUnavailable(RuntimeError):
    """没有可用的模型凭据，调用方应降级为模板回答。"""


def chat(messages: list[dict], temperature: float = 0.2, timeout: float = 90.0) -> str:
    if not settings.llm_enabled:
        raise LLMUnavailable("未配置 LLM_API_KEY / LLM_MODEL")

    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    return data["choices"][0]["message"]["content"].strip()
