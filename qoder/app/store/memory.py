"""PowerContext 客户端：按用户划分 Scope，负责记忆写入与跨会话召回。"""

from __future__ import annotations

from typing import Any

import httpx

from ..config import settings


class PowerContextError(RuntimeError):
    pass


class PowerContext:
    def __init__(self, store) -> None:
        self._store = store
        self._base = settings.powercontext_base_url

    def _request(self, method: str, path: str, payload: dict | None = None) -> Any:
        url = f"{self._base}{path}"
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.request(method, url, json=payload)
        except httpx.HTTPError as exc:
            raise PowerContextError(f"无法连接 PowerContext（{self._base}）：{exc}") from exc
        if resp.status_code >= 400:
            raise PowerContextError(f"{method} {path} 失败 {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def health(self) -> dict:
        info = self._request("GET", "/v1/server-info")
        return {
            "ok": True,
            "product": info.get("product"),
            "version": info.get("package_version"),
            "contracts": sorted((info.get("feature_contracts") or {}).keys()),
        }

    def _scope_title(self, user_key: str) -> str:
        return f"{settings.scope_prefix}:{user_key}"

    def scope_id(self, user_key: str) -> str:
        cached = self._store.get_scope(user_key)
        if cached:
            return cached

        title = self._scope_title(user_key)
        listing = self._request("GET", "/v1/scopes")
        for item in listing.get("items", []):
            if item.get("title") == title:
                self._store.save_scope(user_key, item["scope_id"])
                return item["scope_id"]

        created = self._request(
            "POST",
            "/v1/scopes",
            {
                "title": title,
                "summary": f"复习教练的个人上下文：{user_key}",
                "idempotency_key": f"{settings.scope_prefix}-{user_key}",
            },
        )
        scope_id = created["scope_id"]
        self._store.save_scope(user_key, scope_id)
        return scope_id

    def remember(self, user_key: str, kind: str, text: str, reason: str | None = None) -> dict:
        payload = {"scope_id": self.scope_id(user_key), "kind": kind, "text": text}
        if reason:
            payload["reason"] = reason
        return self._request("POST", "/v1/memory/remember", payload)

    def search(self, user_key: str, query: str, limit: int = 8) -> dict:
        return self._request(
            "POST",
            "/v1/memory/search",
            {"scope_id": self.scope_id(user_key), "query": query, "limit": limit},
        )

    def entries(self, user_key: str) -> list[dict]:
        data = self._request("POST", "/v1/memory/entries/list", {"scope_id": self.scope_id(user_key)})
        return data.get("entries", [])

    def prepare(self, user_key: str, query: str) -> dict:
        try:
            return self._request(
                "POST",
                "/v1/context/prepare",
                {"scope_id": self.scope_id(user_key), "query": query, "explain": True},
            )
        except PowerContextError as exc:
            return {"status": "error", "reason": str(exc)}
