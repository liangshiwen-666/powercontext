"""Offline tests for ``sddctx.minimax_client`` (no network, no real key)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
import requests

from sddctx import minimax_client as mc
from sddctx.config import MiniMaxConfig
from sddctx.minimax_client import (
    MiniMaxClient,
    MiniMaxError,
    MiniMaxNotConfigured,
    build_edit_messages,
    build_patch_messages,
    extract_file_writes,
)

FAKE_KEY = "sk-test-PLACEHOLDER-not-a-real-key"
DIFF = (
    "```diff\n"
    "--- a/app/x.py\n"
    "+++ b/app/x.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def add(a, b):\n"
    "-    return a - b\n"
    "+    return a + b\n"
    "```"
)


class FakeResponse:
    def __init__(self, status_code=200, body=None, headers=None, text=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {"Trace-Id": "trace-test-001", "alb_request_id": "alb-test-001"}
        self.text = text if text is not None else json.dumps(body or {}, ensure_ascii=False)

    def json(self):
        if self._body is None:
            raise ValueError("no json body")
        return self._body


def success_body(content: str = DIFF, *, usage=None, model="MiniMax-M3.1-Flash") -> dict:
    return {
        "id": "066b36619b147e326d17053cccdef70f",
        "choices": [
            {
                "finish_reason": "stop",
                "index": 0,
                "message": {
                    "content": content,
                    "reasoning_content": "thinking about the patch",
                    "role": "assistant",
                    "name": "MiniMax AI",
                    "audio_content": "",
                },
            }
        ],
        "created": 1793100000,
        "model": model,
        "object": "chat.completion",
        "usage": usage
        or {
            "total_tokens": 500,
            "prompt_tokens": 400,
            "completion_tokens": 100,
            "prompt_tokens_details": {"cached_tokens": 256},
        },
        "input_sensitive": False,
        "output_sensitive": False,
        "input_sensitive_type": 0,
        "output_sensitive_type": 0,
        "output_sensitive_int": 0,
        "base_resp": {"status_code": 0, "status_msg": ""},
    }


def error_body(http_code: int, message: str, request_id: str = "req-test-401") -> dict:
    return {
        "type": "error",
        "error": {"type": "authorized_error", "message": message, "http_code": str(http_code)},
        "request_id": request_id,
    }


def make_post(responses, calls):
    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if not responses:
            raise AssertionError("fake requests.post called more times than expected")
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    return fake_post


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Retries must not actually sleep in tests."""

    delays: list[float] = []
    monkeypatch.setattr(mc, "_sleep", lambda seconds: delays.append(seconds))
    return delays


def make_config(**overrides) -> MiniMaxConfig:
    values = dict(
        api_key=FAKE_KEY,
        base_url="https://api.minimax.cn/v1",
        model="MiniMax-M3.1-Flash",
        reasoning_effort="high",
        max_completion_tokens=4096,
        timeout_s=60,
    )
    values.update(overrides)
    return MiniMaxConfig(**values)


def make_client(tmp_path: Path, cfg: MiniMaxConfig | None = None) -> MiniMaxClient:
    return MiniMaxClient.from_config(cfg or make_config(), tmp_path / "records")


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------
def test_configured_property(tmp_path: Path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(mc.requests, "post", make_post([], calls))

    assert make_client(tmp_path).configured is True
    unconfigured = make_client(tmp_path, make_config(api_key="   "))
    assert unconfigured.configured is False
    with pytest.raises(MiniMaxNotConfigured):
        unconfigured.chat([{"role": "user", "content": "hi"}], tag="unit")
    assert calls == []


# --------------------------------------------------------------------------
# happy path: record + index + sha256
# --------------------------------------------------------------------------
def test_chat_writes_record_and_index(tmp_path: Path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(mc.requests, "post", make_post([FakeResponse(200, success_body())], calls))
    client = make_client(tmp_path)
    messages = [
        {"role": "system", "content": "you output one diff"},
        {"role": "user", "content": "TASK: fix add()\nCONTEXT: ..."},
    ]

    result = client.chat(messages, tag="patch/orders-filters", meta={"task_id": "orders-filters-v2"})

    assert set(result) >= {
        "content",
        "record_path",
        "usage",
        "model",
        "trace_id",
        "elapsed_ms",
        "finish_reason",
    }
    assert "+    return a + b" in result["content"]
    assert result["usage"]["prompt_tokens"] == 400
    assert result["model"] == "MiniMax-M3.1-Flash"
    assert result["trace_id"] == "trace-test-001"
    assert result["finish_reason"] == "stop"
    assert isinstance(result["elapsed_ms"], int) and result["elapsed_ms"] >= 0
    assert result["response_sha256"] == hashlib.sha256(result["content"].encode("utf-8")).hexdigest()
    assert client.describe() == "MiniMax MiniMax-M3.1-Flash @ https://api.minimax.cn/v1"

    record_path = Path(result["record_path"])
    assert record_path.is_file()
    assert record_path.parent == client.record_dir
    assert record_path.suffix == ".json"
    name = record_path.name
    assert "patch-orders-filters" in name and "/" not in name and ".." not in name
    assert re.match(r"^\d{8}T\d{12}Z-.+\.json$", name), name

    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["schema"] == mc.RECORD_SCHEMA
    assert record["outcome"] == "success"
    assert record["endpoint"]["base_url"] == "https://api.minimax.cn/v1"
    assert record["endpoint"]["path"] == "/chat/completions"
    assert record["model"] == "MiniMax-M3.1-Flash"
    assert record["parameters"]["max_completion_tokens"] == 4096
    assert record["parameters"]["reasoning_effort"] == "high"
    assert record["request"]["messages"] == messages
    expected_messages_sha = hashlib.sha256(
        json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert record["request"]["messages_sha256"] == expected_messages_sha
    assert record["http_status"] == 200
    assert record["response"]["body"]["id"] == "066b36619b147e326d17053cccdef70f"
    assert record["response"]["content_sha256"] == hashlib.sha256(result["content"].encode("utf-8")).hexdigest()
    assert record["response"]["content_sha256"] == result["response_sha256"]
    assert record["response"]["usage"]["completion_tokens"] == 100
    assert record["response"]["trace_id"] == "trace-test-001"
    assert record["response"]["finish_reason"] == "stop"
    assert record["meta"] == {"task_id": "orders-filters-v2"}
    assert len(record["chain"]["record_sha256"]) == 64
    assert record["chain"]["prev_record_sha256"] is None
    assert record["attempts"][-1]["http_status"] == 200

    # key never persisted; only its hash
    raw_text = record_path.read_text(encoding="utf-8")
    assert FAKE_KEY not in raw_text
    assert "Bearer " + FAKE_KEY not in raw_text
    assert record["auth"]["api_key_sha256"] == hashlib.sha256(FAKE_KEY.encode("utf-8")).hexdigest()
    assert record["auth"]["api_key_present"] is True
    assert record["request"]["headers_redacted"]["Authorization"] == "***REDACTED***"

    # index.jsonl appended and references the record with a *portable* path
    index_path = client.record_dir / "index.jsonl"
    lines = [line for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    index_entry = json.loads(lines[0])
    assert index_entry["record_path"] == f"records/{record_path.name}"
    assert not Path(index_entry["record_path"]).is_absolute()
    assert ":" not in index_entry["record_path"].split("/")[0]
    assert index_entry["record_sha256"] == record["chain"]["record_sha256"]
    assert index_entry["prev_record_sha256"] is None
    assert index_entry["content_sha256"] == record["response"]["content_sha256"]
    assert FAKE_KEY not in index_path.read_text(encoding="utf-8")

    # the request really went to the configured OpenAI-compatible path
    assert calls[0]["url"] == "https://api.minimax.cn/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer " + FAKE_KEY
    assert calls[0]["json"]["model"] == "MiniMax-M3.1-Flash"
    assert calls[0]["json"]["stream"] is False
    assert calls[0]["json"]["reasoning_effort"] == "high"
    assert "temperature" not in calls[0]["json"]


def test_record_sha256_chain_across_calls(tmp_path: Path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        mc.requests,
        "post",
        make_post([FakeResponse(200, success_body()), FakeResponse(200, success_body())], calls),
    )
    client = make_client(tmp_path)
    first = client.chat([{"role": "user", "content": "one"}], tag="first")
    second = client.chat([{"role": "user", "content": "two"}], tag="second")

    first_record = json.loads(Path(first["record_path"]).read_text(encoding="utf-8"))
    second_record = json.loads(Path(second["record_path"]).read_text(encoding="utf-8"))
    assert second_record["chain"]["prev_record_sha256"] == first_record["chain"]["record_sha256"]
    assert first_record["chain"]["record_sha256"] != second_record["chain"]["record_sha256"]

    # The chain hash must be recomputable from the record itself (tamper-evident).
    for record in (first_record, second_record):
        clone = json.loads(json.dumps(record))
        stored = clone["chain"].pop("record_sha256")
        canonical = json.dumps(clone, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        assert stored == hashlib.sha256(canonical).hexdigest()


def test_index_record_path_is_relative_to_minimax_root(tmp_path: Path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(mc.requests, "post", make_post([FakeResponse(200, success_body())], calls))
    record_dir = tmp_path / "minimax" / "records" / "minimax"
    client = MiniMaxClient.from_config(make_config(), record_dir)

    result = client.chat([{"role": "user", "content": "hi"}], tag="portable")

    index_entry = json.loads((record_dir / "index.jsonl").read_text(encoding="utf-8").splitlines()[0])
    relative = index_entry["record_path"]
    assert relative == f"records/minimax/{Path(result['record_path']).name}"
    assert not relative.startswith("/")
    assert ".." not in relative.split("/")
    # Resolving the portable path against the minimax root must find the record.
    assert (tmp_path / "minimax" / relative).is_file()


# --------------------------------------------------------------------------
# retries
# --------------------------------------------------------------------------
def test_429_is_retried_then_succeeds(tmp_path: Path, monkeypatch, _no_sleep):
    calls: list = []
    responses = [
        FakeResponse(429, {"error": {"type": "rate_limit_error", "message": "rate limited"}, "request_id": "req-429"}),
        FakeResponse(200, success_body()),
    ]
    monkeypatch.setattr(mc.requests, "post", make_post(responses, calls))
    client = make_client(tmp_path)

    result = client.chat([{"role": "user", "content": "hi"}], tag="retry")

    assert result["content"]
    assert len(calls) == 2, "429 must trigger exactly one retry"
    record = json.loads(Path(result["record_path"]).read_text(encoding="utf-8"))
    attempt_429, attempt_200 = record["attempts"]
    assert attempt_429["http_status"] == 429
    assert attempt_429["retryable"] is True
    assert attempt_429["request_id"] == "req-429"
    assert attempt_200["http_status"] == 200
    # back-off was requested between the two attempts
    assert len(_no_sleep) == 1
    assert _no_sleep[0] > 0


def test_timeout_is_retried(tmp_path: Path, monkeypatch, _no_sleep):
    calls: list = []
    responses = [requests.Timeout("simulated timeout"), FakeResponse(200, success_body())]
    monkeypatch.setattr(mc.requests, "post", make_post(responses, calls))
    client = make_client(tmp_path)

    result = client.chat([{"role": "user", "content": "hi"}], tag="timeout-retry")

    assert result["content"]
    assert len(calls) == 2
    record = json.loads(Path(result["record_path"]).read_text(encoding="utf-8"))
    assert record["attempts"][0]["error_type"] == "transport_error"
    assert record["attempts"][0]["retryable"] is True
    assert len(_no_sleep) == 1


def test_retries_exhausted_writes_failure_record(tmp_path: Path, monkeypatch, _no_sleep):
    calls: list = []
    responses = [FakeResponse(500, {"error": {"type": "api_error", "message": "boom"}}) for _ in range(mc.MAX_ATTEMPTS)]
    monkeypatch.setattr(mc.requests, "post", make_post(responses, calls))
    client = make_client(tmp_path)

    with pytest.raises(MiniMaxError) as excinfo:
        client.chat([{"role": "user", "content": "hi"}], tag="exhausted")
    error = excinfo.value

    assert error.retryable is True
    assert len(calls) == mc.MAX_ATTEMPTS
    assert error.record_path is not None
    record = json.loads(Path(error.record_path).read_text(encoding="utf-8"))
    assert record["outcome"] == "error"
    assert len(record["attempts"]) == mc.MAX_ATTEMPTS
    assert all(attempt["http_status"] == 500 for attempt in record["attempts"])
    assert len(_no_sleep) == mc.MAX_ATTEMPTS - 1


def test_500_is_retried(tmp_path: Path, monkeypatch):
    calls: list = []
    responses = [
        FakeResponse(500, {"error": {"type": "api_error", "message": "boom"}}),
        FakeResponse(200, success_body()),
    ]
    monkeypatch.setattr(mc.requests, "post", make_post(responses, calls))
    client = make_client(tmp_path)

    result = client.chat([{"role": "user", "content": "hi"}], tag="retry-500")

    assert result["content"]
    assert len(calls) == 2


def test_401_is_not_retried_and_still_recorded(tmp_path: Path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        mc.requests,
        "post",
        make_post([FakeResponse(401, error_body(401, "invalid api key"))], calls),
    )
    client = make_client(tmp_path)

    with pytest.raises(MiniMaxError) as excinfo:
        client.chat([{"role": "user", "content": "hi"}], tag="auth")
    error = excinfo.value

    assert error.retryable is False
    assert error.http_status == 401
    assert error.request_id == "req-test-401"
    assert error.trace_id == "trace-test-001"
    assert len(calls) == 1, "401 must not be retried"

    assert error.record_path is not None
    record = json.loads(Path(error.record_path).read_text(encoding="utf-8"))
    assert record["outcome"] == "error"
    assert record["http_status"] == 401
    assert record["response"]["body"]["error"]["message"] == "invalid api key"
    raw_text = Path(error.record_path).read_text(encoding="utf-8")
    assert FAKE_KEY not in raw_text


# --------------------------------------------------------------------------
# prompt helper
# --------------------------------------------------------------------------
def test_build_patch_messages_contains_contract_and_citations():
    with pytest.warns(DeprecationWarning):
        messages = build_patch_messages(
            task_text="为 GET /orders 增加 status 过滤（REQ-ORD-006）",
            context_text="--- demo/app/service.py:89-140 ---\ndef list_orders(...): ...",
            context_meta=[
                {
                    "path": "demo/app/service.py",
                    "line_start": 89,
                    "line_end": 140,
                    "symbol": "list_orders",
                    "title": "订单列表过滤",
                    "kind": "code",
                }
            ],
        )

    assert [m["role"] for m in messages] == ["system", "user"]
    system, user = messages[0]["content"], messages[1]["content"]
    assert "```diff" in system
    assert "tests/" in system, "system prompt must forbid touching tests/"
    assert "tests/" in user
    assert "不要修改" in user
    assert "为 GET /orders 增加 status 过滤（REQ-ORD-006）" in user
    assert "demo/app/service.py:89-140" in user
    assert "list_orders" in user


def test_build_patch_messages_without_meta_still_works():
    with pytest.warns(DeprecationWarning):
        messages = build_patch_messages("task", "context only", None)
    assert len(messages) == 2
    assert "context only" in messages[1]["content"]
    assert "未附带 citation" in messages[1]["content"]


def test_build_patch_messages_accepts_cli_meta_shape():
    """``cli.py`` passes ``{"task": {...}, "context_sha256": "..."}``."""

    with pytest.warns(DeprecationWarning):
        messages = build_patch_messages(
            "task text",
            "context with demo/app/service.py:1-10",
            {"task": {"id": "orders-filters-v2", "title": "订单筛选"}, "context_sha256": "abc123"},
        )
    user = messages[1]["content"]
    assert "orders-filters-v2" in user
    assert "context_sha256: abc123" in user
    assert "<unknown>" not in user


# --------------------------------------------------------------------------
# boundary behaviour (V1 audit item 4)
# --------------------------------------------------------------------------
def test_response_without_content_is_recorded(tmp_path: Path, monkeypatch):
    calls: list = []
    body = success_body(content="")
    # Mimic a gateway response where the message has no content key at all.
    del body["choices"][0]["message"]["content"]
    monkeypatch.setattr(mc.requests, "post", make_post([FakeResponse(200, body)], calls))
    client = make_client(tmp_path)

    result = client.chat([{"role": "user", "content": "hi"}], tag="empty-content")

    assert result["content"] == ""
    assert result["finish_reason"] == "stop"
    record = json.loads(Path(result["record_path"]).read_text(encoding="utf-8"))
    assert record["outcome"] == "success"
    assert record["response"]["content_sha256"] == hashlib.sha256(b"").hexdigest()


def test_base_resp_error_with_http_200_is_an_error(tmp_path: Path, monkeypatch):
    calls: list = []
    body = success_body()
    body["base_resp"] = {"status_code": 2013, "status_msg": "invalid parameters"}
    monkeypatch.setattr(mc.requests, "post", make_post([FakeResponse(200, body)], calls))
    client = make_client(tmp_path)

    with pytest.raises(MiniMaxError) as excinfo:
        client.chat([{"role": "user", "content": "hi"}], tag="base-resp-error")

    error = excinfo.value
    assert error.retryable is False
    assert error.base_status_code == 2013
    assert len(calls) == 1
    record = json.loads(Path(error.record_path).read_text(encoding="utf-8"))
    assert record["outcome"] == "error"
    assert record["http_status"] == 200
    assert record["response"]["body"]["base_resp"]["status_code"] == 2013


def test_string_base_resp_zero_is_not_an_error(tmp_path: Path, monkeypatch):
    calls: list = []
    body = success_body()
    body["base_resp"] = {"status_code": "0", "status_msg": ""}
    monkeypatch.setattr(mc.requests, "post", make_post([FakeResponse(200, body)], calls))
    client = make_client(tmp_path)

    result = client.chat([{"role": "user", "content": "hi"}], tag="string-zero")
    assert result["content"]
    assert len(calls) == 1


def test_retryable_base_resp_error_is_retried(tmp_path: Path, monkeypatch, _no_sleep):
    calls: list = []
    body = success_body()
    body["base_resp"] = {"status_code": 1002, "status_msg": "rate limited"}
    responses = [FakeResponse(200, body), FakeResponse(200, success_body())]
    monkeypatch.setattr(mc.requests, "post", make_post(responses, calls))
    client = make_client(tmp_path)

    result = client.chat([{"role": "user", "content": "hi"}], tag="retry-base-resp")

    assert result["content"]
    assert len(calls) == 2
    record = json.loads(Path(result["record_path"]).read_text(encoding="utf-8"))
    assert record["attempts"][0]["base_status_code"] == 1002
    assert record["attempts"][0]["retryable"] is True


def test_very_long_response_content_is_recorded(tmp_path: Path, monkeypatch):
    calls: list = []
    long_content = "x" * 200_000
    monkeypatch.setattr(
        mc.requests, "post", make_post([FakeResponse(200, success_body(content=long_content))], calls)
    )
    client = make_client(tmp_path)

    result = client.chat([{"role": "user", "content": "hi"}], tag="long-response")

    assert len(result["content"]) == 200_000
    record = json.loads(Path(result["record_path"]).read_text(encoding="utf-8"))
    assert record["response"]["content_sha256"] == hashlib.sha256(long_content.encode()).hexdigest()
    assert len(record["response"]["content"]) == 200_000


# --------------------------------------------------------------------------
# whole-file edit helpers (the live path; legacy diff helper is deprecated)
# --------------------------------------------------------------------------
def test_build_edit_messages_asks_for_whole_files():
    messages = build_edit_messages(
        "为订单接口增加筛选", "CONTEXT demo/app/service.py:1-10", {"path": "demo/app/service.py"}
    )
    assert [m["role"] for m in messages] == ["system", "user"]
    system, user = messages[0]["content"], messages[1]["content"]
    assert "=== FILE: app/service.py ===" in system
    assert "完整" in system
    assert "tests/" in system
    assert "为订单接口增加筛选" in user


def test_extract_file_writes_parses_sections():
    text = (
        "前言\n"
        "=== FILE: app/service.py ===\n"
        "```python\n"
        "def f():\n"
        "    return 1\n"
        "```\n"
        "=== FILE: app/routes.py ===\n"
        "```python\n"
        "ROUTES = []\n"
        "```\n"
    )
    writes = extract_file_writes(text)
    assert writes == {
        "app/service.py": "def f():\n    return 1\n",
        "app/routes.py": "ROUTES = []\n",
    }
    assert extract_file_writes("没有 FILE 段") == {}
