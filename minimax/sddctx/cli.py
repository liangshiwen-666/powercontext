"""Command line interface for the SDD context toolkit.

Entry point::

    python -m sddctx <command> [options]

Commands
--------
init        create the seekdb schema (idempotent)
ingest      import specs / code / tests into seekdb
context     build the development context pack for a task
state       show or save PowerContext task state
patch       generate (live) / apply / inspect a unified-diff patch
verify      run the before/after test matrix and write evidence
run         end-to-end demo: ingest -> context -> patch -> verify -> state
reset       restore the business project to the pre-change state
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from . import verify as verifymod
from .config import ToolkitConfig
from .context import build_context
from .ingest import ingest as ingest_materials
from .minimax_client import MiniMaxClient, build_edit_messages, extract_file_writes
from .patch import apply_patch, extract_unified_diff, reverse_patch
from .pcstore import TaskStateStore
from .store import MaterialStore, StoreError

DEFAULT_SCOPE_KEY = "sddctx-orders-filters-v1"
DEFAULT_PATCH_NAME = "0001-orders-filters.patch"
DEFAULT_TASK = "demo/tasks/orders-filters.task.md"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _cfg() -> ToolkitConfig:
    cfg = ToolkitConfig.from_env()
    cfg.ensure_dirs()
    return cfg


def _store(cfg: ToolkitConfig) -> MaterialStore:
    store = MaterialStore.from_config(cfg.seekdb)
    store.init_schema()
    return store


def _state(cfg: ToolkitConfig, scope_key: str | None) -> TaskStateStore:
    return TaskStateStore(cfg.pc_db_path, scope_key=scope_key or DEFAULT_SCOPE_KEY)


def _task_path(cfg: ToolkitConfig, raw: str | None) -> Path:
    path = Path(raw) if raw else Path(cfg.root / DEFAULT_TASK)
    return path if path.is_absolute() else (cfg.root / path)


def _patch_path(cfg: ToolkitConfig, name: str | None) -> Path:
    path = Path(name) if name else (cfg.patches_dir / DEFAULT_PATCH_NAME)
    return path if path.is_absolute() else (cfg.patches_dir / path)


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _rel(cfg: ToolkitConfig, value: Any) -> str:
    """Render a path relative to the ``minimax/`` root when possible."""

    if value is None:
        return ""
    try:
        return cfg.relpath(Path(str(value)))
    except Exception:  # evidence rendering must never fail
        return str(value)


def _sorted_records(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """PowerContext returns records unordered; sort them for reproducible evidence."""

    return sorted(
        records,
        key=lambda item: (str(item.get("kind") or ""), str(item.get("text") or "")),
    )


def _save_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _context_for(cfg: ToolkitConfig, store: MaterialStore, state: TaskStateStore, task_path: Path) -> dict[str, Any]:
    return build_context(store=store, state_store=state, task_path=task_path, root=cfg.root)


def _save_context(cfg: ToolkitConfig, pack: dict[str, Any], label: str) -> Path:
    out = cfg.evidence_dir / "context" / f"{label}-{_stamp()}.json"
    return _save_json(out, pack)


def _collect_db_evidence(cfg: ToolkitConfig, label: str) -> Path:
    """Record the real seekdb DDL / counts / retrieval queries used for the context."""

    import pymysql

    db = cfg.seekdb
    queries: list[dict[str, Any]] = []
    statements = [
        ("schema", "SHOW CREATE TABLE ctx_doc"),
        ("counts_by_kind", "SELECT kind, COUNT(*) AS chunks FROM ctx_doc GROUP BY kind ORDER BY kind"),
        (
            "retrieval_specs",
            "SELECT kind, CONCAT(path, ':', line_start, '-', line_end) AS citation, "
            "ROUND(MATCH(body) AGAINST('REQ-ORD-006 筛选 状态'), 4) AS score "
            "FROM ctx_doc WHERE MATCH(body) AGAINST('REQ-ORD-006 筛选 状态') AND kind IN ('spec') "
            "ORDER BY score DESC LIMIT 10",
        ),
        (
            "retrieval_code",
            "SELECT kind, CONCAT(path, ':', line_start, '-', line_end) AS citation, "
            "ROUND(MATCH(body) AGAINST('list_orders parse_pagination status created_after min_amount'), 4) AS score "
            "FROM ctx_doc WHERE MATCH(body) AGAINST('list_orders parse_pagination status created_after min_amount') "
            "AND kind = 'code' ORDER BY score DESC LIMIT 5",
        ),
        (
            "retrieval_tests",
            "SELECT kind, CONCAT(path, ':', line_start, '-', line_end) AS citation, "
            "ROUND(MATCH(body) AGAINST('test_filter_by_status test_filters_compose test_filter_by_min_amount'), 4) AS score "
            "FROM ctx_doc WHERE MATCH(body) AGAINST('test_filter_by_status test_filters_compose test_filter_by_min_amount') "
            "AND kind = 'test' ORDER BY score DESC LIMIT 5",
        ),
        (
            # one statement that returns spec + code + test rows: each UNION branch
            # uses the keywords that actually hit its own document kind (a shared
            # keyword set only matches specs for this task, see docs/audit-v1.md §2)
            "retrieval_all_kinds",
            "SELECT kind, citation, score FROM ("
            "  (SELECT kind, CONCAT(path, ':', line_start, '-', line_end) AS citation, "
            "     ROUND(MATCH(body) AGAINST('筛选 状态 时间范围 金额下限'), 4) AS score "
            "   FROM ctx_doc WHERE MATCH(body) AGAINST('筛选 状态 时间范围 金额下限') "
            "   AND kind = 'spec' ORDER BY score DESC LIMIT 2) "
            "  UNION ALL "
            "  (SELECT kind, CONCAT(path, ':', line_start, '-', line_end) AS citation, "
            "     ROUND(MATCH(body) AGAINST('list_orders parse_pagination status created_after min_amount'), 4) AS score "
            "   FROM ctx_doc WHERE MATCH(body) AGAINST('list_orders parse_pagination status created_after min_amount') "
            "   AND kind = 'code' ORDER BY score DESC LIMIT 2) "
            "  UNION ALL "
            "  (SELECT kind, CONCAT(path, ':', line_start, '-', line_end) AS citation, "
            "     ROUND(MATCH(body) AGAINST('test_filter_by_status test_filters_compose test_filter_by_min_amount'), 4) AS score "
            "   FROM ctx_doc WHERE MATCH(body) AGAINST('test_filter_by_status test_filters_compose test_filter_by_min_amount') "
            "   AND kind = 'test' ORDER BY score DESC LIMIT 2) "
            ") AS merged ORDER BY kind, score DESC",
        ),
    ]

    connection = None
    connection_error: str | None = None
    try:
        connection = pymysql.connect(
            host=db.host,
            port=db.port,
            user=db.user,
            password=db.password,
            database=db.database,
            charset="utf8mb4",
            connect_timeout=10,
        )
    except Exception as exc:  # recorded as evidence; the command reports the failure
        connection_error = f"{type(exc).__name__}: {exc}"

    try:
        for name, sql in statements:
            if connection is None:
                queries.append({"name": name, "sql": sql, "error": connection_error})
                continue
            try:
                with connection.cursor() as cur:
                    cur.execute(sql)
                    rows = cur.fetchall() if cur.description else []
                    columns = [col[0] for col in cur.description] if cur.description else []
                queries.append(
                    {
                        "name": name,
                        "sql": sql,
                        "columns": columns,
                        "rows": [list(row) for row in rows],
                        "row_count": len(rows),
                    }
                )
            except Exception as exc:  # recorded as evidence
                queries.append({"name": name, "sql": sql, "error": f"{type(exc).__name__}: {exc}"})
    finally:
        if connection is not None:
            connection.close()

    payload = {
        "schema": "sddctx.seekdb-evidence/v1",
        "generated_at": _stamp(),
        "connection": {"host": db.host, "port": db.port, "database": db.database, "table": "ctx_doc"},
        "engine": "seekdb 1.4.0 (MySQL protocol)",
        "queries": queries,
    }
    out = cfg.records_dir / "db" / f"seekdb-queries-{label}-{_stamp()}.json"
    return _save_json(out, payload)


def _export_state_evidence(cfg: ToolkitConfig, state: TaskStateStore, label: str) -> Path:
    payload = {
        "schema": "sddctx.powercontext-evidence/v1",
        "generated_at": _stamp(),
        "db_path": _rel(cfg, cfg.pc_db_path),
        "scope_key": getattr(state, "scope_key", None),
        "records": _sorted_records(state.list()),
        "prepared": state.prepare("补丁 测试 验证 状态"),
    }
    out = cfg.records_dir / "powercontext" / f"state-{label}-{_stamp()}.json"
    return _save_json(out, payload)


def cmd_db_evidence(args: argparse.Namespace) -> int:
    cfg = _cfg()
    out = _collect_db_evidence(cfg, "manual")
    payload = json.loads(out.read_text(encoding="utf-8"))
    print(f"seekdb evidence : {out}")
    failures = 0
    for query in payload["queries"]:
        status = "ERROR" if query.get("error") else f"{query.get('row_count', 0)} rows"
        print(f"  {query['name']:<20} {status}")
        if query.get("error"):
            failures += 1
            print(f"    {query['error']}")
    if failures:
        print(
            f"error: {failures}/{len(payload['queries'])} seekdb queries failed "
            "(check SEEKDB_HOST/PORT and that `sddctx ingest` has run)",
            file=sys.stderr,
        )
        return 1
    return 0


def _assert_patch_scope(patch_text: str) -> list[str]:
    """Refuse patches that touch anything outside the business ``app/`` package.

    The task explicitly forbids modifying the test suite; a model-generated
    patch must only change implementation files.  Returns the touched paths.
    """

    import re

    paths = set(re.findall(r"^(?:---|\+\+\+) [ab]/([^\t\n]+)", patch_text, flags=re.MULTILINE))
    paths = {path.strip() for path in paths if path.strip() and path.strip() != "/dev/null"}
    bad = sorted(path for path in paths if not path.startswith("app/"))
    if bad:
        raise SystemExit(
            "refusing to apply a patch that touches files outside app/ "
            f"(tests and specs are read-only): {bad}"
        )
    return sorted(paths)


def _git_head(path: Path) -> str | None:
    """Return the git HEAD of the enclosing repository (for evidence logs)."""

    import subprocess

    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _tree_state(cfg: ToolkitConfig, patch_text: str) -> str:
    """Return ``before`` or ``after`` for the business project tree."""

    applied = apply_patch(cfg.demo_root, patch_text, check_only=True)
    if applied.get("ok"):
        return "before"
    reversed_ = reverse_patch(cfg.demo_root, patch_text, check_only=True)
    if reversed_.get("ok"):
        return "after"
    return "unknown"


def _ensure_after(cfg: ToolkitConfig, patch_text: str) -> dict[str, Any]:
    state = _tree_state(cfg, patch_text)
    if state == "after":
        return {"changed": False, "state": "after"}
    if state == "before":
        result = apply_patch(cfg.demo_root, patch_text)
        return {"changed": bool(result.get("ok")), "state": "after" if result.get("ok") else "unknown", "result": result}
    raise SystemExit(
        "business project tree is in an unknown state: the saved patch neither applies nor reverses cleanly. "
        "Run `python -m sddctx reset --demo --force` to restore the pre-change state."
    )


def _ensure_before(cfg: ToolkitConfig, patch_text: str) -> dict[str, Any]:
    state = _tree_state(cfg, patch_text)
    if state == "before":
        return {"changed": False, "state": "before"}
    if state == "after":
        result = reverse_patch(cfg.demo_root, patch_text)
        return {"changed": bool(result.get("ok")), "state": "before" if result.get("ok") else "unknown", "result": result}
    raise SystemExit("business project tree is in an unknown state; run `python -m sddctx reset --demo --force`.")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_init(args: argparse.Namespace) -> int:
    cfg = _cfg()
    store = _store(cfg)
    print(f"seekdb: {cfg.seekdb.describe()}")
    print(f"schema ready; stats={store.stats()}")
    store.close()
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    cfg = _cfg()
    store = _store(cfg)
    specs = Path(args.specs).resolve() if args.specs else None
    result = ingest_materials(store, cfg.demo_root, specs=specs, archive_dir=cfg.data_dir / "ingest")
    if args.json:
        # machine-readable mode: stdout stays a single valid JSON document
        _print_json({**result, "store_stats": store.stats()})
    else:
        print(f"chunks ingested : {result['chunks']}")
        print(f"counts          : {json.dumps(result['counts'], ensure_ascii=False)}")
        print(f"archive         : {result['archive']}")
        print(f"store stats     : {json.dumps(store.stats(), ensure_ascii=False)}")
    store.close()
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    cfg = _cfg()
    store = _store(cfg)
    state = _state(cfg, args.scope_key)
    task_path = _task_path(cfg, args.task)
    pack = _context_for(cfg, store, state, task_path)
    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = cfg.root / out
        _save_json(out, pack)
    else:
        # default location is runtime data, not evidence: only the pipeline
        # (`sddctx run`) writes auditable packs into evidence/context/
        out = cfg.data_dir / "context" / f"{pack['task']['id']}-{_stamp()}.json"
        _save_json(out, pack)
    if not (pack["specs"] or pack["code"] or pack["tests"]):
        print(
            "warning: the context pack is empty — run `python -m sddctx ingest` first "
            "and check SEEKDB_HOST/SEEKDB_PORT",
            file=sys.stderr,
        )
    print(f"task            : {pack['task']['id']}  requirements={pack['task']['requirements']}")
    print(f"specs           : {len(pack['specs'])} entries (missing={pack['specs_missing']})")
    print(f"code            : {len(pack['code'])} entries; files={list(pack['file_contents'])}")
    print(f"tests           : {len(pack['tests'])} entries "
          f"(target={sum(1 for t in pack['tests'] if t['suite'] == 'target')}, "
          f"regression={sum(1 for t in pack['tests'] if t['suite'] == 'regression')})")
    print(f"state           : available={pack['state'].get('available')} records={pack['state'].get('record_count')}")
    print(f"context pack    : {out}")
    for entry in pack["specs"]:
        print(f"  spec  {entry.get('requirement_id') or '-':<14} {entry['citation']}")
    for entry in pack["code"]:
        print(f"  code  {entry['symbol']:<24} {entry['citation']}")
    for entry in pack["tests"]:
        print(f"  test  ({entry['suite']}) {entry['name']:<40} {entry['citation']}")
    if args.print_text:
        print("\n" + "=" * 78 + "\n" + pack["context_text"])
    store.close()
    return 0


def cmd_state_show(args: argparse.Namespace) -> int:
    cfg = _cfg()
    state = _state(cfg, args.scope_key)
    records = _sorted_records(state.list())
    prepared = state.prepare("补丁 测试 验证 状态")
    if args.json:
        # machine-readable mode: stdout stays a single valid JSON document
        _print_json(
            {
                "db_path": _rel(cfg, cfg.pc_db_path),
                "scope_key": args.scope_key or DEFAULT_SCOPE_KEY,
                "records": records,
                "prepared": prepared,
            }
        )
        return 0
    print(f"powercontext db  : {cfg.pc_db_path}")
    print(f"scope key        : {args.scope_key or DEFAULT_SCOPE_KEY}")
    print(f"records          : {len(records)}")
    for record in records:
        print(f"  [{record.get('kind')}] {record.get('text')}")
    print(f"prepared context : status={prepared.get('status')} bytes={prepared.get('bytes')}")
    return 0


def cmd_state_export(args: argparse.Namespace) -> int:
    """Export the persisted state from a *fresh process* (restart evidence)."""

    import os

    cfg = _cfg()
    state = _state(cfg, args.scope_key)
    payload = {
        "schema": "sddctx.state-readback/v1",
        "generated_at": _stamp(),
        "db_path": _rel(cfg, cfg.pc_db_path),
        "scope_key": args.scope_key or DEFAULT_SCOPE_KEY,
        "process": {"pid": os.getpid(), "python": sys.executable},
        "records": _sorted_records(state.list()),
        "prepared": state.prepare("补丁 测试 验证 状态"),
    }
    out = Path(args.out) if args.out else (cfg.records_dir / "powercontext" / f"state-readback-{_stamp()}.json")
    if not out.is_absolute():
        out = cfg.root / out
    _save_json(out, payload)
    print(f"state readback : {out}")
    print(f"records        : {len(payload['records'])}")
    print(f"prepared       : status={payload['prepared'].get('status')} bytes={payload['prepared'].get('bytes')}")
    return 0


def cmd_state_save(args: argparse.Namespace) -> int:
    cfg = _cfg()
    state = _state(cfg, args.scope_key)
    if args.entries_json:
        payload = json.loads(Path(args.entries_json).read_text(encoding="utf-8"))
        entries = [(str(item[0]), str(item[1])) for item in payload["entries"]]
    else:
        entries = []
        for raw in args.entry or []:
            kind, _, text = raw.partition(":")
            if not kind or not text:
                raise SystemExit(f"--entry must be kind:text, got {raw!r}")
            entries.append((kind, text))
    if not entries:
        raise SystemExit("nothing to save: pass --entry kind:text (repeatable) or --entries-json FILE")
    saved = state.save(entries)
    if args.json:
        # machine-readable mode: stdout stays a single valid JSON document
        _print_json({"db_path": _rel(cfg, cfg.pc_db_path), "saved": saved})
        return 0
    print(f"saved {len(saved)} records to {cfg.pc_db_path}")
    return 0


def cmd_patch_apply(args: argparse.Namespace) -> int:
    cfg = _cfg()
    patch_path = _patch_path(cfg, args.patch)
    if not patch_path.is_file():
        raise SystemExit(f"patch not found: {patch_path}")
    patch_text = patch_path.read_text(encoding="utf-8")
    touched = _assert_patch_scope(patch_text)
    if args.reverse:
        result = reverse_patch(cfg.demo_root, patch_text, check_only=args.check_only)
    else:
        result = apply_patch(cfg.demo_root, patch_text, check_only=args.check_only)
    result["touched_paths"] = touched
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


def cmd_patch_generate(args: argparse.Namespace) -> int:
    cfg = _cfg()
    patch_path = _patch_path(cfg, args.patch)
    if not args.live:
        if not patch_path.is_file():
            raise SystemExit(
                f"replay mode: patch not found at {patch_path}. "
                "Either generate it with --live (requires MINIMAX_API_KEY) or pass --patch."
            )
        print(f"replay mode: using recorded patch {patch_path} ({patch_path.stat().st_size} bytes)")
        return 0

    store = _store(cfg)
    state = _state(cfg, args.scope_key)
    pack = _context_for(cfg, store, state, _task_path(cfg, args.task))
    context_out = _save_context(cfg, pack, pack["task"]["id"])
    client = MiniMaxClient.from_config(cfg.minimax, cfg.minimax_record_dir)
    if not client.configured:
        raise SystemExit("MINIMAX_API_KEY is not set; use replay mode (omit --live) or export the key")
    messages = build_edit_messages(
        pack["task_text"],
        pack["context_text"],
        {"task": pack["task"], "context_sha256": pack["context_sha256"]},
    )
    result = client.chat(messages, tag="orders-filters", meta={"task": pack["task"]["id"], "context": str(context_out)})
    edits: dict[str, str] = {}
    for raw_path, body in extract_file_writes(result["content"]).items():
        rel = _normalise_edit_path(raw_path)
        if rel is not None:
            edits[rel] = body
    if edits:
        problems = _edit_validation_problems(edits)
        if problems:
            raise SystemExit("MiniMax returned invalid files: " + "; ".join(problems))
        diff = _diff_for_edits(cfg.demo_root, edits)
        mode = "live-file-writes"
    else:
        diff = extract_unified_diff(result["content"]) or ""
        mode = "live-diff"
    if not diff:
        raise SystemExit(
            "MiniMax returned neither complete-file sections nor a parsable unified diff; inspect the record at "
            + result["record_path"]
        )
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(diff, encoding="utf-8")
    meta = {
        "schema": "sddctx.patch-generation/v1",
        "generated_at": _stamp(),
        "mode": mode,
        "model": result.get("model"),
        "record": result["record_path"],
        "context_pack": _rel(cfg, context_out),
        "context_sha256": pack["context_sha256"],
        "response_sha256": result.get("response_sha256"),
        "patch_path": _rel(cfg, patch_path),
        "patch_sha256": verifymod._sha256_text(diff),
        "edited_files": sorted(edits),
        "usage": result.get("usage"),
        "trace_id": result.get("trace_id"),
        "elapsed_ms": result.get("elapsed_ms"),
    }
    _save_json(patch_path.with_suffix(".meta.json"), meta)
    print(f"patch written   : {patch_path}")
    print(f"record          : {result['record_path']}")
    store.close()
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    cfg = _cfg()
    patch_path = _patch_path(cfg, args.patch)
    if not patch_path.is_file():
        raise SystemExit(f"patch not found: {patch_path}")
    patch_text = patch_path.read_text(encoding="utf-8")
    _assert_patch_scope(patch_text)
    patch_sha = verifymod._sha256_text(patch_text)
    base_head = _git_head(cfg.root)

    before_root = cfg.evidence_dir / "tmp" / f"before-{_stamp()}"
    before_tree = verifymod.make_before_tree(cfg.demo_root, patch_text, work_root=before_root)
    if not before_tree["ok"]:
        raise SystemExit(f"could not build the pre-change tree: {before_tree['apply_stderr']}")
    try:
        before = verifymod.run_suites(
            before_tree["before_dir"],
            log_dir=cfg.evidence_dir / "logs",
            prefix="before",
            code_version={"base_git_head": base_head, "patch_sha256": patch_sha, "tree_state": "before"},
        )
        after = verifymod.run_suites(
            cfg.demo_root,
            log_dir=cfg.evidence_dir / "logs",
            prefix="after",
            code_version={"base_git_head": base_head, "patch_sha256": patch_sha, "tree_state": "after"},
        )
    finally:
        import shutil as _shutil
        _shutil.rmtree(before_root, ignore_errors=True)
    verdict = verifymod.evaluate(before, after, expected_target_failures_before=args.expect_target_failures)

    summary = {
        "schema": "sddctx.verification/v1",
        "generated_at": _stamp(),
        "patch": {"path": _rel(cfg, patch_path), "sha256": verifymod._sha256_text(patch_text)},
        "tree_state": _tree_state(cfg, patch_text),
        "before": {name: {**{k: record[k] for k in ("command", "exit_code", "counts", "duration_s")},
                          "log_path": _rel(cfg, record["log_path"])}
                   for name, record in before.items()},
        "after": {name: {**{k: record[k] for k in ("command", "exit_code", "counts", "duration_s")},
                         "log_path": _rel(cfg, record["log_path"])}
                  for name, record in after.items()},
        "before_tree": {"apply_exit_code": before_tree["apply_exit_code"], "patch_sha256": before_tree["patch_sha256"]},
        "verdict": verdict,
    }
    out = _save_json(cfg.evidence_dir / "verification.json", summary)

    if args.json:
        # machine-readable mode: stdout stays a single valid JSON document
        _print_json(summary)
    else:
        print(f"patch           : {patch_path}  sha256={summary['patch']['sha256'][:16]}…")
        print(f"before target   : exit={before['target']['exit_code']} counts={before['target']['counts']}")
        print(f"before regression: exit={before['regression']['exit_code']} counts={before['regression']['counts']}")
        print(f"after target    : exit={after['target']['exit_code']} counts={after['target']['counts']}")
        print(f"after regression: exit={after['regression']['exit_code']} counts={after['regression']['counts']}")
        for check in verdict["checks"]:
            print(f"  [{'PASS' if check['ok'] else 'FAIL'}] {check['check']}: {check['detail']}")
        print(f"evidence        : {_rel(cfg, out)}")
    return 0 if verdict["ok"] else 1


def _normalise_edit_path(raw: str) -> str | None:
    """Normalise a model-provided file path to ``app/...`` or reject it."""

    path = raw.strip().replace("\\", "/").lstrip("./")
    if path.startswith("demo/"):
        path = path[len("demo/"):]
    if ".." in path.split("/"):
        return None
    if not path.endswith(".py") or not path.startswith("app/"):
        return None
    return path


def _edit_validation_problems(edits: dict[str, str]) -> list[str]:
    import ast

    problems: list[str] = []
    for rel, content in edits.items():
        if not content.strip():
            problems.append(f"{rel}: empty content")
            continue
        try:
            ast.parse(content)
        except SyntaxError as exc:
            problems.append(f"{rel}: SyntaxError at line {exc.lineno}: {exc.msg}")
    return problems


def _diff_for_edits(demo_root: Path, edits: dict[str, str]) -> str:
    """Derive a git-style unified diff from complete file contents."""

    import difflib

    chunks: list[str] = []
    for rel in sorted(edits):
        target = demo_root / rel
        old = target.read_text(encoding="utf-8") if target.is_file() else ""
        new = edits[rel]
        if old == new:
            continue
        diff_lines = list(
            difflib.unified_diff(
                old.splitlines(keepends=True),
                new.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
                n=3,
            )
        )
        if not diff_lines:
            continue
        chunks.append(f"diff --git a/{rel} b/{rel}\n" + "".join(diff_lines))
    return "".join(chunks)


def _apply_edits(demo_root: Path, edits: dict[str, str]) -> dict[str, str | None]:
    """Write complete files, returning the previous contents for rollback."""

    originals: dict[str, str | None] = {}
    for rel, content in edits.items():
        target = demo_root / rel
        originals[rel] = target.read_text(encoding="utf-8") if target.is_file() else None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return originals


def _restore_edits(demo_root: Path, originals: dict[str, str | None]) -> None:
    for rel, old in originals.items():
        target = demo_root / rel
        if old is None:
            target.unlink(missing_ok=True)
        else:
            target.write_text(old, encoding="utf-8")


def _live_generate_with_retry(cfg: ToolkitConfig, pack: dict[str, Any], args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    """Ask MiniMax for the changed files, verify them, and return a derived diff.

    The model is asked for *complete new file contents* (``extract_file_writes``)
    because LLM-authored unified diffs are frequently malformed; the unified
    diff recorded in ``patches/`` is derived mechanically from the model's files
    and verified by actually running the test suites.
    """

    client = MiniMaxClient.from_config(cfg.minimax, cfg.minimax_record_dir)
    if not client.configured:
        raise SystemExit("MINIMAX_API_KEY is not set; run without --live to replay the recorded patch")
    messages = build_edit_messages(
        pack["task_text"],
        pack["context_text"],
        {"task": pack["task"], "context_sha256": pack["context_sha256"]},
    )
    feedback: str | None = None
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, args.attempts + 1):
        turn_messages = list(messages)
        if feedback:
            turn_messages = [
                *turn_messages,
                {"role": "assistant", "content": str(attempts[-1].get("raw_text", ""))[:20000]},
                {"role": "user", "content": feedback},
            ]
        result = client.chat(
            turn_messages,
            tag=f"orders-filters-attempt{attempt}",
            meta={"attempt": attempt, "task": pack["task"]["id"]},
        )
        content = result["content"]
        record: dict[str, Any] = {
            "attempt": attempt,
            "record": result["record_path"],
            "response_sha256": result.get("response_sha256"),
            "raw_text": content,
            "usage": result.get("usage"),
            "finish_reason": result.get("finish_reason"),
        }
        attempts.append(record)

        edits: dict[str, str] = {}
        rejected_paths: list[str] = []
        for raw_path, body in extract_file_writes(content).items():
            rel = _normalise_edit_path(raw_path)
            if rel is None:
                rejected_paths.append(raw_path)
            else:
                edits[rel] = body
        record["mode"] = "file-writes" if edits else "diff-fallback"
        record["edited_files"] = sorted(edits)
        record["rejected_paths"] = rejected_paths

        if rejected_paths:
            feedback = (
                "Rejected: you tried to modify files that must stay read-only: "
                f"{rejected_paths}. Only files under app/ may be changed; never touch tests/, specs/ or tasks/."
            )
            continue

        if edits:
            problems = _edit_validation_problems(edits)
            if problems:
                record["validation_problems"] = problems
                feedback = (
                    "The returned files are not valid Python or are incomplete. Return the COMPLETE new "
                    "content of every modified file (do not omit unchanged code).\n" + "\n".join(problems)
                )
                continue
            diff = _diff_for_edits(cfg.demo_root, edits)
            if not diff:
                feedback = "Your files are identical to the current code; actually implement the requested change."
                continue
            originals = _apply_edits(cfg.demo_root, edits)
            target = verifymod.run_pytest(
                cfg.demo_root,
                "tests/target",
                log_path=cfg.evidence_dir / "logs" / f"live-attempt{attempt}-target.json",
                code_version={"base_git_head": _git_head(cfg.root), "tree_state": "live-attempt"},
            )
            record["applied"] = True
            record["target_counts"] = target["counts"]
            if target["exit_code"] == 0:
                record["patch_sha256"] = verifymod._sha256_text(diff)
                return diff, {"attempts": attempts, "client": client.describe(), "model": client.model}
            _restore_edits(cfg.demo_root, originals)
            record["applied"] = False
            feedback = (
                "After writing your files the target tests still fail. Fix the implementation and return the "
                "complete new file contents again.\n" + target["stdout"][-6000:]
            )
            continue

        # ---- fallback: the model answered with a unified diff anyway ----
        diff = extract_unified_diff(content)
        if not diff:
            feedback = (
                "Your previous answer contained neither `=== FILE: app/... ===` complete-file sections nor a "
                "parsable unified diff. Return each modified file completely, in the documented format."
            )
            continue
        try:
            touched = _assert_patch_scope(diff)
        except SystemExit as exc:
            record["scope_violation"] = str(exc)
            feedback = (
                "Your patch was rejected because it touched read-only files: "
                f"{exc}. Only files under app/ may be modified."
            )
            continue
        record["touched_paths"] = touched
        applied = apply_patch(cfg.demo_root, diff, check_only=True)
        if not applied.get("ok"):
            feedback = (
                "The patch did not apply and the answer was not in complete-file format. Re-read the FULL FILE "
                "sections and return complete new file contents.\npatch error:\n"
                f"{str(applied.get('stderr', ''))[:4000]}"
            )
            continue
        apply_patch(cfg.demo_root, diff)
        target = verifymod.run_pytest(
            cfg.demo_root,
            "tests/target",
            log_path=cfg.evidence_dir / "logs" / f"live-attempt{attempt}-target.json",
        )
        record["applied"] = True
        record["target_counts"] = target["counts"]
        if target["exit_code"] == 0:
            record["patch_sha256"] = verifymod._sha256_text(diff)
            return diff, {"attempts": attempts, "client": client.describe(), "model": client.model}
        reverse_patch(cfg.demo_root, diff)
        record["applied"] = False
        feedback = (
            "The target tests still fail after applying your patch. Return the complete new file contents again.\n"
            + target["stdout"][-6000:]
        )
    raise SystemExit(
        f"MiniMax did not produce a passing change in {args.attempts} attempts; see {cfg.minimax_record_dir}"
    )


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _cfg()
    patch_path = _patch_path(cfg, args.patch)
    if not args.live and not patch_path.is_file():
        raise SystemExit(
            "replay mode needs a recorded patch, but it is missing:\n"
            f"  {patch_path}\n"
            "restore it with `git checkout -- minimax/patches/`, pass --patch FILE, "
            "or generate a fresh change with `run --live` (needs MINIMAX_API_KEY)."
        )
    if getattr(args, "fresh", False):
        for suffix in ("", "-wal", "-shm"):
            (cfg.pc_db_path.parent / (cfg.pc_db_path.name + suffix)).unlink(missing_ok=True)
        print(f"[0/6] fresh start: removed {cfg.pc_db_path}")
    store = _store(cfg)
    state = _state(cfg, args.scope_key)
    task_path = _task_path(cfg, args.task)

    # 0. put the business project back into the pre-change state, so the context
    #    pack describes exactly the code the task starts from
    if patch_path.is_file():
        reverted = _ensure_before(cfg, patch_path.read_text(encoding="utf-8"))
        print(f"[0/6] business project restored to the pre-change state (changed={reverted.get('changed')})")

    # 1. import engineering materials into seekdb
    specs = Path(args.specs).resolve() if args.specs else None
    ingest_result = ingest_materials(store, cfg.demo_root, specs=specs, archive_dir=cfg.data_dir / "ingest")
    print(f"[1/6] ingested {ingest_result['chunks']} chunks into {cfg.seekdb.describe()}")
    db_evidence = _collect_db_evidence(cfg, "run")
    print(f"      seekdb query record: {db_evidence}")

    # 2. build the development context pack
    pack = _context_for(cfg, store, state, task_path)
    context_out = _save_context(cfg, pack, pack["task"]["id"])
    state.save([
        ("decision", f"任务 {pack['task']['id']} 使用规格 {pack['task']['requirements']}"),
        ("done", f"已导入 {ingest_result['chunks']} 条资料到 seekdb，并生成上下文档 {len(pack['specs'])} 规格 / "
                 f"{len(pack['code'])} 代码 / {len(pack['tests'])} 测试"),
        ("todo", "生成并应用代码补丁"),
        ("todo", "运行目标测试与回归测试"),
    ])
    print(f"[2/6] context pack {context_out} "
          f"(specs={len(pack['specs'])}, code={len(pack['code'])}, tests={len(pack['tests'])})")

    # 3. obtain the patch
    if args.live:
        diff, generation = _live_generate_with_retry(cfg, pack, args)
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(diff, encoding="utf-8")
        slim_attempts: list[dict[str, Any]] = []
        for item in generation["attempts"]:
            slim = {
                key: item.get(key)
                for key in ("attempt", "response_sha256", "usage", "finish_reason", "mode",
                            "edited_files", "rejected_paths", "applied", "target_counts", "patch_sha256")
                if item.get(key) is not None
            }
            slim["record"] = _rel(cfg, item.get("record"))
            slim_attempts.append(slim)
        _save_json(patch_path.with_suffix(".meta.json"), {
            "schema": "sddctx.patch-generation/v1",
            "generated_at": _stamp(),
            "generation_mode": "live",
            "model": generation["model"],
            "endpoint": f"{cfg.minimax.base_url.rstrip('/')}/chat/completions",
            "context_pack": _rel(cfg, context_out),
            "context_sha256": pack["context_sha256"],
            "patch_path": _rel(cfg, patch_path),
            "patch_sha256": verifymod._sha256_text(diff),
            "attempts": slim_attempts,
        })
        print(f"[3/6] patch generated live by {generation['client']} -> {patch_path}")
    else:
        if not patch_path.is_file():
            raise SystemExit(f"replay mode: patch not found at {patch_path}")
        diff = patch_path.read_text(encoding="utf-8")
        touched = _assert_patch_scope(diff)
        applied = _ensure_after(cfg, diff)
        print(f"[3/6] replay patch {patch_path} (applied_now={applied['changed']}, touched={touched})")

    patch_sha = verifymod._sha256_text(diff)

    # 4. before/after verification
    before_root = cfg.evidence_dir / "tmp" / f"before-run-{_stamp()}"
    before_tree = verifymod.make_before_tree(cfg.demo_root, diff, work_root=before_root)
    if not before_tree["ok"]:
        raise SystemExit(f"could not build the pre-change tree: {before_tree['apply_stderr']}")
    try:
        before = verifymod.run_suites(
            before_tree["before_dir"],
            log_dir=cfg.evidence_dir / "logs",
            prefix="before",
            code_version={"base_git_head": _git_head(cfg.root), "patch_sha256": patch_sha, "tree_state": "before"},
        )
        after = verifymod.run_suites(
            cfg.demo_root,
            log_dir=cfg.evidence_dir / "logs",
            prefix="after",
            code_version={"base_git_head": _git_head(cfg.root), "patch_sha256": patch_sha, "tree_state": "after"},
        )
    finally:
        import shutil as _shutil
        _shutil.rmtree(before_root, ignore_errors=True)
    verdict = verifymod.evaluate(before, after, expected_target_failures_before=args.expect_target_failures)
    print(f"[4/6] before: target exit={before['target']['exit_code']} {before['target']['counts']} | "
          f"regression exit={before['regression']['exit_code']} {before['regression']['counts']}")
    print(f"      after : target exit={after['target']['exit_code']} {after['target']['counts']} | "
          f"regression exit={after['regression']['exit_code']} {after['regression']['counts']}")

    # 5. persist verification results (and a handoff outcome) in PowerContext
    verification_text = "; ".join(
        f"{check['check']}={ 'PASS' if check['ok'] else 'FAIL'} ({check['detail']})" for check in verdict["checks"]
    )
    state.save([
        ("verification", f"目标测试: before exit={before['target']['exit_code']} {before['target']['counts']} -> "
                         f"after exit={after['target']['exit_code']} {after['target']['counts']}"),
        ("verification", f"回归测试: before exit={before['regression']['exit_code']} -> "
                         f"after exit={after['regression']['exit_code']} {after['regression']['counts']}"),
        ("done", "已应用补丁并通过目标测试与回归测试"),
        ("todo_done", "生成并应用代码补丁"),
        ("todo_done", "运行目标测试与回归测试"),
        ("next_step", "如需继续：更新规格后重新 ingest，并重新生成上下文" if verdict["ok"] else "修复失败用例后重试"),
    ])
    outcome = state.record_task_outcome(
        objective=f"完成 {pack['task']['title']}",
        status="succeeded" if verdict["ok"] else "failed",
        summary=verification_text,
        checks=[(check["check"], "passed" if check["ok"] else "failed") for check in verdict["checks"]],
        done=[f"ingest {ingest_result['chunks']} chunks", f"context {pack['task']['id']}",
              f"patch {patch_sha[:16]}"],
        todo=[] if verdict["ok"] else ["fix failing target tests"],
    )
    state_evidence = _export_state_evidence(cfg, state, "final")
    print(f"      PowerContext state record: {state_evidence}")
    state_records = state.list()
    next_action = next(
        (record for record in state_records if record.get("kind") in {"next_step", "next_action"}),
        state_records[-1] if state_records else {},
    )
    summary = {
        "schema": "sddctx.run/v1",
        "generated_at": _stamp(),
        "task": pack["task"],
        "seekdb": cfg.seekdb.describe(),
        "code_version": {
            "base_git_head": _git_head(cfg.root),
            "demo_tree_state": "after",
            "patch_sha256": patch_sha,
            "suite_files": ["demo/tests/target/test_orders_filters_target.py",
                            "demo/tests/regression/test_orders_regression.py"],
        },
        "ingest": {k: (ingest_result[k] if k != "archive" else _rel(cfg, ingest_result[k]))
                   for k in ("chunks", "counts", "archive")},
        "context_pack": _rel(cfg, context_out),
        "context_sha256": pack["context_sha256"],
        "patch": {"path": _rel(cfg, patch_path), "sha256": patch_sha},
        "generation": {
            "mode": "live" if args.live else "replay",
            "patch_meta": _rel(cfg, patch_path.with_suffix(".meta.json"))
            if patch_path.with_suffix(".meta.json").is_file() else None,
        },
        "db_evidence": _rel(cfg, db_evidence),
        "state_evidence": _rel(cfg, state_evidence),
        "before": {name: {**{k: record[k] for k in ("command", "exit_code", "counts")},
                          "log_path": _rel(cfg, record["log_path"])} for name, record in before.items()},
        "after": {name: {**{k: record[k] for k in ("command", "exit_code", "counts")},
                         "log_path": _rel(cfg, record["log_path"])} for name, record in after.items()},
        "verdict": verdict,
        "powercontext": {"db": _rel(cfg, cfg.pc_db_path), "scope_key": args.scope_key or DEFAULT_SCOPE_KEY,
                          "outcome": outcome},
        "next_action": next_action,
    }
    out = _save_json(cfg.evidence_dir / "run.json", summary)
    verification_payload = {
        "schema": "sddctx.verification/v1",
        "generated_at": _stamp(),
        "patch": summary["patch"],
        "tree_state": "after",
        "before": {name: {**{k: record[k] for k in ("command", "exit_code", "counts", "duration_s")},
                          "log_path": _rel(cfg, record["log_path"])}
                   for name, record in before.items()},
        "after": {name: {**{k: record[k] for k in ("command", "exit_code", "counts", "duration_s")},
                         "log_path": _rel(cfg, record["log_path"])}
                  for name, record in after.items()},
        "verdict": verdict,
    }
    _save_json(cfg.evidence_dir / "verification.json", verification_payload)
    if args.live:
        _save_json(cfg.evidence_dir / "run-live-generation.json", summary)
    print(f"[5/6] verification {'PASSED' if verdict['ok'] else 'FAILED'}; PowerContext outcome written "
          f"(scope_key={args.scope_key or DEFAULT_SCOPE_KEY})")
    print(f"[6/6] run evidence: {out}")
    for check in verdict["checks"]:
        print(f"  [{'PASS' if check['ok'] else 'FAIL'}] {check['check']}: {check['detail']}")
    store.close()
    return 0 if verdict["ok"] else 1


def cmd_reset(args: argparse.Namespace) -> int:
    cfg = _cfg()
    patch_path = _patch_path(cfg, args.patch)
    if args.demo or args.force:
        if patch_path.is_file():
            diff = patch_path.read_text(encoding="utf-8")
            result = _ensure_before(cfg, diff) if not args.force else reverse_patch(cfg.demo_root, diff)
            print(f"demo tree state : before (changed={result.get('changed') if isinstance(result, dict) else result})")
        else:
            print("no patch recorded; demo tree left untouched")
    if args.data:
        import shutil
        shutil.rmtree(cfg.data_dir, ignore_errors=True)
        print(f"removed runtime data: {cfg.data_dir}")
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sddctx", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"sddctx {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create the seekdb schema")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("ingest", help="import specs / code / tests into seekdb")
    p.add_argument("--specs", help="spec directory to import (default: demo/specs)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("context", help="build the development context pack")
    p.add_argument("--task", help="task description file (default: demo/tasks/orders-filters.task.md)")
    p.add_argument("--out", help="output path for the context pack JSON")
    p.add_argument("--scope-key", help="PowerContext scope idempotency key")
    p.add_argument("--print-text", action="store_true", help="print the rendered context text")
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("state", help="inspect or save PowerContext task state")
    state_sub = p.add_subparsers(dest="state_command", required=True)
    p_show = state_sub.add_parser("show", help="read the persisted task state")
    p_show.add_argument("--scope-key")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_state_show)
    p_export = state_sub.add_parser("export", help="export the persisted state from a fresh process (restart evidence)")
    p_export.add_argument("--out", help="output JSON path (default: records/powercontext/state-readback-<ts>.json)")
    p_export.add_argument("--scope-key")
    p_export.set_defaults(func=cmd_state_export)
    p_save = state_sub.add_parser("save", help="write task-state records")
    p_save.add_argument("--scope-key")
    p_save.add_argument("--entry", action="append", help="kind:text (repeatable)")
    p_save.add_argument("--entries-json", help="JSON file with {'entries': [[kind, text], ...]}")
    p_save.add_argument("--json", action="store_true")
    p_save.set_defaults(func=cmd_state_save)

    p = sub.add_parser("patch", help="generate / apply a unified-diff patch")
    patch_sub = p.add_subparsers(dest="patch_command", required=True)
    p_gen = patch_sub.add_parser("generate", help="generate a patch with MiniMax (--live) or verify a recorded one")
    p_gen.add_argument("--task")
    p_gen.add_argument("--patch")
    p_gen.add_argument("--live", action="store_true", help="call the MiniMax API (requires MINIMAX_API_KEY)")
    p_gen.add_argument("--scope-key")
    p_gen.set_defaults(func=cmd_patch_generate)
    p_apply = patch_sub.add_parser("apply", help="apply or reverse the recorded patch")
    p_apply.add_argument("--patch")
    p_apply.add_argument("--reverse", action="store_true")
    p_apply.add_argument("--check-only", action="store_true")
    p_apply.set_defaults(func=cmd_patch_apply)

    p = sub.add_parser("verify", help="run the before/after test matrix and write evidence")
    p.add_argument("--patch")
    p.add_argument("--expect-target-failures", type=int, default=6)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("run", help="end-to-end: ingest -> context -> patch -> verify -> state")
    p.add_argument("--task")
    p.add_argument("--patch")
    p.add_argument("--specs")
    p.add_argument("--scope-key")
    p.add_argument("--live", action="store_true", help="generate the patch with the MiniMax API")
    p.add_argument("--attempts", type=int, default=3)
    p.add_argument("--expect-target-failures", type=int, default=6)
    p.add_argument("--fresh", action="store_true", help="start from an empty PowerContext state")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("db", help="seekdb usage evidence")
    db_sub = p.add_subparsers(dest="db_command", required=True)
    p_db = db_sub.add_parser("evidence", help="record DDL / counts / retrieval queries")
    p_db.set_defaults(func=cmd_db_evidence)

    p = sub.add_parser("reset", help="restore the demo project to the pre-change state")
    p.add_argument("--patch")
    p.add_argument("--demo", action="store_true", help="revert the business project tree")
    p.add_argument("--data", action="store_true", help="remove runtime data (.data)")
    p.add_argument("--force", action="store_true", help="reverse the patch even if the tree state is unclear")
    p.set_defaults(func=cmd_reset)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(
            "hint: start seekdb (`systemctl start seekdb` or the docker image) or set "
            "SEEKDB_HOST/SEEKDB_PORT; see docs/runbook.md",
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
