# Changelog

All notable changes to the `minimax/` submission are documented here.
The V1 series is the version submitted for judging; it is a hardened revision of the
initial delivery (commit `62ae7f1`). Full feasibility audit: [docs/audit-v1.md](docs/audit-v1.md).

## V1 — 1.0.0 (2026-10-29)

首个正式版本（Version 1）。对 `minimax/` 全部文件做了可行性审计（5 条并行工作流：CLI/上下文/脚本/集成 +
seekdb 资料层 + 补丁/验证层 + PowerContext 状态层 + 文档/证据/安装矩阵），修复 16 项缺陷并补齐验证。

### V1 收尾补充（同日）

- 新增 `scripts/check-evidence.py`：91 项只读证据链自检（patch ↔ meta ↔ MiniMax 记录 ↔ 上下文 ↔
  日志/JUnit ↔ 快照 ↔ PowerContext 状态 ↔ 文档），退出码 = 失败数；已接入 `verify-all.sh` 第 6 步。
- 修复 DB 证据"一次查询返回三类资料"：原 UNION 三个分支共用同一组关键词，实际只命中 spec；现按
  kind 使用各自命中的关键词（spec=需求中文词 / code=符号名 / test=测试函数名），一条语句稳定返回三类。
- 用真实 MiniMax 调用重新生成 V1 证据（1 次成功；patch sha `ebdbe1c2…`），并重新生成快照/规格对比/状态读回。

### 可运行性（P0）

- 修复干净环境复现的两个阻断点：`demo.sh` 导出相对解释器路径导致 `verify` 子进程 `FileNotFoundError`；
  `setup.sh` 在缺 `python3-venv` 的宿主上建 venv 失败。现在解释器路径绝对化（`abspath`，不破坏 venv），
  venv 创建失败会自动回退 `uv venv --seed --python <当前解释器>`，无 uv 时给出可操作提示。
- Python 版本修正：`requirements.lock` 中 `numpy 2.5.4` 要求 ≥3.12，故最低版本为 **3.12**（原文档误写 3.11）；
  `pyproject.toml` 改为 `>=3.12,<4.0`，3.12 / 3.13 / 3.14 三版本实测通过。
- 补丁层去掉对 GNU `patch`/`git` 的依赖：改为**纯 Python unified-diff 引擎**（支持多文件、CRLF、
  新建/删除、`\ No newline`；上下文不匹配时给出文件+hunk+行号的可操作错误；先全量校验再原子写盘）。

### 正确性与健壮性

- PowerContext：修复两进程同时初始化全新 DB 时的 P0 竞争崩溃（瞬态错误识别 + 退避重试 + `busy_timeout`），
  异常统一包装，长文本切分改为 NFC 无损，私有 asyncio loop 增加 fork 安全。
- seekdb 资料层：citation 行号与 body 行数强校验（杜绝假引用）、Windows 路径归一化、库名白名单、
  path 长度前置校验、批量 upsert 事务化、ingest 归档微秒时间戳（防同秒覆盖）。
- 验证器：用 JUnit `classname::name` 集合严格校验"修改前后是同一组测试"；日志新增
  `testcase_ids`/`code_version{base_git_head, patch_sha256, tree_state}`；`make_before_tree` 走纯 Python 引擎。
- 上下文模块：输出确定性排序；`state.db_path` 相对化（`context_sha256` 可跨机复算）；
  `--json` 输出为单份合法 JSON；`ingest --json` 不再混入人类摘要；DB 证据 schema 修正为 `seekdb-evidence`。
- CLI：seekdb 不可达、replay 缺补丁、空上下文等场景给出可操作提示与非零退出码；`run` 在缺补丁时
  **先报错再执行副作用**；新增 `--version`、`state export`、`db evidence` 失败退出码。
- 测试隔离：测试库改为每进程 `sddctx_test_<pid>`（会话末清理），并发 pytest 不再互相干扰；
  新增 `tests/conftest.py`。

### 记录与文档

- MiniMax 开发记录：`index.jsonl` 路径相对化；本次 V1 证据由真实调用重新生成（1 次成功，模型 `MiniMax-M3.1-Flash`）。
- `records/db`：新增按 spec/code/test 的分类检索与一条 `UNION ALL` 三类联合检索；README 同步说明。
- 新增 `scripts/verify-all.sh`（一条命令全量验证）、`scripts/lint.sh` + ruff 正确性规则；
  `scripts/setup.sh` 增加版本/venv/seekdb 预检。
- 文档对齐：README/runbook/sample/tech-note 修正版本范围、脚本清单、before 数字前提、版本对应语义、
  docker 镜像 tag、`evidence/ingest/` 路径等；新增本 Changelog 与 V1 审计报告。
- `.gitignore` 补充 `*.egg-info/`、`build/`、`dist/`、`.ruff_cache/`。
