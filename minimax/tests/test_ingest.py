"""Tests for :mod:`sddctx.ingest` — chunking rules and end-to-end ingestion.

The chunking tests are pure filesystem work; the ingestion tests need a
reachable seekdb server and use the dedicated ``sddctx_test`` database.
"""

from __future__ import annotations

import json
import os
import re
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

from sddctx.config import MINIMAX_ROOT, SeekDBConfig
from sddctx.ingest import collect_chunks, ingest, minimax_root
from sddctx.protocols import Chunk
from sddctx.store import MaterialStore

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


REQUIRES_SEEKDB = pytest.mark.skipif(
    not _seekdb_reachable(),
    reason=f"seekdb server 未监听 {HOST}:{PORT}；先执行 `systemctl start seekdb`",
)


@pytest.fixture(scope="session")
def demo_root() -> Path:
    assert (DEMO_ROOT / "specs" / "orders.md").is_file(), f"demo tree missing at {DEMO_ROOT}"
    return DEMO_ROOT


@pytest.fixture(scope="session")
def chunks(demo_root: Path) -> list[Chunk]:
    return collect_chunks(demo_root)


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


def _find(chunks: list[Chunk], *, kind: str, symbol: str) -> Chunk:
    for chunk in chunks:
        if chunk["kind"] == kind and chunk["symbol"] == symbol:
            return chunk
    raise AssertionError(f"missing chunk kind={kind} symbol={symbol}")


def _file_lines(relative_path: str) -> list[str]:
    return (MINIMAX_ROOT / relative_path).read_text(encoding="utf-8").splitlines()


# ---------------------------------------------------------------------------
# chunking (no database needed)
# ---------------------------------------------------------------------------
def test_collect_chunks_covers_all_three_kinds(chunks: list[Chunk]) -> None:
    by_kind = {kind: [c for c in chunks if c["kind"] == kind] for kind in ("spec", "code", "test")}
    # demo/specs/orders.md has exactly 9 REQ sections; history/ is excluded.
    assert len(by_kind["spec"]) == 9, "默认只导入 demo/specs/*.md（不含 history/）"
    assert len(by_kind["code"]) >= 10
    assert len(by_kind["test"]) >= 18
    assert all(by_kind.values())


def test_history_spec_is_excluded_by_default(chunks: list[Chunk]) -> None:
    """The superseded v1 spec must not be ingested by the default run."""

    specs = [c for c in chunks if c["kind"] == "spec"]
    assert {c["path"] for c in specs} == {"demo/specs/orders.md"}
    assert {c["symbol"] for c in specs} == {f"REQ-ORD-{index:03d}" for index in range(1, 10)}
    assert not any("history" in c["path"] for c in chunks)


def test_explicit_history_specs_dir_is_not_recursive(demo_root: Path) -> None:
    """Passing an explicit specs dir imports only ``*.md`` in that dir."""

    history_dir = demo_root / "specs" / "history"
    assert (history_dir / "orders.v1.md").is_file()
    collected = collect_chunks(demo_root, specs=history_dir)
    specs = [c for c in collected if c["kind"] == "spec"]
    assert len(specs) == 5, "history/orders.v1.md 有 5 条 REQ（v1）"
    assert {c["symbol"] for c in specs} == {f"REQ-ORD-{index:03d}" for index in range(1, 6)}
    assert all(c["path"].startswith("demo/specs/history/") for c in specs)


def test_spec_chunks_are_req_sections(chunks: list[Chunk]) -> None:
    specs = [c for c in chunks if c["kind"] == "spec"]
    assert all(c["body"].lstrip().startswith("### REQ-") for c in specs)
    req = _find(chunks, kind="spec", symbol="REQ-ORD-006")
    assert req["path"] == "demo/specs/orders.md"
    assert "状态筛选" in req["title"]
    lines = _file_lines(req["path"])
    assert lines[req["line_start"] - 1].startswith("### REQ-ORD-006")
    assert "status" in req["body"]
    assert "REQ-ORD-007" not in req["body"], "REQ 小节不得越过下一个标题"
    assert len(req["body"].splitlines()) == req["line_end"] - req["line_start"] + 1


def test_spec_chunks_do_not_overlap(chunks: list[Chunk]) -> None:
    by_path: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        if chunk["kind"] == "spec":
            by_path.setdefault(chunk["path"], []).append(chunk)
    for path, items in by_path.items():
        ordered = sorted(items, key=lambda c: c["line_start"])
        for previous, current in zip(ordered, ordered[1:]):
            assert current["line_start"] > previous["line_end"], (path, previous, current)


def test_code_chunks_cover_top_level_symbols(chunks: list[Chunk]) -> None:
    symbols = {c["symbol"] for c in chunks if c["kind"] == "code"}
    assert {"list_orders", "parse_pagination", "parse_sort", "OrderRepository"} <= symbols
    code = [c for c in chunks if c["kind"] == "code"]
    assert all(c["path"].startswith("demo/app/") for c in code)
    assert all("\n" not in c["path"] for c in code)

    target = _find(chunks, kind="code", symbol="list_orders")
    lines = _file_lines(target["path"])
    assert lines[target["line_start"] - 1].startswith("def list_orders")
    assert target["body"].startswith("def list_orders")
    assert target["body"] == "\n".join(lines[target["line_start"] - 1 : target["line_end"]])


def test_code_chunks_include_decorators(chunks: list[Chunk]) -> None:
    route = _find(chunks, kind="code", symbol="list_orders_route")
    assert route["path"] == "demo/app/routes.py"
    assert route["body"].startswith('@router.get("/orders")')
    lines = _file_lines(route["path"])
    assert lines[route["line_start"] - 1].startswith('@router.get("/orders")')


def test_test_chunks_use_docstring_titles(chunks: list[Chunk]) -> None:
    symbols = {c["symbol"] for c in chunks if c["kind"] == "test"}
    assert {"test_filter_by_status", "test_filters_compose", "test_health_endpoint"} <= symbols
    tests = [c for c in chunks if c["kind"] == "test"]
    assert all(c["path"].startswith("demo/tests/") for c in tests)

    target = _find(chunks, kind="test", symbol="test_filter_by_status")
    assert target["path"] == "demo/tests/target/test_orders_filters_target.py"
    assert target["title"].startswith("REQ-ORD-006")
    lines = _file_lines(target["path"])
    assert lines[target["line_start"] - 1].startswith("def test_filter_by_status")
    assert target["body"] == "\n".join(lines[target["line_start"] - 1 : target["line_end"]])


def test_paths_are_posix_and_relative(chunks: list[Chunk]) -> None:
    for chunk in chunks:
        assert chunk["path"].startswith("demo/"), chunk["path"]
        assert "\\" not in chunk["path"], chunk["path"]
        assert not chunk["path"].startswith("/"), chunk["path"]
        assert chunk["line_start"] >= 1
        assert chunk["line_end"] >= chunk["line_start"]
        assert chunk["body"].strip()


def test_no_duplicate_citations(chunks: list[Chunk]) -> None:
    citations = [(c["path"], c["line_start"]) for c in chunks]
    assert len(citations) == len(set(citations))


def test_bodies_are_verbatim_source(chunks: list[Chunk]) -> None:
    for chunk in chunks:
        if chunk["kind"] == "spec":
            continue
        lines = _file_lines(chunk["path"])
        assert chunk["body"] == "\n".join(lines[chunk["line_start"] - 1 : chunk["line_end"]]), (
            chunk["path"],
            chunk["line_start"],
            chunk["line_end"],
        )


def test_minimax_root_helper(demo_root: Path) -> None:
    assert minimax_root(demo_root) == MINIMAX_ROOT


# ---------------------------------------------------------------------------
# ingestion (needs seekdb)
# ---------------------------------------------------------------------------
@REQUIRES_SEEKDB
def test_ingest_writes_jsonl_and_counts(
    store: MaterialStore, demo_root: Path, tmp_path: Path
) -> None:
    # Collect inside the test (instead of the session fixture) so a concurrent
    # edit of the demo tree cannot desynchronise the expected chunk list.
    chunks = collect_chunks(demo_root)
    result = ingest(store, demo_root, archive_dir=tmp_path)

    assert set(result) == {"chunks", "counts", "archive"}
    assert result["chunks"] == len(chunks)
    assert result["counts"]["spec"] == 9
    assert sum(result["counts"].values()) == len(chunks)
    assert result["counts"]["spec"] == len([c for c in chunks if c["kind"] == "spec"])
    assert store.stats() == result["counts"]

    archive = Path(result["archive"])
    assert archive.is_file()
    assert archive.parent == tmp_path
    assert re.fullmatch(r"ingest-\d{8}T\d{6}Z\.jsonl", archive.name), archive.name

    lines = archive.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(chunks)
    first = json.loads(lines[0])
    assert set(first) == {"kind", "path", "line_start", "line_end", "title", "symbol", "body"}
    assert "状态筛选" in archive.read_text(encoding="utf-8"), "JSONL 必须保留中文（ensure_ascii=False）"
    assert "orders.v1.md" not in archive.read_text(encoding="utf-8"), "history/ 规格不应出现在快照里"


@REQUIRES_SEEKDB
def test_ingest_is_idempotent(store: MaterialStore, demo_root: Path, tmp_path: Path) -> None:
    chunks = collect_chunks(demo_root)
    first = ingest(store, demo_root, archive_dir=tmp_path)
    stats_first = store.stats()
    second = ingest(store, demo_root, archive_dir=tmp_path)
    assert second["chunks"] == first["chunks"] == len(chunks)
    assert store.stats() == stats_first
    assert sum(store.stats().values()) == len(chunks)


@REQUIRES_SEEKDB
def test_search_after_ingest_cites_real_spec_lines(store: MaterialStore, demo_root: Path, tmp_path: Path) -> None:
    ingest(store, demo_root, archive_dir=tmp_path)

    hits = store.search("状态筛选", kinds=["spec"], limit=5)
    assert hits, "导入后中文检索必须命中 REQ-ORD-006"
    hit = hits[0]
    assert hit["path"] == "demo/specs/orders.md"
    lines = _file_lines(hit["path"])
    cited = "\n".join(lines[hit["line_start"] - 1 : hit["line_end"]])
    assert "REQ-ORD-006" in cited and "状态筛选" in cited
    assert hit["citation"] == f"{hit['path']}:{hit['line_start']}-{hit['line_end']}"


@REQUIRES_SEEKDB
def test_search_after_ingest_finds_code_and_test(store: MaterialStore, demo_root: Path, tmp_path: Path) -> None:
    ingest(store, demo_root, archive_dir=tmp_path)

    code_hits = store.search("list_orders", kinds=["code"], limit=5)
    assert code_hits
    assert any(hit["path"] == "demo/app/service.py" for hit in code_hits)

    test_hits = store.search("status", kinds=["test"], limit=5)
    assert test_hits
    assert any(hit["path"].startswith("demo/tests/") for hit in test_hits)


@REQUIRES_SEEKDB
def test_ingest_keeps_both_test_suites_selectable_by_path(
    store: MaterialStore, demo_root: Path, tmp_path: Path
) -> None:
    """Context assembly uses ``path_contains`` to get target + regression tests."""

    ingest(store, demo_root, archive_dir=tmp_path)

    # ``list_chunks`` is the deterministic suite enumeration used by the
    # context assembler: the whole suite, no keyword luck involved.
    target = store.list_chunks(["test"], path_contains="/target/", limit=100)
    regression = store.list_chunks(["test"], path_contains="/regression/", limit=100)
    assert len(target) == 6, f"target 套件应有 6 条测试，实际 {len(target)}"
    assert len(regression) == 18, f"regression 套件应有 18 条测试，实际 {len(regression)}"
    assert all("/target/" in hit["path"] for hit in target)
    assert all("/regression/" in hit["path"] for hit in regression)
    assert all(hit["score"] == 0.0 for hit in target + regression)
    assert any(hit["symbol"] == "test_filter_by_status" for hit in target)
    assert any(hit["symbol"] == "test_caller_isolation" for hit in regression)
    # Enumeration is ordered by path, then line — deterministic across runs.
    assert [(hit["path"], hit["line_start"]) for hit in regression] == sorted(
        (hit["path"], hit["line_start"]) for hit in regression
    )

    # FTS + path_contains keeps working as a narrowing search.
    matched = store.search("status", kinds=["test"], path_contains="/regression/", limit=40)
    assert matched and all("/regression/" in hit["path"] for hit in matched)

    # A query with no FTS match still degrades to suite enumeration.
    fallback = store.search("regression", kinds=["test"], path_contains="/regression/", limit=40)
    assert fallback and all("/regression/" in hit["path"] for hit in fallback)


@REQUIRES_SEEKDB
def test_ingest_honours_specs_override(
    store: MaterialStore, demo_root: Path, tmp_path: Path
) -> None:
    custom_specs = tmp_path / "custom-specs"
    custom_specs.mkdir()
    (custom_specs / "custom.md").write_text(
        "# 自定义规格\n\n### REQ-CUSTOM-001 自定义规格\n只包含一条自定义需求。\n",
        encoding="utf-8",
    )

    result = ingest(store, demo_root, specs=custom_specs, archive_dir=tmp_path)
    assert result["counts"]["spec"] == 1

    hits = store.search("自定义需求", kinds=["spec"])
    assert hits
    assert hits[0]["path"].endswith("custom.md")
