"""MiniMax chat-completion client with auditable development records.

Design goals
------------
* One *live* API call == one JSON record on disk (plus one line in
  ``index.jsonl``), so a reviewer can trace
  ``task -> context/messages -> MiniMax response -> final patch``.
* The API key is **never** written to disk.  Only ``sha256(api_key)`` and a
  redacted header are persisted.
* Every record carries a ``sha256`` chain (``prev_record_sha256`` ->
  ``record_sha256``) so records cannot be silently re-ordered or edited.
* Retry policy is deliberate: 429 / 5xx / timeouts are retried with
  exponential back-off; 4xx (including 401) is *not* retried.

The public surface is intentionally small because other toolkit modules
(CLI, patch workflow) only need :class:`MiniMaxClient` and
:func:`build_edit_messages` (whole-file edits).  :func:`build_patch_messages`
is deprecated — it is kept as an import-compatible alias for
``sddctx/cli.py`` and slated for removal in the next interface revision.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import requests

from .config import MiniMaxConfig

RECORD_SCHEMA = "sddctx.minimax.record/v1"
INDEX_SCHEMA = "sddctx.minimax.index/v1"
CHAT_PATH = "/chat/completions"
CONNECT_TIMEOUT_S = 15
MAX_ATTEMPTS = 4
BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 16.0

RETRYABLE_HTTP_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
RETRYABLE_BASE_RESP_CODES = frozenset({1000, 1001, 1002, 1013, 1024, 1033, 2064, 2066})
# Non-retryable codes that still deserve a clear message.
ERROR_HINTS = {
    1004: "authentication failed: check MINIMAX_API_KEY and that the key matches the base_url region",
    1008: "insufficient balance: top up the MiniMax account",
    1039: "token limit exceeded: raise max_completion_tokens or trim the context",
    2013: "invalid parameters: check the request body",
    2022: "model not found or not enabled for this account",
    2069: "model has been retired: switch to an available model",
}

# ``_sleep`` is module level so tests can patch it without slowing down.
_sleep = time.sleep


class MiniMaxError(RuntimeError):
    """Normalised API/transport error (never contains the API key)."""

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        retryable: bool = False,
        response_body: Any = None,
        record_path: str | None = None,
        base_status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.request_id = request_id
        self.trace_id = trace_id
        self.retryable = retryable
        self.response_body = response_body
        self.record_path = record_path
        self.base_status_code = base_status_code


class MiniMaxNotConfigured(MiniMaxError):
    """Raised by :meth:`MiniMaxClient.chat` when no API key is configured."""


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_json(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_text(canonical)


def _utc_stamp() -> str:
    # Compact, Windows-safe UTC stamp: 20261027T134512123456Z
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _sanitize_tag(tag: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(tag or "").strip()).strip("-._")
    return (safe or "call")[:60]


def _header(headers: Mapping[str, str] | None, name: str) -> str | None:
    if not headers:
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
    return None


def _redact_headers(headers: Mapping[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        if str(key).lower() in {"authorization", "x-api-key", "api-key", "proxy-authorization"}:
            redacted[str(key)] = "***REDACTED***"
        else:
            redacted[str(key)] = str(value)
    return redacted


def _json_safe(value: Any) -> Any:
    """Best-effort conversion so ``meta`` can contain Paths etc."""

    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        if isinstance(value, Mapping):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [_json_safe(v) for v in value]
        return str(value)


def _error_from_body(body: Mapping[str, Any] | None) -> tuple[str | None, str | None, int | None]:
    """Return ``(error_type, message, base_resp_code)`` from a MiniMax body."""

    if not isinstance(body, Mapping):
        return None, None, None
    error = body.get("error")
    error_type = message = None
    if isinstance(error, Mapping):
        error_type = str(error.get("type")) if error.get("type") is not None else None
        raw_message = error.get("message") or error.get("msg")
        message = str(raw_message) if raw_message is not None else None
    base_code: int | None = None
    base_resp = body.get("base_resp")
    if isinstance(base_resp, Mapping):
        code = base_resp.get("status_code")
        if code is not None:
            try:
                parsed = int(code)
            except (TypeError, ValueError):
                parsed = 0
            if parsed != 0:
                base_code = parsed
                if message is None:
                    message = f"base_resp.status_code={base_code}: {base_resp.get('status_msg', '')}"
    return error_type, message, base_code


def _truncate(text: str | None, limit: int = 2000) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit] + "…"


# --------------------------------------------------------------------------
# prompt helper
# --------------------------------------------------------------------------
_SYSTEM_PROMPT = """你是严格的 unified diff 补丁生成器。你的唯一输出是一个 ```diff 代码块，除此之外不许有任何文字（解释、标题、总结、道歉都不要）。

输出格式（严格遵守）：
```diff
--- a/<相对 demo 根目录的路径>
+++ b/<相对 demo 根目录的路径>
@@ -<old_start>,<old_count> +<new_start>,<new_count> @@
 <上下文行：必须与 CONTEXT 中的原文逐字一致>
-<被删除的行>
+<新增的行>
```

硬性规则：
1. 只允许修改 CONTEXT 中出现的 `app/` 实现代码。CONTEXT 的 citation 形如 `demo/app/service.py:89-140`（相对 minimax 根），但 patch 路径必须相对 **demo 根目录**（去掉 `demo/` 前缀），即写成 `a/app/service.py` / `b/app/service.py`。
2. **绝对不要修改 `tests/` 下的任何文件**（回归测试与目标测试由测试方提供，修改测试视为无效实现）；也不要修改 `specs/`。
3. 禁止绝对路径、禁止包含 ".."、禁止二进制补丁、禁止重命名/删除文件。
4. 上下文行必须逐字复制自 CONTEXT（保持缩进、空格、换行风格）；行号只是提示，可按内容重新计算 hunk 头。
5. 如果 CONTEXT 不足以确定正确补丁，只输出一行：INSUFFICIENT_CONTEXT: <缺少什么>，不要猜测。
6. 整个回复只能有 1 个 ```diff 代码块，不要输出其他代码块。"""


def _format_citations(context_meta: Any) -> list[str]:
    """Render a flexible ``context_meta`` structure into citation lines."""

    if not context_meta:
        return []
    entries: list[Any]
    if isinstance(context_meta, Mapping):
        if "hits" in context_meta and isinstance(context_meta["hits"], (list, tuple)):
            entries = list(context_meta["hits"])
        elif "citations" in context_meta and isinstance(context_meta["citations"], (list, tuple)):
            entries = list(context_meta["citations"])
        elif "path" in context_meta or "citation" in context_meta:
            entries = [context_meta]
        elif all(isinstance(v, Mapping) for v in context_meta.values()):
            entries = [{**v, "path": v.get("path", k)} for k, v in context_meta.items()]
        else:
            # Descriptive metadata such as {"task": {...}, "context_sha256": "..."};
            # render it as a plain summary instead of a bogus "<unknown>" citation.
            summary: list[str] = []
            for key, value in context_meta.items():
                if isinstance(value, Mapping):
                    bits = [
                        f"{sub_key}={sub_value}"
                        for sub_key, sub_value in value.items()
                        if not isinstance(sub_value, (Mapping, list, tuple))
                    ]
                    summary.append(f"- {key}: " + (", ".join(bits[:8]) or "<mapping>"))
                elif isinstance(value, (list, tuple)):
                    summary.append(f"- {key}: <{len(value)} items>")
                else:
                    summary.append(f"- {key}: {value}")
            return summary
    elif isinstance(context_meta, (list, tuple)):
        entries = list(context_meta)
    else:
        entries = [context_meta]

    lines: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            lines.append(f"- {entry}")
            continue
        if not isinstance(entry, Mapping):
            lines.append(f"- {entry}")
            continue
        path = entry.get("path") or entry.get("file") or "<unknown>"
        start = entry.get("line_start", entry.get("start", ""))
        end = entry.get("line_end", entry.get("end", ""))
        citation = entry.get("citation")
        if not citation:
            if start and end:
                citation = f"{path}:{start}-{end}"
            elif start:
                citation = f"{path}:{start}"
            else:
                citation = str(path)
        extras = []
        for key in ("symbol", "title", "kind"):
            value = entry.get(key)
            if value:
                extras.append(str(value))
        suffix = f" ({', '.join(extras)})" if extras else ""
        lines.append(f"- {citation}{suffix}")
    return lines


def build_patch_messages(
    task_text: str,
    context_text: str,
    context_meta: Mapping[str, Any] | Sequence[Any] | None = None,
) -> list[dict[str, str]]:
    """Deprecated: build messages that ask the model for a unified diff.

    The live pipeline now uses :func:`build_edit_messages` (whole-file edits),
    which parses more reliably than byte-exact unified diffs.  This function is
    kept only because ``sddctx/cli.py`` still imports the name (it is no longer
    called there); it will be removed together with that import.
    """

    warnings.warn(
        "build_patch_messages() is deprecated; use build_edit_messages() instead",
        DeprecationWarning,
        stacklevel=2,
    )
    citations = _format_citations(context_meta)
    citation_block = "\n".join(citations) if citations else "- (context pack 未附带 citation 元数据)"
    user_prompt = f"""TASK
{task_text.strip()}

CONTEXT（唯一可信来源；每条命中的引用见 CITATIONS）
{context_text.strip()}

CITATIONS
{citation_block}

CONSTRAINTS
- 只修改 CONTEXT 中出现的 `app/` 实现代码；不要修改 `tests/` 下的任何文件，也不要修改 `specs/`。
- CONTEXT 的 citation 形如 `demo/app/service.py:89-140`；patch 路径要去掉 `demo/` 前缀，使用 `a/app/...` 与 `b/app/...` 形式（相对 demo 根目录）。
- 输出一个且仅一个 ```diff 代码块；上下文不足时只输出 `INSUFFICIENT_CONTEXT: <原因>`。

请输出 unified diff："""
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


# --------------------------------------------------------------------------
# client
# --------------------------------------------------------------------------
_EDIT_SYSTEM_PROMPT = """你是严谨的 Python 代码编辑助手。你的任务：按规格修改业务工程中 `app/` 下的实现代码。

输出规则（必须严格遵守）：
1. 对每一个需要修改的文件，输出该文件的**完整新内容**（不是 diff、不是片段、不要省略未改动的代码）。
2. 文件格式严格如下（每个文件一段，路径相对 demo 根目录，必须以 app/ 开头）：

=== FILE: app/service.py ===
```python
<该文件修改后的完整内容>
```

3. 只允许修改 `app/` 下的文件；绝对不能修改 `tests/`、`specs/`、`tasks/`。
4. 必须保留规格中"必须保持的行为"（分页、排序、错误信封、调用者隔离、total 语义、未知参数忽略）。
5. 输出中不要有任何解释文字、思考过程或总结；只输出上述 FILE 段。
6. 如果上下文不足，只输出一行 `INSUFFICIENT_CONTEXT: <原因>`。
"""

FILE_BLOCK_RE = re.compile(r"^===\s*FILE:\s*(?P<path>[A-Za-z0-9_./-]+?)\s*===\s*$", re.MULTILINE)
_FENCE_RE = re.compile(r"```[A-Za-z0-9_+.-]*[ \t]*\r?\n(.*?)\r?\n```", re.DOTALL)


def extract_file_writes(text: str) -> dict[str, str]:
    """Parse ``=== FILE: app/xxx.py ===`` sections into ``{path: full content}``.

    The model is asked to return whole files (much more reliable than asking an
    LLM for a byte-exact unified diff).  Fenced code blocks inside a section are
    unwrapped; unfenced sections are taken verbatim.
    """

    if not text:
        return {}
    matches = list(FILE_BLOCK_RE.finditer(text))
    if not matches:
        return {}
    writes: dict[str, str] = {}
    for index, match in enumerate(matches):
        path = match.group("path").strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[start:end]
        fence = _FENCE_RE.search(body)
        content = fence.group(1) if fence else body
        content = content.strip("\n")
        if content and not content.endswith("\n"):
            content += "\n"
        writes[path] = content
    return writes


def build_edit_messages(
    task_text: str,
    context_text: str,
    context_meta: Mapping[str, Any] | Sequence[Any] | None = None,
) -> list[dict[str, str]]:
    """Build messages that ask for complete file contents instead of a diff."""

    citations = _format_citations(context_meta)
    citation_block = "\n".join(citations) if citations else "- (context pack 未附带 citation 元数据)"
    user_prompt = f"""TASK
{task_text.strip()}

CONTEXT（唯一可信来源；完整文件内容见 FULL FILE 段）
{context_text.strip()}

CITATIONS
{citation_block}

现在请按规格修改实现代码。要求：
- 只改 `app/` 下的实现文件（通常是 app/service.py 与 app/routes.py）。
- 每个被修改的文件都输出**完整的新文件内容**，格式为：
  === FILE: app/xxx.py ===
  ```python
  ...
  ```
- 不要输出 diff，不要省略未改动内容，不要修改 tests/ 或 specs/。
"""
    return [
        {"role": "system", "content": _EDIT_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


class MiniMaxClient:
    """Thin ``requests``-based MiniMax chat client that writes dev records."""

    def __init__(self, cfg: MiniMaxConfig, record_dir: str | Path) -> None:
        self._cfg = cfg
        self._record_dir = Path(record_dir)
        self._model = cfg.model
        self._base_url = cfg.base_url.rstrip("/")

    # -- construction ----------------------------------------------------
    @classmethod
    def from_config(cls, cfg: MiniMaxConfig, record_dir: str | Path) -> "MiniMaxClient":
        return cls(cfg, record_dir)

    @property
    def configured(self) -> bool:
        return bool(self._cfg.api_key.strip())

    @property
    def record_dir(self) -> Path:
        return self._record_dir

    @property
    def model(self) -> str:
        return self._model

    def describe(self) -> str:
        """Human readable endpoint description (used by CLI/evidence logs)."""

        return f"MiniMax {self._model} @ {self._base_url}"

    # -- main entry point ------------------------------------------------
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tag: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call MiniMax once (with bounded retries) and persist one record.

        Returns
        -------
        dict
            ``{"content", "record_path", "usage", "model", "trace_id",
            "elapsed_ms", "finish_reason"}`` (plus ``response_id``).

        Raises
        ------
        MiniMaxNotConfigured
            When ``MINIMAX_API_KEY`` is empty.
        MiniMaxError
            On non-retryable API errors or after retries are exhausted; a
            failure record is still written first.
        """

        if not self.configured:
            raise MiniMaxNotConfigured(
                "MiniMax is not configured: set MINIMAX_API_KEY (see sddctx.config.MiniMaxConfig)"
            )

        url = f"{self._base_url}{CHAT_PATH}"
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "max_completion_tokens": self._cfg.max_completion_tokens,
        }
        if self._model == "MiniMax-M3.1-Flash":
            payload["reasoning_effort"] = self._cfg.reasoning_effort
        headers = {
            "Authorization": f"Bearer {self._cfg.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        timeout = (CONNECT_TIMEOUT_S, self._cfg.timeout_s)

        attempts: list[dict[str, Any]] = []
        last_error: MiniMaxError | None = None
        started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")

        for attempt in range(1, MAX_ATTEMPTS + 1):
            attempt_started = time.monotonic()
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=timeout)
            except requests.RequestException as exc:  # timeout / connection / TLS
                elapsed_ms = int((time.monotonic() - attempt_started) * 1000)
                last_error = MiniMaxError(
                    f"MiniMax transport error ({type(exc).__name__}): {_truncate(str(exc), 500)}",
                    retryable=True,
                )
                attempts.append(
                    {
                        "attempt": attempt,
                        "http_status": None,
                        "error_type": "transport_error",
                        "message": _truncate(str(exc), 500),
                        "retryable": True,
                        "elapsed_ms": elapsed_ms,
                    }
                )
                if attempt < MAX_ATTEMPTS:
                    _sleep(self._backoff(attempt))
                    continue
                record_path = self._write_record(
                    tag=tag,
                    meta=meta,
                    payload=payload,
                    headers=headers,
                    url=url,
                    http_status=None,
                    body={"error": {"type": "transport_error", "message": _truncate(str(exc), 500)}},
                    attempts=attempts,
                    elapsed_ms=elapsed_ms,
                    started_at=started_at,
                    error=last_error,
                )
                last_error.record_path = str(record_path)
                raise last_error from exc

            elapsed_ms = int((time.monotonic() - attempt_started) * 1000)
            body = self._parse_body(response)
            http_status = int(getattr(response, "status_code", 0) or 0)
            response_headers = getattr(response, "headers", {}) or {}
            trace_id = _header(response_headers, "Trace-Id")
            request_id = str(body.get("request_id")) if isinstance(body, Mapping) and body.get("request_id") else None
            error_type, error_message, base_code = _error_from_body(body)

            is_error = http_status >= 400 or error_type is not None or base_code is not None
            if is_error:
                retryable = http_status in RETRYABLE_HTTP_STATUS or base_code in RETRYABLE_BASE_RESP_CODES
                hint = ERROR_HINTS.get(base_code) if base_code is not None else None
                message = error_message or f"MiniMax API error (HTTP {http_status})"
                if hint:
                    message = f"{message} [{hint}]"
                last_error = MiniMaxError(
                    message,
                    http_status=http_status,
                    request_id=request_id,
                    trace_id=trace_id,
                    retryable=retryable,
                    response_body=body,
                    base_status_code=base_code,
                )
                attempts.append(
                    {
                        "attempt": attempt,
                        "http_status": http_status,
                        "error_type": error_type or ("base_resp" if base_code is not None else "http_error"),
                        "message": _truncate(message, 1000),
                        "base_status_code": base_code,
                        "request_id": request_id,
                        "trace_id": trace_id,
                        "retryable": retryable,
                        "elapsed_ms": elapsed_ms,
                    }
                )
                if retryable and attempt < MAX_ATTEMPTS:
                    _sleep(self._backoff(attempt))
                    continue
                record_path = self._write_record(
                    tag=tag,
                    meta=meta,
                    payload=payload,
                    headers=headers,
                    url=url,
                    http_status=http_status,
                    body=body,
                    attempts=attempts,
                    elapsed_ms=elapsed_ms,
                    started_at=started_at,
                    error=last_error,
                    trace_id=trace_id,
                )
                last_error.record_path = str(record_path)
                raise last_error

            # ---- success ----
            content = self._extract_content(body)
            attempts.append(
                {
                    "attempt": attempt,
                    "http_status": http_status,
                    "error_type": None,
                    "message": "",
                    "retryable": False,
                    "elapsed_ms": elapsed_ms,
                }
            )
            record_path = self._write_record(
                tag=tag,
                meta=meta,
                payload=payload,
                headers=headers,
                url=url,
                http_status=http_status,
                body=body,
                attempts=attempts,
                elapsed_ms=elapsed_ms,
                started_at=started_at,
                error=None,
                trace_id=trace_id,
            )
            usage = body.get("usage") if isinstance(body, Mapping) else {}
            choices = body.get("choices") if isinstance(body, Mapping) else None
            finish_reason = None
            if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
                finish_reason = choices[0].get("finish_reason")
            return {
                "content": content,
                "record_path": str(record_path),
                "usage": usage if isinstance(usage, Mapping) else {},
                "model": str(body.get("model") or self._model) if isinstance(body, Mapping) else self._model,
                "trace_id": trace_id,
                "elapsed_ms": elapsed_ms,
                "finish_reason": finish_reason,
                "response_id": str(body.get("id")) if isinstance(body, Mapping) and body.get("id") else None,
                "response_sha256": _sha256_text(content),
            }

        # Defensive: the loop always returns/raises.
        raise last_error or MiniMaxError("MiniMax request failed without a specific error")

    # -- internals -------------------------------------------------------
    @staticmethod
    def _backoff(attempt: int) -> float:
        delay = min(BACKOFF_BASE_S * (2 ** (attempt - 1)), BACKOFF_CAP_S)
        # tiny deterministic jitter from the attempt number (no RNG => no flakiness)
        return delay + (attempt % 3) * 0.1

    @staticmethod
    def _parse_body(response: requests.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except Exception:
            return {"_non_json": _truncate(getattr(response, "text", ""), 4000)}
        return body if isinstance(body, dict) else {"_body": body}

    @staticmethod
    def _extract_content(body: Mapping[str, Any]) -> str:
        choices = body.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
            message = choices[0].get("message")
            if isinstance(message, Mapping):
                return str(message.get("content") or "")
        return ""

    def _read_prev_record_sha256(self) -> str | None:
        index = self._record_dir / "index.jsonl"
        if not index.is_file():
            return None
        prev = None
        try:
            for raw_line in index.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get("record_sha256"):
                    prev = str(payload["record_sha256"])
        except OSError:
            return None
        return prev

    def _minimax_root(self) -> Path:
        """Resolve the ``minimax/`` project root for portable record paths.

        The toolkit layout is ``<minimax>/records/minimax``; anything else
        (mainly tests using a bare temp dir) falls back to the record dir's
        parent so index paths are still relative, never absolute.
        """

        record_dir = self._record_dir.resolve()
        if record_dir.name == "minimax" and record_dir.parent.name == "records":
            return record_dir.parent.parent
        return record_dir.parent

    def _portable_record_path(self, record_path: Path) -> str:
        """Return the record path relative to the minimax root (POSIX style)."""

        try:
            return record_path.resolve().relative_to(self._minimax_root()).as_posix()
        except ValueError:
            # Never leak an absolute path into the portable index.
            return record_path.name

    def _write_record(
        self,
        *,
        tag: str,
        meta: Mapping[str, Any] | None,
        payload: Mapping[str, Any],
        headers: Mapping[str, str],
        url: str,
        http_status: int | None,
        body: Mapping[str, Any],
        attempts: list[dict[str, Any]],
        elapsed_ms: int,
        started_at: str,
        error: MiniMaxError | None,
        trace_id: str | None = None,
    ) -> Path:
        self._record_dir.mkdir(parents=True, exist_ok=True)
        messages = payload.get("messages") if isinstance(payload, Mapping) else []
        messages_sha256 = _sha256_json(messages)
        content = self._extract_content(body) if not error else ""
        body_sha256 = _sha256_json(body)
        request_id = str(body.get("request_id")) if isinstance(body, Mapping) and body.get("request_id") else None
        response_id = str(body.get("id")) if isinstance(body, Mapping) and body.get("id") else None

        record: dict[str, Any] = {
            "schema": RECORD_SCHEMA,
            "tag": str(tag),
            "started_at_utc": started_at,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "elapsed_ms": elapsed_ms,
            "endpoint": {
                "protocol": "openai-compatible",
                "base_url": self._base_url,
                "path": CHAT_PATH,
                "url": url,
            },
            "model": self._model,
            "parameters": {
                "max_completion_tokens": self._cfg.max_completion_tokens,
                "reasoning_effort": payload.get("reasoning_effort"),
                "stream": False,
            },
            "auth": {
                "scheme": "Bearer",
                "header": "Authorization",
                "api_key_present": True,
                "api_key_sha256": _sha256_text(self._cfg.api_key),
                "group_id_sent": False,
            },
            "request": {
                "headers_redacted": _redact_headers(headers),
                "messages": messages,
                "messages_sha256": messages_sha256,
                "body_sha256": _sha256_json(payload),
            },
            "http_status": http_status,
            "response": {
                "body": body,
                "body_sha256": body_sha256,
                "content": content,
                "content_sha256": _sha256_text(content),
                "usage": body.get("usage") if isinstance(body, Mapping) else {},
                "finish_reason": self._finish_reason(body),
                "trace_id": trace_id,
                "request_id": request_id,
                "response_id": response_id,
            },
            "attempts": attempts,
            "error": None
            if error is None
            else {
                "message": str(error),
                "http_status": error.http_status,
                "base_status_code": error.base_status_code,
                "retryable": error.retryable,
            },
            "meta": _json_safe(meta) if meta else {},
            "outcome": "success" if error is None else "error",
        }

        prev = self._read_prev_record_sha256()
        record["chain"] = {"prev_record_sha256": prev}
        canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        record_sha256 = hashlib.sha256(canonical).hexdigest()
        record["chain"]["record_sha256"] = record_sha256

        base_name = f"{_utc_stamp()}-{_sanitize_tag(tag)}"
        record_path = self._record_dir / f"{base_name}.json"
        suffix = 2
        while record_path.exists():
            record_path = self._record_dir / f"{base_name}-{suffix}.json"
            suffix += 1
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

        index_line = {
            "schema": INDEX_SCHEMA,
            "finished_at_utc": record["finished_at_utc"],
            "tag": record["tag"],
            "record_path": self._portable_record_path(record_path),
            "record_sha256": record_sha256,
            "prev_record_sha256": prev,
            "model": self._model,
            "http_status": http_status,
            "outcome": record["outcome"],
            "usage": record["response"]["usage"],
            "trace_id": trace_id,
            "request_id": request_id,
            "response_id": response_id,
            "messages_sha256": messages_sha256,
            "content_sha256": record["response"]["content_sha256"],
        }
        with (self._record_dir / "index.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(index_line, ensure_ascii=False) + "\n")
        return record_path

    @staticmethod
    def _finish_reason(body: Mapping[str, Any]) -> str | None:
        choices = body.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
            return choices[0].get("finish_reason")
        return None


__all__ = [
    "RECORD_SCHEMA",
    "MiniMaxClient",
    "MiniMaxError",
    "MiniMaxNotConfigured",
    "build_edit_messages",
    "build_patch_messages",  # deprecated, kept for sddctx/cli.py import compatibility
    "extract_file_writes",
]
