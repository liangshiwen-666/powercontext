"""Tests for :mod:`sddctx.pcstore` against a real in-process PowerContext runtime.

The toolkit declares ``powercontext[builtin]==1.4.1`` as a hard dependency, so these are
integration tests rather than mocks: every assertion reads or writes a real SQLite
database owned by the builtin runtime. All databases live under pytest's ``tmp_path``;
the tests never touch ``.data/`` or a PowerContext Server.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sddctx.pcstore import (
    DEFAULT_SCOPE_KEY,
    MAX_TEXT_BYTES,
    PcStoreError,
    PcStoreUnavailableError,
    TaskStateStore,
    split_text,
)

SCOPE_KEY = "pytest-sddctx-orders-v1"

DECISIONS = [
    ("decision", "订单查询接口新增四个筛选参数：status、created_after、created_before、min_amount。"),
    (
        "constraint",
        "REQ-ORD-002 调用者隔离不可退化：caller isolation is non-negotiable，"
        "任何新参数都不能改变 X-User-Id 的作用域。",
    ),
    ("next_step", "下一步：在 list_orders 的过滤阶段应用 status 筛选，total 保持过滤后、分页前语义。"),
]


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "powercontext" / "pc.db"


@pytest.fixture()
def store(db_path: Path) -> TaskStateStore:
    return TaskStateStore(db_path, scope_key=SCOPE_KEY)


def test_save_then_new_instance_reads_back(store: TaskStateStore, db_path: Path) -> None:
    """Write three records, drop the runtime, read them back from a fresh instance."""

    saved = store.save(DECISIONS)
    assert len(saved) == 3
    assert db_path.is_file()

    for entry, (kind, text) in zip(saved, DECISIONS):
        assert set(entry) == {"kind", "text", "ref", "state"}
        assert entry["kind"] == kind
        assert entry["text"] == text
        assert entry["state"] == "active"
        assert entry["ref"]["family"] == "atomic-memory"
        assert entry["ref"]["revision"] == 1

    # A new instance opens a brand-new runtime on the same file, which is exactly what a
    # restarted CLI process does.
    restarted = TaskStateStore(db_path, scope_key=SCOPE_KEY)
    listed = restarted.list()
    assert len(listed) == 3
    assert {entry["text"] for entry in listed} == {text for _, text in DECISIONS}
    assert {json.dumps(entry["ref"], sort_keys=True) for entry in listed} == {
        json.dumps(entry["ref"], sort_keys=True) for entry in saved
    }

    hits = restarted.search("caller isolation", limit=5)
    assert hits, "search must find an English keyword in a record written by another instance"
    assert any("X-User-Id" in hit["text"] for hit in hits)
    saved_refs = {json.dumps(entry["ref"], sort_keys=True) for entry in saved}
    assert {json.dumps(hit["ref"], sort_keys=True) for hit in hits} & saved_refs
    assert all(isinstance(hit["score"], float) for hit in hits)


def test_prepare_returns_ready_content_with_citation(store: TaskStateStore) -> None:
    """Prepared context is ready and every item carries an exact artifact citation."""

    saved = store.save([("decision", "验收环境使用进程内 PowerContext 保存任务状态，不启动任何 Server。")])
    prepared = store.prepare("进程内 PowerContext 任务状态")

    assert prepared["status"] == "ready"
    content = prepared["content"]
    assert isinstance(content, str) and content.strip()
    assert prepared["bytes"] == len(content.encode("utf-8"))
    assert "citation" in content
    assert "artifact_ref" in content
    assert saved[0]["ref"]["artifact_id"] in content


def test_record_task_outcome_full_chain(store: TaskStateStore, db_path: Path) -> None:
    """The deterministic work loop records every reference and a complete continuity view."""

    result = store.record_task_outcome(
        objective="为 GET /orders 增加四个可选筛选参数并保持 v1.0 行为兼容",
        status="succeeded",
        summary="实现 status/created_after/created_before/min_amount 组合筛选，target 与 regression 测试全绿。",
        checks=[("pytest tests/target -q", "passed"), ("pytest tests/regression -q", "passed"), ("manual review", "skipped")],
        done=["在 service.list_orders 的过滤阶段应用 status 筛选", "新增 parse_created_bounds 与 parse_min_amount"],
        todo=["补充 README 中的筛选示例"],
    )

    assert result["contract"]["kind"] == "work-contract"
    assert result["contract"]["source_ref"]["source_type"] == "content"
    assert result["handoff"]["family"] == "handoff"
    assert result["ack"]["status"] == "accepted"
    assert result["ack"]["resolution_status"] == "resolved"
    assert result["ack"]["selection"] == "exact"
    assert result["ack"]["selected_revision"] == result["handoff"]
    assert result["outcome"]["kind"] == "task-outcome"
    assert result["outcome"]["content_digest"].startswith("sha256:")
    assert result["disposition"] == "continuable"  # todo is not empty

    continuity = result["continuity"]
    assert continuity["total_event_count"] >= 4
    event_kinds = [event["kind"] for event in continuity["events"]]
    for expected in ("work-contract", "handoff-boundary", "handoff-receipt", "task-outcome"):
        assert expected in event_kinds
    coverage = continuity["coverage"]
    assert coverage["transfer_state"] == "accepted"
    assert coverage["outcome_state"] == "covered"
    assert coverage["contract_records"] == 1
    assert coverage["acknowledgement_records"] == 1

    # The scope and its timeline survive a restart.
    restarted = TaskStateStore(db_path, scope_key=SCOPE_KEY)
    assert restarted.scope_id() == result["scope_id"]


def test_chinese_search_hits(store: TaskStateStore) -> None:
    """Chinese full-text search (jieba tokenizer) finds records written in Chinese."""

    store.save(DECISIONS)

    hits = store.search("调用者隔离")
    assert hits, "Chinese query must hit the constraint record"
    assert any("调用者隔离" in hit["text"] for hit in hits)
    assert any("constraint" == hit["kind"] for hit in hits)

    filter_hits = store.search("筛选参数")
    assert filter_hits
    assert any("筛选参数" in hit["text"] for hit in filter_hits)


def test_scope_key_is_idempotent_and_isolated(store: TaskStateStore, db_path: Path) -> None:
    """The same scope key resolves to the same scope id; another key is another scope."""

    first_scope = store.scope_id()
    other_instance = TaskStateStore(db_path, scope_key=SCOPE_KEY)
    assert other_instance.scope_id() == first_scope
    assert store.scope_id() == first_scope  # repeated calls stay idempotent

    store.save([("decision", "scope A record")])
    other_scope = TaskStateStore(db_path, scope_key="pytest-other-scope-v1")
    assert other_scope.scope_id() != first_scope
    assert other_scope.list() == []  # a different scope starts empty

    with pytest.raises(ValueError):
        TaskStateStore(db_path, scope_key="   ")


def test_long_text_is_split_within_limit(store: TaskStateStore) -> None:
    """A value above 8192 UTF-8 bytes is written as several records, losslessly."""

    body = "订单筛选规则要求 total 反映过滤后的数量。" * 1200
    assert len(body.encode("utf-8")) > MAX_TEXT_BYTES

    saved = store.save([("fact", body)])
    assert len(saved) >= 2
    assert all(len(entry["text"].encode("utf-8")) <= MAX_TEXT_BYTES for entry in saved)
    assert "".join(entry["text"] for entry in saved) == body
    assert len(store.list()) == len(saved)


def test_split_text_respects_multibyte_boundaries() -> None:
    text = "订" * 700  # 2100 UTF-8 bytes
    pieces = split_text(text, limit=1000)
    assert len(pieces) == 3
    assert all(len(piece.encode("utf-8")) <= 1000 for piece in pieces)
    assert "".join(pieces) == text

    with pytest.raises(ValueError):
        split_text("")


def test_open_failure_keeps_original_error(tmp_path: Path) -> None:
    """Opening a database that cannot be a SQLite file surfaces a clear, chained error."""

    not_a_database = tmp_path / "directory-instead-of-file"
    not_a_database.mkdir()
    broken = TaskStateStore(not_a_database)

    with pytest.raises(PcStoreUnavailableError) as excinfo:
        broken.list()

    message = str(excinfo.value)
    assert "failed to open PowerContext runtime" in message
    assert str(not_a_database) in message
    assert excinfo.value.__cause__ is not None


def test_save_rejects_invalid_entries(store: TaskStateStore) -> None:
    assert store.save([]) == []
    with pytest.raises(ValueError):
        store.save([("decision", "   ")])
    with pytest.raises(ValueError):
        store.save([("", "some text")])
    with pytest.raises(ValueError):
        store.save([("k" * 129, "some text")])
    with pytest.raises(ValueError):
        store.search("query", limit=0)
    with pytest.raises(ValueError):
        store.prepare("query", max_bytes=8)


def test_default_scope_key_constant() -> None:
    assert DEFAULT_SCOPE_KEY == "sddctx-orders-v1"
    assert issubclass(PcStoreUnavailableError, PcStoreError)


def test_record_task_outcome_failed_with_empty_lists(store: TaskStateStore) -> None:
    """A failed attempt with no done/todo text still records a complete chain."""

    result = store.record_task_outcome(
        objective="尝试为筛选参数增加向量排序",
        status="failed",
        summary="向量排序依赖 embedding 模型，本期不引入，回滚改动。",
        checks=[],
        done=[],
        todo=[],
    )
    assert result["disposition"] == "blocked"
    assert result["ack"]["status"] == "accepted"
    assert result["continuity"]["total_event_count"] >= 4
    assert result["continuity"]["coverage"]["outcome_state"] == "covered"


def test_record_task_outcome_reports_truncated_items(store: TaskStateStore) -> None:
    """Items beyond the 64-item Work ceiling are reported instead of failing validation."""

    result = store.record_task_outcome(
        objective="列出全部待办",
        status="partial",
        summary="只完成了前 64 项。",
        checks=[],
        done=[],
        todo=[f"待办事项 {index}" for index in range(70)],
    )
    assert result["truncated"]["todo"] == 6
    assert result["disposition"] == "continuable"
    assert result["continuity"]["total_event_count"] >= 4


def test_missing_powercontext_gives_install_hint(
    monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> None:
    """Importing PowerContext lazily keeps ``import sddctx.pcstore`` usable and actionable."""

    import builtins

    from sddctx import pcstore

    real_import = builtins.__import__

    def blocked_import(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "powercontext" or name.startswith("powercontext."):
            raise ImportError("No module named 'powercontext'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(pcstore, "_MODULES", None)
    monkeypatch.setattr(builtins, "__import__", blocked_import)
    store = TaskStateStore(db_path, scope_key=SCOPE_KEY)
    with pytest.raises(PcStoreUnavailableError) as excinfo:
        store.save([("decision", "anything")])
    assert "powercontext[builtin]" in str(excinfo.value)
