"""Tests for :mod:`sddctx.store` against a real seekdb server.

These tests require a reachable seekdb (MySQL protocol) and are skipped with a
clear reason when it is not running.  They use the dedicated database
``sddctx_test`` and never touch the ``sddctx`` / demo databases.
"""

from __future__ import annotations

import importlib.util
import os
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

from sddctx.config import MINIMAX_ROOT, SeekDBConfig
from sddctx.ingest import collect_chunks
from sddctx.protocols import Chunk
from sddctx.store import DOC_KINDS, SNIPPET_CHARS, MaterialStore, StoreError

HOST = os.environ.get("SEEKDB_HOST", "127.0.0.1")
PORT = int(os.environ.get("SEEKDB_PORT", "2881"))
TEST_DB = "sddctx_test"
DEMO_ROOT = MINIMAX_ROOT / "demo"


def _seekdb_reachable() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=1.0):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _seekdb_reachable(),
    reason=f"seekdb server 未监听 {HOST}:{PORT}；先执行 `systemctl start seekdb`",
)


@pytest.fixture(scope="session")
def cfg() -> SeekDBConfig:
    return SeekDBConfig(
        host=HOST, port=PORT, user="root", password="", database=TEST_DB, embedded_dir=None
    )


@pytest.fixture()
def store(cfg: SeekDBConfig) -> Iterator[MaterialStore]:
    material = MaterialStore.from_config(cfg)
    material.init_schema()
    material.reset()
    try:
        yield material
    finally:
        try:
            material.reset()
        finally:
            material.close()


def _chunk(kind: str, body: str, path: str | None = None, line_start: int = 1, symbol: str = "") -> Chunk:
    return Chunk(
        kind=kind,  # type: ignore[arg-type]
        path=path or f"demo/tests/{kind}.py",
        line_start=line_start,
        line_end=line_start + max(len(body.splitlines()) - 1, 0),
        title=symbol or kind,
        symbol=symbol or kind,
        body=body,
    )


# ---------------------------------------------------------------------------
# schema / connection
# ---------------------------------------------------------------------------
def test_init_schema_is_idempotent(store: MaterialStore) -> None:
    store.init_schema()
    store.init_schema()
    assert store.stats() == {"spec": 0, "code": 0, "test": 0}


def test_schema_uses_ik_fulltext_parser(store: MaterialStore) -> None:
    rows = store._fetch(f"SHOW CREATE TABLE {store.table}")
    ddl = str(rows[0][1])
    assert "FULLTEXT KEY" in ddl.upper()
    assert "PARSER ik" in ddl, f"全文索引必须使用 ik parser，实际 DDL: {ddl}"


def test_connection_error_is_actionable() -> None:
    bad = SeekDBConfig(host="127.0.0.1", port=1, user="root", password="", database=TEST_DB)
    material = MaterialStore.from_config(bad)
    try:
        with pytest.raises(StoreError) as excinfo:
            material.init_schema()
    finally:
        material.close()
    message = str(excinfo.value)
    assert "SEEKDB_HOST" in message and "SEEKDB_PORT" in message
    assert "systemctl start seekdb" in message


# ---------------------------------------------------------------------------
# upsert / stats / reset
# ---------------------------------------------------------------------------
def test_upsert_is_idempotent_and_counts(store: MaterialStore) -> None:
    chunks = [
        _chunk("spec", "### REQ-ORD-006 状态筛选\n订单查询支持 status 过滤。", path="demo/specs/orders.md"),
        _chunk("code", "def list_orders(repo, user_id):\n    return repo.list_for_user(user_id)", path="demo/app/service.py", line_start=186),
        _chunk("test", "def test_filter_by_status():\n    assert True", path="demo/tests/target/test_orders_filters_target.py", line_start=13),
    ]
    assert store.upsert(chunks) == 3
    assert store.stats() == {"spec": 1, "code": 1, "test": 1}

    # Same data again: REPLACE INTO keeps one row per (path, line_start).
    assert store.upsert(chunks) == 3
    assert store.stats() == {"spec": 1, "code": 1, "test": 1}


def test_reset_by_kind(store: MaterialStore) -> None:
    store.upsert(
        [
            _chunk("spec", "### REQ-ORD-001 列表分页", path="demo/specs/orders.md"),
            _chunk("code", "def parse_sort(sort_raw):\n    return sort_raw", path="demo/app/service.py"),
            _chunk("test", "def test_health_endpoint(client):\n    assert True", path="demo/tests/regression/test_orders_regression.py"),
        ]
    )
    assert store.reset(["spec"]) == 1
    assert store.stats() == {"spec": 0, "code": 1, "test": 1}
    assert store.reset() == 2
    assert store.stats() == {"spec": 0, "code": 0, "test": 0}


def test_upsert_rejects_invalid_kind(store: MaterialStore) -> None:
    with pytest.raises(ValueError, match="unknown document kind"):
        store.upsert([_chunk("bogus", "x")])


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
def test_search_chinese_requires_ik_hits(store: MaterialStore) -> None:
    store.upsert(
        [
            _chunk(
                "spec",
                "### REQ-ORD-006 状态筛选（v2.0 新增）\n"
                "GET /orders 支持可选查询参数 status，取值 pending、paid、shipped、cancelled；"
                "提供时只返回该状态的订单。",
                path="demo/specs/orders.md",
            )
        ]
    )

    hits = store.search("状态筛选", kinds=["spec", "code", "test"])
    assert hits, "中文查询必须命中（ik parser 回归；默认 space parser 会返回 0 条）"
    hit = hits[0]
    assert hit["kind"] == "spec"
    assert hit["path"] == "demo/specs/orders.md"
    assert hit["citation"] == "demo/specs/orders.md:1-2"
    assert hit["score"] > 0
    assert len(hit["snippet"]) <= SNIPPET_CHARS


def test_search_returns_three_kinds_in_one_query(store: MaterialStore) -> None:
    store.upsert(
        [
            _chunk(
                "spec",
                "### REQ-ORD-006 状态筛选\n订单查询支持可选参数 status，只返回该状态的订单。",
                path="demo/specs/orders.md",
            ),
            _chunk(
                "code",
                'def list_orders(repo, user_id):\n'
                '    """返回调用者订单列表（订单状态筛选在 service 层完成）。"""\n'
                "    return repo.list_for_user(user_id)",
                path="demo/app/service.py",
                line_start=186,
            ),
            _chunk(
                "test",
                'def test_filter_by_status():\n'
                '    """REQ-ORD-006: 订单状态筛选。"""\n'
                "    assert True",
                path="demo/tests/target/test_orders_filters_target.py",
                line_start=13,
            ),
        ]
    )

    hits = store.search("订单 状态筛选", kinds=list(DOC_KINDS), limit=10)
    found = {hit["kind"] for hit in hits}
    assert {"spec", "code", "test"} <= found, f"一条 SQL 应能返回三类资料，实际: {found}"
    assert all(hit["citation"].count(":") == 1 and "-" in hit["citation"] for hit in hits)


def test_search_respects_kind_filter(store: MaterialStore) -> None:
    store.upsert(
        [
            _chunk("spec", "订单查询规格说明，状态筛选。", path="demo/specs/orders.md"),
            _chunk(
                "code",
                'def list_orders(repo):\n    """返回订单列表。"""\n    return repo.orders()',
                path="demo/app/service.py",
            ),
        ]
    )
    hits = store.search("订单", kinds=["code"])
    assert hits
    assert {hit["kind"] for hit in hits} == {"code"}


def test_search_empty_query_returns_empty(store: MaterialStore) -> None:
    store.upsert([_chunk("spec", "订单查询规格", path="demo/specs/orders.md")])
    assert store.search("   ", kinds=["spec"]) == []


def test_search_path_contains_filters(store: MaterialStore) -> None:
    """``path_contains`` selects a suite by directory even without FTS text."""

    body = 'def test_alpha():\n    """orders suite。"""\n    assert True'
    store.upsert(
        [
            _chunk("test", body, path="demo/tests/target/test_x.py"),
            _chunk("test", body, path="demo/tests/regression/test_y.py"),
        ]
    )

    # FTS + path: the combined query keeps the suite narrowing.
    regression = store.search("orders", kinds=["test"], path_contains="/regression/")
    assert regression and all("/regression/" in hit["path"] for hit in regression)

    target = store.search("orders", kinds=["test"], path_contains="/target/")
    assert target and all("/target/" in hit["path"] for hit in target)

    # Empty query + path filter enumerates the suite (no FTS term needed).
    enumerated = store.search("", kinds=["test"], path_contains="/target/")
    assert enumerated and all("/target/" in hit["path"] for hit in enumerated)

    # Body-less query + path filter degrades to suite enumeration, because
    # identifiers such as ``test_filter_by_status`` are single FTS tokens.
    fallback = store.search("target", kinds=["test"], path_contains="/target/")
    assert fallback and all("/target/" in hit["path"] for hit in fallback)

    # Default (no path filter) is unchanged: both suites are visible.
    both = store.search("orders", kinds=["test"])
    assert len({hit["path"] for hit in both}) == 2


def test_list_chunks_enumerates_deterministically(store: MaterialStore) -> None:
    store.upsert(
        [
            _chunk("test", "def test_a():\n    assert True", path="demo/tests/regression/b.py", line_start=10),
            _chunk("test", "def test_b():\n    assert True", path="demo/tests/regression/a.py", line_start=5),
            _chunk("test", "def test_c():\n    assert True", path="demo/tests/target/c.py", line_start=1),
            _chunk("spec", "### REQ-ORD-001 列表分页", path="demo/specs/orders.md"),
        ]
    )

    regression = store.list_chunks(["test"], path_contains="/regression/")
    assert [hit["path"] for hit in regression] == [
        "demo/tests/regression/a.py",
        "demo/tests/regression/b.py",
    ]
    assert all(hit["score"] == 0.0 for hit in regression)
    assert all(
        hit["citation"] == f"{hit['path']}:{hit['line_start']}-{hit['line_end']}"
        for hit in regression
    )
    assert all(hit["body"] for hit in regression)
    assert len(store.list_chunks(["test"], limit=1)) == 1

    every_kind = store.list_chunks(list(DOC_KINDS))
    assert {hit["kind"] for hit in every_kind} == {"spec", "test"}

    with pytest.raises(ValueError, match="unknown document kind"):
        store.list_chunks(["nope"])


def test_hits_include_raw_body_for_context_excerpts(store: MaterialStore) -> None:
    chunk = _chunk("spec", "### REQ-ORD-006 状态筛选\n原文正文，用于 context excerpt。", path="demo/specs/orders.md")
    store.upsert([chunk])
    hit = store.search("状态筛选", kinds=["spec"])[0]
    assert hit["body"] == chunk["body"]
    assert len(hit["snippet"]) <= SNIPPET_CHARS


def test_search_rejects_unknown_kind(store: MaterialStore) -> None:
    with pytest.raises(ValueError, match="unknown document kind"):
        store.search("订单", kinds=["nope"])


def test_search_limit_is_honoured(store: MaterialStore) -> None:
    store.upsert(
        [
            _chunk("spec", f"订单 第{i} 条说明", path=f"demo/specs/note-{i}.md", line_start=1)
            for i in range(5)
        ]
    )
    assert len(store.search("订单", kinds=["spec"], limit=2)) == 2


# ---------------------------------------------------------------------------
# citation correctness against the real demo tree
# ---------------------------------------------------------------------------
def test_citation_matches_real_file_lines(store: MaterialStore) -> None:
    chunks = collect_chunks(DEMO_ROOT)
    target = next(
        chunk
        for chunk in chunks
        if chunk["kind"] == "code" and chunk["symbol"] == "list_orders"
    )
    assert store.upsert([target]) == 1

    hits = store.search("list_orders", kinds=["code"])
    assert hits, "代码符号检索应命中 list_orders"
    hit = hits[0]
    assert hit["path"] == target["path"] == "demo/app/service.py"
    assert (hit["line_start"], hit["line_end"]) == (
        target["line_start"],
        target["line_end"],
    )
    assert hit["citation"] == f"demo/app/service.py:{target['line_start']}-{target['line_end']}"

    lines = (MINIMAX_ROOT / hit["path"]).read_text(encoding="utf-8").splitlines()
    first = lines[hit["line_start"] - 1]
    assert first.lstrip().startswith("def list_orders"), first
    source = "\n".join(lines[hit["line_start"] - 1 : hit["line_end"]])
    assert source == target["body"]
    # ``list_orders`` implements REQ-ORD-001 in both the v1 baseline and the
    # v2 target implementation, so this stays valid whichever demo revision is
    # checked out when the tests run.
    assert "REQ-ORD-001" in source


# ---------------------------------------------------------------------------
# embedded route (optional: only when pylibseekdb is installed)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    importlib.util.find_spec("pylibseekdb") is None,
    reason="pylibseekdb 未安装（仅 Linux/macOS）；本次运行跳过嵌入式路线",
)
def test_embedded_route_roundtrip(tmp_path: Path) -> None:
    embedded_cfg = SeekDBConfig(
        host="127.0.0.1",
        port=1,  # never used in embedded mode
        user="root",
        password="",
        database="sddctx_test",
        embedded_dir=tmp_path / "seekdb",
    )
    material = MaterialStore.from_config(embedded_cfg)
    try:
        material.init_schema()
        material.upsert(
            [_chunk("spec", "### REQ-ORD-006 状态筛选\n嵌入式 seekdb 中文检索验证。", path="demo/specs/orders.md")]
        )
        hits = material.search("中文检索", kinds=["spec"])
        assert hits and hits[0]["path"] == "demo/specs/orders.md"
        assert material.stats() == {"spec": 1, "code": 0, "test": 0}
    finally:
        material.close()
