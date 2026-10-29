"""Assemble the development context pack for one SDD task.

Pipeline::

    task description
      -> requirement ids + keywords
      -> seekdb retrieval (specs / code / tests, one query per doc kind)
      -> PowerContext task-state read (done / todo / verification / next action)
      -> context pack JSON (every entry carries a real ``path:line`` citation)
      -> rendered context text for the coding assistant

The pack is deliberately data-driven: nothing about the demo task is hard-coded,
so re-importing an updated spec changes the output.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

REQUIREMENT_RE = re.compile(r"REQ-[A-Z]+-\d{3}")
BACKTICK_RE = re.compile(r"`([^`]+)`")
IDENTIFIER_RE = re.compile(r"\b(?:[a-z]+_[a-z0-9_]+|[A-Za-z]+[0-9]*)\b")
TEST_NAME_RE = re.compile(r"\btest_[a-z0-9_]+\b")

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "when", "then",
    "must", "should", "will", "不得", "必须", "需要", "以及", "并且", "实现", "测试",
    "文件", "代码", "规格", "任务", "行为", "返回", "支持", "参数", "查询", "订单",
}

MAX_SPEC_ENTRIES = 10
MAX_CODE_ENTRIES = 10
MAX_FILES_IN_PROMPT = 6
MAX_BODY_CHARS = 4000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_task(task_path: Path, *, root: Path) -> dict[str, Any]:
    """Read a task description and extract the requirement ids it references."""

    text = task_path.read_text(encoding="utf-8")
    title = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            break
    try:
        rel = task_path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        rel = task_path.as_posix()
    return {
        "id": task_path.stem,
        "title": title or task_path.stem,
        "path": rel,
        "text": text,
        "requirements": sorted(set(REQUIREMENT_RE.findall(text))),
    }


def _tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in BACKTICK_RE.findall(text):
        tokens.extend(IDENTIFIER_RE.findall(raw))
    tokens.extend(IDENTIFIER_RE.findall(text))
    tokens.extend(REQUIREMENT_RE.findall(text))
    seen: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        lowered = token.lower()
        if len(token) < 2 or lowered in STOPWORDS or lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(token)
    return ordered


def _requirement_of(hit: dict[str, Any]) -> str | None:
    for candidate in (hit.get("symbol") or "", hit.get("title") or "", hit.get("body") or ""):
        match = REQUIREMENT_RE.search(candidate)
        if match:
            return match.group(0)
    return None


def _spec_entry(hit: dict[str, Any], *, required: bool, matched_by: str) -> dict[str, Any]:
    body = (hit.get("body") or "").strip()
    return {
        "requirement_id": _requirement_of(hit),
        "title": hit.get("title") or "",
        "citation": hit.get("citation") or "",
        "score": round(float(hit.get("score") or 0.0), 4),
        "required": required,
        "matched_by": matched_by,
        "excerpt": body[:MAX_BODY_CHARS],
    }


def _code_entry(hit: dict[str, Any], *, reason: str) -> dict[str, Any]:
    body = (hit.get("body") or "").strip()
    return {
        "path": hit.get("path") or "",
        "symbol": hit.get("symbol") or "",
        "line_start": hit.get("line_start"),
        "line_end": hit.get("line_end"),
        "citation": hit.get("citation") or "",
        "score": round(float(hit.get("score") or 0.0), 4),
        "reason": reason,
        "excerpt": body[:MAX_BODY_CHARS],
    }


def _test_entry(hit: dict[str, Any], *, reason: str) -> dict[str, Any]:
    path = hit.get("path") or ""
    suite = "target" if "/target/" in f"/{path}" else "regression" if "/regression/" in f"/{path}" else "other"
    body = (hit.get("body") or "").strip()
    return {
        "name": hit.get("symbol") or "",
        "suite": suite,
        "path": path,
        "citation": hit.get("citation") or "",
        "score": round(float(hit.get("score") or 0.0), 4),
        "requirements": sorted(set(REQUIREMENT_RE.findall(body))),
        "reason": reason,
        "excerpt": body[:1200],
    }


def select_specs(store, task: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Select applicable spec entries: exact requirement hits first, then keywords."""

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    missing: list[str] = []

    for requirement in task["requirements"]:
        hits = store.search(requirement, kinds=["spec"], limit=5)
        exact = next(
            (
                hit
                for hit in hits
                if (hit.get("symbol") == requirement)
                or requirement in (hit.get("title") or "")
                or requirement in (hit.get("body") or "")
            ),
            None,
        )
        if exact is None:
            missing.append(requirement)
            continue
        entry = _spec_entry(exact, required=True, matched_by=f"requirement:{requirement}")
        if entry["citation"] in seen:
            continue
        seen.add(entry["citation"])
        entries.append(entry)

    keyword_query = " ".join(_tokens(task["text"])[:20])
    if keyword_query:
        for hit in store.search(keyword_query, kinds=["spec"], limit=8):
            citation = hit.get("citation") or ""
            if citation in seen:
                continue
            seen.add(citation)
            entries.append(_spec_entry(hit, required=False, matched_by="keyword"))
            if len(entries) >= MAX_SPEC_ENTRIES:
                break

    return entries[:MAX_SPEC_ENTRIES], missing


def _code_symbols(text: str) -> list[str]:
    """Extract identifier-like symbols that the specification references."""

    symbols: list[str] = []
    seen: set[str] = set()
    for raw in BACKTICK_RE.findall(text):
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", raw):
            if "_" in token or re.search(r"[a-z][A-Z]", token):
                if token.lower() not in seen:
                    seen.add(token.lower())
                    symbols.append(token)
    for token in re.findall(r"\b([a-z_][a-z0-9_]*)\(", text):
        if len(token) > 4 and token.lower() not in seen:
            seen.add(token.lower())
            symbols.append(token)
    return symbols


def select_code(store, task: dict[str, Any], specs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select the code symbols most likely to require modification."""

    spec_text = "\n".join(entry.get("excerpt", "") for entry in specs)
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(hits: Iterable[dict[str, Any]], reason: str) -> None:
        for hit in hits:
            key = f"{hit.get('path')}::{hit.get('symbol')}"
            if key in seen:
                continue
            seen.add(key)
            entries.append(_code_entry(hit, reason=reason))

    for symbol in _code_symbols(task["text"] + "\n" + spec_text)[:12]:
        hits = store.search(symbol, kinds=["code"], limit=4)
        exact = [hit for hit in hits if hit.get("symbol") == symbol]
        if exact:
            _add(exact[:1], f"symbol:{symbol}")
        elif hits:
            _add(hits[:1], f"symbol~:{symbol}")
        if len(entries) >= MAX_CODE_ENTRIES:
            break

    tokens = _tokens(task["text"]) + _tokens(spec_text)
    seen_tokens: set[str] = set()
    ordered: list[str] = []
    for token in tokens:
        if token.lower() in seen_tokens:
            continue
        seen_tokens.add(token.lower())
        ordered.append(token)
    query = " ".join(ordered[:30])
    if query:
        _add(store.search(query, kinds=["code"], limit=MAX_CODE_ENTRIES), "task+spec keywords")
    if len(entries) < 3 and len(ordered) > 5:
        _add(store.search(" ".join(ordered[:8]), kinds=["code"], limit=MAX_CODE_ENTRIES), "fallback keywords")
    return entries[:MAX_CODE_ENTRIES]


def _search_path(store, query: str, *, kinds: Sequence[str], limit: int, path_contains: str | None) -> list[dict[str, Any]]:
    """Search with an optional path filter, tolerating stores without it."""

    if path_contains is None:
        return list(store.search(query, kinds=kinds, limit=limit))
    try:
        return list(store.search(query, kinds=kinds, limit=limit, path_contains=path_contains))
    except TypeError:
        hits = list(store.search(query, kinds=kinds, limit=limit * 3))
        return [hit for hit in hits if path_contains in (hit.get("path") or "")][:limit]


def _enumerate_suite(store, *, kinds: Sequence[str], path_contains: str, limit: int) -> list[dict[str, Any]]:
    """Enumerate every chunk of a suite (``list_chunks`` when available)."""

    fn = getattr(store, "list_chunks", None)
    if callable(fn):
        try:
            return list(fn(kinds=kinds, path_contains=path_contains, limit=limit))
        except TypeError:  # pragma: no cover - older store implementation
            pass
    return _search_path(store, "", kinds=kinds, limit=limit, path_contains=path_contains)


def select_tests(store, task: dict[str, Any]) -> list[dict[str, Any]]:
    """Select the full functional suite plus the complete regression suite.

    Functional (``suite=target``) tests are first located by the requirement ids
    they mention, then the whole target suite is enumerated; the regression
    suite is enumerated in full because "which existing behaviour must keep
    working" must never depend on keyword luck.
    """

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(hits: Iterable[dict[str, Any]], reason: str) -> None:
        for hit in hits:
            citation = hit.get("citation") or ""
            if citation in seen:
                continue
            seen.add(citation)
            entry = _test_entry(hit, reason=reason)
            entry["excerpt"] = entry["excerpt"][: 1200 if entry["suite"] == "target" else 400]
            entries.append(entry)

    for requirement in task["requirements"]:
        _add(store.search(requirement, kinds=["test"], limit=4), f"requirement:{requirement}")

    target_hits = _enumerate_suite(store, kinds=["test"], path_contains="tests/target", limit=20)
    _add(target_hits, "functional suite (enumerated)")

    regression_hits = _enumerate_suite(store, kinds=["test"], path_contains="tests/regression", limit=40)
    _add(regression_hits, "regression suite (enumerated)")

    # deterministic order: functional suite first, then regression, then by path/name
    suite_order = {"target": 0, "regression": 1}
    entries.sort(
        key=lambda entry: (
            suite_order.get(str(entry.get("suite")), 9),
            str(entry.get("path") or ""),
            str(entry.get("name") or ""),
        )
    )
    return entries


def _read_state(state_store, query: str, root: Path | None = None) -> dict[str, Any]:
    try:
        records = state_store.list()
        prepared = state_store.prepare(query or "补丁 测试 验证 状态")
    except Exception as exc:  # pragma: no cover - surfaced in the pack for debugging
        return {
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
            "done": [],
            "todo": [],
            "verification": [],
            "next_action": None,
            "prepared_context": "",
        }

    # PowerContext returns records unordered; sort for reproducible output.
    records = sorted(records, key=lambda item: (str(item.get("kind") or ""), str(item.get("text") or "")))
    done, todo, verification, decisions = [], [], [], []
    closed_todos: set[str] = set()
    next_action = None
    for record in records:
        kind = str(record.get("kind") or "")
        text = str(record.get("text") or "")
        if kind == "todo_done":
            closed_todos.add(text)
        elif kind in {"done", "completed", "progress"}:
            done.append(text)
        elif kind in {"todo", "pending", "next_step", "next_action"}:
            todo.append(text)
            if kind in {"next_step", "next_action"} and next_action is None:
                next_action = text
        elif kind in {"verification", "test_result", "validation"}:
            verification.append(text)
        else:
            decisions.append(text)
    todo = [item for item in todo if item not in closed_todos]
    db_path = str(getattr(state_store, "db_path", ""))
    if root is not None and db_path:
        try:
            db_path = Path(db_path).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            pass
    return {
        "available": True,
        "scope_key": getattr(state_store, "scope_key", None),
        "db_path": db_path,
        "record_count": len(records),
        "done": done,
        "todo": todo,
        "verification": verification,
        "decisions": decisions,
        "next_action": next_action,
        "prepared_context": prepared.get("content", ""),
        "prepared_status": prepared.get("status"),
        "records": records,
    }


def _file_contents(root: Path, code_entries: Sequence[dict[str, Any]]) -> dict[str, str]:
    contents: dict[str, str] = {}
    for entry in code_entries:
        rel = entry.get("path") or ""
        if not rel or rel in contents:
            continue
        if len(contents) >= MAX_FILES_IN_PROMPT:
            break
        target = root / rel
        if target.is_file():
            contents[rel] = target.read_text(encoding="utf-8")
    return contents


def render_context_text(pack: dict[str, Any]) -> str:
    """Render the context pack as the text block sent to the coding assistant."""

    lines: list[str] = []
    lines.append("=== TASK ===")
    lines.append(pack.get("task_text", "").strip())
    lines.append("")

    lines.append("=== APPLICABLE SPECIFICATIONS ===")
    for index, spec in enumerate(pack["specs"], 1):
        requirement = spec.get("requirement_id") or "GENERAL"
        lines.append(f"[SPEC {index}] {requirement} {spec.get('title', '')}")
        lines.append(f"citation: {spec.get('citation')}")
        lines.append(spec.get("excerpt", "").strip())
        lines.append("")
    if pack.get("specs_missing"):
        lines.append(f"NOTE: requirement ids not found in the material store: {', '.join(pack['specs_missing'])}")
        lines.append("")

    lines.append("=== CODE TO MODIFY ===")
    for index, code in enumerate(pack["code"], 1):
        lines.append(f"[CODE {index}] {code.get('symbol')}  ({code.get('citation')})")
        lines.append(code.get("excerpt", "").strip())
        lines.append("")
    for path, content in pack.get("file_contents", {}).items():
        lines.append(f"----- FULL FILE: {path} -----")
        lines.append(content.rstrip())
        lines.append(f"----- END FILE: {path} -----")
        lines.append("")

    lines.append("=== TESTS ===")
    for index, test in enumerate(pack["tests"], 1):
        requirements = ",".join(test.get("requirements") or [])
        lines.append(
            f"[TEST {index}] ({test.get('suite')}) {test.get('name')}  "
            f"citation: {test.get('citation')}  requirements: {requirements or '-'}"
        )
    lines.append("")

    lines.append("=== TASK STATE (PowerContext) ===")
    state = pack["state"]
    if not state.get("available"):
        lines.append(f"state unavailable: {state.get('error')}")
    else:
        lines.append(f"scope_key: {state.get('scope_key')}  db: {state.get('db_path')}")
        lines.append(f"done: {json.dumps(state.get('done'), ensure_ascii=False)}")
        lines.append(f"todo: {json.dumps(state.get('todo'), ensure_ascii=False)}")
        lines.append(f"verification: {json.dumps(state.get('verification'), ensure_ascii=False)}")
        next_action = state.get("next_action")
        if next_action:
            lines.append(f"next_action: {next_action}")
    lines.append("")
    return "\n".join(lines)


def build_context(
    *,
    store,
    state_store,
    task_path: Path,
    root: Path,
) -> dict[str, Any]:
    """Build the full context pack for ``task_path``."""

    task = load_task(task_path, root=root)
    specs, missing = select_specs(store, task)
    code = select_code(store, task, specs)
    tests = select_tests(store, task)
    state_query = " ".join([task["title"], *task["requirements"], "补丁 测试 状态 验证"])
    state = _read_state(state_store, state_query, root)
    pack: dict[str, Any] = {
        "schema": "sddctx.context-pack/v1",
        "generated_at": _now(),
        "task": {key: task[key] for key in ("id", "title", "path", "requirements")},
        "task_text": task["text"],
        "specs": specs,
        "specs_missing": missing,
        "code": code,
        "file_contents": _file_contents(root, code),
        "tests": tests,
        "state": state,
        "sources": {
            "material_store": getattr(store, "describe", lambda: "seekdb")() if callable(getattr(store, "describe", None)) else "seekdb",
            "state_store": f"PowerContext builtin runtime @ {getattr(state_store, 'db_path', '')}",
            "db_stats": store.stats() if hasattr(store, "stats") else {},
        },
    }
    pack["context_text"] = render_context_text(pack)
    pack["context_sha256"] = __import__("hashlib").sha256(pack["context_text"].encode("utf-8")).hexdigest()
    return pack
