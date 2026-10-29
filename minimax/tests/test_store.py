"""Tests for :mod:`sddctx.store` against a real seekdb server.

The server tests require a reachable seekdb (MySQL protocol) and are skipped
with a clear reason when it is not running.  They use a dedicated per-process database (``sddctx_test_<pid>``, override with
``SDDCTX_TEST_DB``) and never touch the ``sddctx`` / demo databases.

The embedded tests do **not** need a server: they open a local
``pylibseekdb`` runtime in ``tmp_path`` and are skipped only when that optional
dependency is absent.
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
# unique per pytest process so concurrent runs cannot disturb each other's data
TEST_DB = os.environ.get("SDDCTX_TEST_DB") or f"sddctx_test_{os.getpid()}"
DEMO_ROOT = MINIMAX_ROOT / "demo"


def _seekdb_reachable() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=1.0):
            return True
    except OSError:
        return False


def _requires_seekdb() -> None:
    if not _seekdb_reachable():
        pytest.skip(f"seekdb server 未监听 {HOST}:{PORT}；先执行 `systemctl start seekdb`")


@pytest.fixture(scope="session")
def cfg() -> SeekDBConfig:
    return SeekDBConfig(
        host=HOST, port=PORT, user="root", password="", database=TEST_DB, embedded_dir=None
    )


@pytest.fixture()
def store(cfg: SeekDBConfig) -> Iterator[MaterialStore]:
    _requires_seekdb()
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
    # The chunk must quote the real function source verbatim (asserted above);
    # the docstring wording depends on the implementation revision, so only the
    # definition itself is checked here.
    assert "def list_orders" in source


# ---------------------------------------------------------------------------
# boundary / validation (V1 audit additions)
# ---------------------------------------------------------------------------
def test_search_rejects_non_positive_limit(store: MaterialStore) -> None:
    with pytest.raises(ValueError, match="limit must be positive"):
        store.search("订单", kinds=["spec"], limit=0)
    with pytest.raises(ValueError, match="limit must be positive"):
        store.list_chunks(["spec"], limit=-1)


def test_reset_rejects_empty_or_unknown_kinds(store: MaterialStore) -> None:
    with pytest.raises(ValueError, match="kinds must not be empty"):
        store.reset([])
    with pytest.raises(ValueError, match="unknown document kind"):
        store.reset(["bogus"])
    assert store.stats() == {"spec": 0, "code": 0, "test": 0}


def test_upsert_rejects_line_range_mismatch(store: MaterialStore) -> None:
    bad = Chunk(
        kind="code",
        path="demo/app/bad.py",
        line_start=10,
        line_end=20,
        title="bad",
        symbol="bad",
        body="def bad(): pass",  # 1 line, but the range claims 11
    )
    with pytest.raises(ValueError, match="body/line range mismatch"):
        store.upsert([bad])
    assert store.stats() == {"spec": 0, "code": 0, "test": 0}


def test_upsert_validates_batch_before_writing(store: MaterialStore) -> None:
    good = _chunk("spec", "订单查询规格", path="demo/specs/orders.md")
    bad = Chunk(
        kind="spec", path="demo/specs/bad.md", line_start=5, line_end=9,
        title="bad", symbol="bad", body="### REQ-ORD-999 只有一行",
    )
    with pytest.raises(ValueError, match="body/line range mismatch"):
        store.upsert([good, bad])
    assert store.stats() == {"spec": 0, "code": 0, "test": 0}, "校验失败时不得写半批数据"


def test_upsert_normalises_windows_paths(store: MaterialStore) -> None:
    store.upsert(
        [
            Chunk(
                kind="code",
                path="demo\\app\\service.py",
                line_start=1,
                line_end=1,
                title="list_orders",
                symbol="list_orders",
                body="def list_orders(): pass",
            )
        ]
    )
    hits = store.list_chunks(["code"])
    assert [hit["path"] for hit in hits] == ["demo/app/service.py"]
    assert hits[0]["citation"] == "demo/app/service.py:1-1"
    assert "\\" not in hits[0]["citation"]


def test_upsert_rejects_overlong_path(store: MaterialStore) -> None:
    long_path = "demo/" + "x" * 600 + ".py"
    with pytest.raises(ValueError, match="path too long"):
        store.upsert([_chunk("code", "def f(): pass", path=long_path)])
    assert store.stats() == {"spec": 0, "code": 0, "test": 0}


def test_upsert_rejects_control_chars_in_path(store: MaterialStore) -> None:
    with pytest.raises(ValueError, match="control characters"):
        store.upsert([_chunk("code", "def f(): pass", path="demo/app/x\n.py")])


def test_invalid_database_name_fails_fast() -> None:
    """Database names are interpolated into identifiers -> strict allow-list."""

    bad = SeekDBConfig(host="127.0.0.1", port=1, user="root", password="", database="x`; DROP TABLE y; --")
    material = MaterialStore.from_config(bad)
    try:
        with pytest.raises(StoreError, match="非法 seekdb 库名"):
            material.init_schema()
    finally:
        material.close()


def test_upsert_empty_iterable_is_noop(store: MaterialStore) -> None:
    assert store.upsert([]) == 0
    assert store.stats() == {"spec": 0, "code": 0, "test": 0}


def test_duplicate_chunks_in_one_batch_last_wins(store: MaterialStore) -> None:
    first = _chunk("spec", "订单 第一版", path="demo/specs/dup.md")
    second = _chunk("spec", "订单 第二版", path="demo/specs/dup.md")
    assert store.upsert([first, second]) == 1
    stored = store.list_chunks(["spec"])
    assert len(stored) == 1
    assert stored[0]["body"] == "订单 第二版"


def test_path_contains_escapes_like_wildcards(store: MaterialStore) -> None:
    store.upsert(
        [
            _chunk("test", "def test_a(): pass", path="demo/tests/100%_done/test_a.py"),
            _chunk("test", "def test_b(): pass", path="demo/tests/other/test_b.py"),
        ]
    )
    hits = store.list_chunks(["test"], path_contains="100%_done")
    assert [hit["path"] for hit in hits] == ["demo/tests/100%_done/test_a.py"]
    assert store.search("", kinds=["test"], path_contains="50%") == []


def test_long_body_is_stored_and_snippeted(store: MaterialStore) -> None:
    lines = [f"订单筛选规格 line {index}；seekdb 全文索引。" for index in range(3000)]
    body = "\n".join(lines)
    assert len(body) > 60_000
    assert store.upsert([_chunk("spec", body, path="demo/specs/big.md")]) == 1
    hits = store.search("订单筛选", kinds=["spec"], limit=1)
    assert hits and len(hits[0]["snippet"]) <= SNIPPET_CHARS
    assert hits[0]["body"] == body, "大 body 必须原样 round-trip"


def test_multibyte_text_and_title_clamp(store: MaterialStore) -> None:
    title = "😀" * 300  # 300 chars, 1200 bytes
    store.upsert(
        [
            Chunk(
                kind="spec",
                path="demo/specs/emoji.md",
                line_start=1,
                line_end=1,
                title=title,
                symbol="S" * 300,
                body="订单😀🚀 状态 ✓ e\u0301",
            )
        ]
    )
    stored = store.list_chunks(["spec"])[0]
    assert len(stored["title"]) == 255 and len(stored["symbol"]) == 255
    assert stored["body"] == "订单😀🚀 状态 ✓ e\u0301"
    assert store.search("订单", kinds=["spec"]), "多字节正文必须可检索"


def test_absolute_and_relative_paths_are_distinct(store: MaterialStore, tmp_path: Path) -> None:
    absolute = (tmp_path / "specs" / "a.md").as_posix()
    store.upsert(
        [
            _chunk("spec", "订单 相对路径", path="demo/specs/a.md"),
            _chunk("spec", "订单 绝对路径", path=absolute),
        ]
    )
    paths = [hit["path"] for hit in store.list_chunks(["spec"])]
    assert sorted(paths) == sorted(["demo/specs/a.md", absolute])
    absolute_hit = next(hit for hit in store.list_chunks(["spec"]) if hit["path"] == absolute)
    assert absolute_hit["citation"].startswith(absolute + ":1-1")


def test_close_is_reconnectable(store: MaterialStore) -> None:
    store.close()
    assert store.stats() == {"spec": 0, "code": 0, "test": 0}  # 自动重连


# ---------------------------------------------------------------------------
# embedded route (optional: only when pylibseekdb is installed)
# ---------------------------------------------------------------------------
@pytest.mark.skipif(
    importlib.util.find_spec("pylibseekdb") is None,
    reason="pylibseekdb 未安装（仅 Linux/macOS）；本次运行跳过嵌入式路线",
)
def test_embedded_route_roundtrip(tmp_path: Path) -> None:
    """The embedded seekdb route must work end-to-end without any server."""

    embedded_dir = tmp_path / "seekdb"
    embedded_cfg = SeekDBConfig(
        host="127.0.0.1",
        port=1,  # 故意不可用：嵌入式路线绝不能回落到 server
        user="root",
        password="",
        database="sddctx_test",
        embedded_dir=embedded_dir,
    )
    material = MaterialStore.from_config(embedded_cfg)
    assert material.embedded
    try:
        material.init_schema()
        assert embedded_dir.is_dir(), "嵌入式数据目录应被创建"
        material.reset()

        assert (
            material.upsert(
                [
                    _chunk(
                        "spec",
                        "### REQ-ORD-006 状态筛选\n支持可选查询参数 status，只返回该状态的订单。",
                        path="demo/specs/orders.md",
                    ),
                    _chunk("code", "def list_orders(repo):\n    return repo.orders()", path="demo/app/service.py"),
                    _chunk("test", "def test_x():\n    assert True", path="demo/tests/target/test_x.py"),
                ]
            )
            == 3
        )
        assert material.stats() == {"spec": 1, "code": 1, "test": 1}

        # 中文 FTS（ik parser）必须在嵌入式引擎上工作
        hits = material.search("状态筛选", kinds=["spec"])
        assert hits and hits[0]["symbol"] == "spec"
        assert hits[0]["score"] > 0
        assert hits[0]["citation"] == "demo/specs/orders.md:1-2"
        assert len(hits[0]["snippet"]) <= SNIPPET_CHARS

        # 确定性枚举 + path_contains（无参数绑定的内联路径）
        assert [hit["path"] for hit in material.list_chunks(["test"])] == [
            "demo/tests/target/test_x.py"
        ]
        narrowed = material.search("", kinds=["test"], path_contains="/target/")
        assert narrowed and all("/target/" in hit["path"] for hit in narrowed)

        # DDL 必须是 ik parser（与 server 路线同一份 DDL）
        ddl = str(material._fetch(f"SHOW CREATE TABLE {material.table}")[0][1])
        assert "PARSER ik" in ddl

        # 关闭后重开：数据仍在（持久化）
        material.close()
        reopened = MaterialStore.from_config(embedded_cfg)
        try:
            reopened.init_schema()
            assert reopened.stats() == {"spec": 1, "code": 1, "test": 1}
            assert reopened.search("状态筛选", kinds=["spec"])[0]["citation"] == (
                "demo/specs/orders.md:1-2"
            )
            assert reopened.reset(["code"]) == 1
            assert reopened.stats() == {"spec": 1, "code": 0, "test": 1}
        finally:
            reopened.close()
    finally:
        material.close()
