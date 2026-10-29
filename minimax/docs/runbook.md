# 运行说明（Runbook）— V1（1.0.0）

> 适用版本：本目录（`minimax/`）提交物 V1；最后更新 2026-10-29。
> V1 变更与可行性审计见 [CHANGELOG.md](../CHANGELOG.md)、[audit-v1.md](audit-v1.md)。
> 所有命令按顺序在**干净环境**中执行即可复现；**不需要 MiniMax API Key**（见 §6 的 live 模式）。

## 1. 环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| 操作系统 | Linux x86_64 / macOS | PowerContext 官方要求 macOS/Linux；Windows 可用 WSL2 |
| Python | **3.12 – 3.14**（推荐 3.12 / 3.13） | `requirements.lock` 的 `numpy 2.5.4` 要求 ≥3.12，故 **3.11 不可用**；3.12.16 / 3.13.18 / 3.14.4 均已实测 |
| 构建工具 | `python3-venv`（或 `uv`） | `python -m venv` 需要 venv 模块；若缺失，`setup.sh` 会自动回退到 `uv venv --seed` 或给出安装提示 |
| seekdb | 1.4.0（`5.7.25-OceanBase seekdb-v1.4.0.0`） | 任选 §2 两种部署方式之一 |
| 网络 | 首次安装需要访问 PyPI | 可换镜像：`MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple bash scripts/setup.sh` |
| 其他 | `bash`；无 `patch` / `git` 也可运行 | V1 的补丁应用为纯 Python 实现 |

## 2. 准备 seekdb（二选一）

### 2.1 Docker（推荐）

```bash
docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb:latest
# 等待就绪
docker exec seekdb sh -c 'mysql -h127.0.0.1 -P2881 -uroot -e "select version()"'
```

### 2.2 apt 安装（不使用 Docker 的机器）

```bash
# Ubuntu 24.04 / 26.04（26.04 没有独立的 resolute 目录，用 noble 的包）
echo 'deb [trusted=yes] http://mirrors.oceanbase.com/oceanbase/community/stable/ubuntu/noble/amd64/ ./' \
  | sudo tee /etc/apt/sources.list.d/oceanbase.list
sudo apt-get update && sudo apt-get install -y seekdb
sudo systemctl enable --now seekdb
mysql -h127.0.0.1 -P2881 -uroot -e "select version()"
```

连接参数可用环境变量覆盖：`SEEKDB_HOST`（默认 `127.0.0.1`）、`SEEKDB_PORT`（默认 `2881`）、
`SEEKDB_USER`（默认 `root`）、`SEEKDB_PASSWORD`（默认空）、`SEEKDB_DATABASE`（默认 `sddctx`）。

### 2.3 可选：嵌入式 seekdb（无 server，仅 Linux/macOS）

```bash
. .venv/bin/activate && pip install "pylibseekdb==1.4.0.post1"
export SDDCTX_SEEKDB_EMBEDDED_DIR=$PWD/.data/seekdb
```

V1 已实测：`init_schema / upsert / 中文 ik 检索 / list_chunks / 关闭重开持久化 / reset` 均通过
（单进程使用；跨进程同目录未验证）。默认/评审主路径仍是 server。

## 3. 安装依赖

```bash
cd minimax
bash scripts/setup.sh
```

脚本依次执行：Python 版本检查 → 创建 `.venv` → 安装 `requirements.lock`（或 `pip install -e ".[demo]"`）
→ 打印版本 → **seekdb 连通性预检** → `python -m sddctx init` 建库建表。失败时给出可操作提示。

实际锁定版本（`requirements.lock`，共 71 个包）：

| 组件 | 版本 |
|---|---|
| powercontext | 1.4.1（`[builtin]`，进程内） |
| pymysql | 1.2.0 |
| requests | 2.34.2 |
| fastapi | 0.162.0 |
| pytest | 9.1.1 |
| httpx | 0.28.1 |
| uvicorn | 0.54.0 |

## 4. 调用入口

上下文模块是一个 CLI（`python -m sddctx`）：

| 命令 | 作用 | 主要输出 |
|---|---|---|
| `python -m sddctx --version` | 打印版本 | `sddctx 1.0.0` |
| `python -m sddctx init` | 建库建表（幂等） | 表结构、行数 |
| `python -m sddctx ingest [--specs DIR] [--json]` | 规格/代码/测试切块入 seekdb | chunks/归档 JSONL/库内计数；`--json` 输出单份合法 JSON |
| `python -m sddctx context [--task FILE] [--out FILE] [--print-text]` | **构造开发上下文** | 上下文包 JSON（规格/代码/测试/状态，全部带 `path:line`）；默认写 `.data/context/` |
| `python -m sddctx state show [--json]` | 读回 PowerContext 任务状态 | 记录列表 + prepared context |
| `python -m sddctx state export --out FILE` | 在**新进程**中导出状态（重启复用证据） | `records/powercontext/state-readback.json` |
| `python -m sddctx state save --entry kind:text` | 写入任务状态 | 记录引用 |
| `python -m sddctx patch generate [--live]` | 取补丁（`--live` 调 MiniMax，默认重放） | `patches/*.patch` + `.meta.json` |
| `python -m sddctx patch apply [--reverse] [--check-only]` | 应用/回退补丁（纯 Python） | apply 结果（`tool="python"`） |
| `python -m sddctx verify [--json]` | 跑 before/after 测试矩阵 | `evidence/verification.json`、`evidence/logs/*.json` |
| `python -m sddctx run [--live] [--fresh]` | **端到端**：导入→上下文→补丁→验证→状态 | `evidence/run.json`、`evidence/verification.json` |
| `python -m sddctx db evidence` | 记录 seekdb DDL/计数/检索 SQL | `records/db/*.json` |
| `python -m sddctx reset [--demo] [--data]` | 回到修改前 / 清空运行时数据 | — |

输入/输出约定：

- 输入：任务描述文件（`demo/tasks/orders-filters.task.md`）、业务工程的 `specs/`、`app/`、`tests/`。
- 输出：`evidence/context/*.json`。字段含义：
  - `task`：任务 id、标题、涉及的规格编号；
  - `specs[]`：适用的规格条目（`requirement_id`、`citation`、原文摘录）；
  - `code[]`：需要修改的文件/符号（`citation`、`symbol`、摘录）与 `file_contents` 全文；
  - `tests[]`：目标测试（`suite=target`）与回归测试（`suite=regression`）；
  - `state`：PowerContext 中的已完成/待办/验证结果与 `next_action`；
  - `context_sha256`：上下文哈希（同一输入两次运行一致；规格变化后变化）。

## 5. 测试命令

```bash
# 一条命令全量验证（推荐）
bash scripts/verify-all.sh

# 业务工程（评审重点）
cd demo && python -m pytest tests/regression -q    # 18 passed（修改前后都必须通过）
cd demo && python -m pytest tests/target -q        # 提交树（after）为 6 passed；before 的 6 failed 见下一行说明
cd demo && python -m pytest -q                     # 全绿（24 passed）

# 上下文模块自身的单元测试（V1：141 passed / 2 skipped，见 audit-v1.md）
python -m pytest tests -q

# 证据链自检（patch ↔ meta ↔ records ↔ 上下文 ↔ 日志 ↔ 快照 ↔ 文档，只读，约 2 秒）
python scripts/check-evidence.py
```

> `check-evidence.py` 会逐项核对证据链并打印 `PASS/FAIL`（退出码 = 失败数）；
> `verify-all.sh` 已把它作为第 6 步自动执行，因此"一条命令"即可完成全部核对。

> 提交树中的业务工程是**修改后**状态，所以直接 `pytest tests/target` 是 6 passed。
> 想亲手看到"修改前 6 failed"，先执行 `python -m sddctx reset --demo`（把补丁反向应用），
> 再跑 `pytest tests/target`，最后 `python -m sddctx patch apply` 恢复；`demo.sh`/`verify` 会自动完成这个矩阵。

测试日志统一记录：命令、退出码、计数、完整 stdout（`evidence/logs/*.json`）、
JUnit XML（`*.junit.xml`）与 `code_version{base_git_head, patch_sha256, tree_state}`；
`verify` 还会比对 before/after 的 **JUnit testcase 集合**是否完全一致（防改名/删测）。

## 6. 一键复现与 live 模式

```bash
bash scripts/demo.sh        # 重放模式：不需要 API Key
```

`demo.sh` 依次执行：初始化 → `run`（导入/上下文/补丁重放/验证/状态）→ **新进程** `state show`
→ `state export`（写 `records/powercontext/state-readback.json`）→ 上下文预览 → 证据清单。

现场调用 MiniMax（需要 Key）：

```bash
export MINIMAX_API_KEY=sk-xxx            # 国内站 key；国际站另设 MINIMAX_BASE_URL=https://api.minimax.io/v1
bash scripts/live-generate.sh            # == python -m sddctx run --live --attempts 5
```

V1 的 `--live` 流程：把开发上下文发给 MiniMax，要求返回**被修改文件的完整新内容**
（LLM 直接生成 unified diff 的 hunk 头经常不自洽）；程序做语法校验 → 写入 → 跑目标测试，
失败则把校验/测试输出回喂模型重试；全部通过后用纯 Python 引擎机械生成
`patches/0001-orders-filters.patch`。每次调用都落盘到 `records/minimax/`（脱敏请求头、
响应原文、usage、trace id、sha256 链），`patches/*.meta.json` 记录上下文 ↔ 响应 ↔ 补丁的哈希。

### 6.1 代码版本对应

`evidence/run.json` 的 `code_version` 记录：

| 字段 | 含义 |
|---|---|
| `base_git_head` | **生成证据时**仓库的 HEAD（提交 commit 的祖先） |
| `demo_tree_state` | `after`（业务工程为修改后状态；提交树与之一致） |
| `patch_sha256` | 本次改动补丁哈希（= `patches/0001-orders-filters.patch`） |
| `suite_files` | 使用的同一组测试文件（修改前后一致） |

核对方式：`evidence/snapshots/before/` **加上** `patches/0001-orders-filters.patch` 应逐字节等于
`evidence/snapshots/after/`，而 `after` 应等于提交树中的 `demo/app/`（`evidence/snapshots/README.md`
给出逐字节校验命令）。测试日志里的 `code_version` 是"命令 + 退出码 + 代码版本"三件套的一部分。

## 7. 规格更新演示

```bash
bash scripts/spec-update.sh
```

先用 `demo/specs/history/orders.v1.md`（初始版本）导入并生成上下文 A，再用 `demo/specs/orders.md`
（v2.0，新增 REQ-ORD-006～009）重新导入并生成上下文 B，最后打印两份上下文的差异
（`evidence/context/spec-v1-context.json` / `spec-v2-context.json`、`evidence/ingest/*.json`）。
这证明上下文来自数据库检索而非写死的答案。

## 8. 重置

```bash
bash scripts/reset.sh            # 业务工程回到修改前 + 清空 .data（PowerContext 状态、入库归档）
bash scripts/setup.sh            # 重新初始化
```

## 9. 已知限制

1. **MiniMax Key**：`--live` 模式需要有效 Key；评审复现走重放模式，不需要 Key。
2. **seekdb 依赖**：必须有可连通的 seekdb 实例（Docker 或 apt）。嵌入式 `pylibseekdb`
   为可选备选，仅 Linux/macOS、单进程使用；其数据目录约 190MB。
3. **PowerContext 版本**：固定 `1.4.1`；其进程内 API 为 Core SDK，本项目已做薄封装
   （`pcstore.py`，含瞬态错误重试与异常包装）。
4. **检索为全文检索**：未接 embedding，因此没有向量召回；中文检索依赖 `ik` parser。
5. **补丁引擎**：纯 Python、精确上下文匹配（无 fuzz）；不支持 rename/binary patch——
   本项目生成的补丁只含修改/新建文本文件。
6. **Windows 原生不支持**：请使用 WSL2（本项目在 WSL2 Ubuntu 26.04 上开发验证）。
7. 业务工程为演示用内存数据（无外部数据库），这是刻意的：让"改代码→测试红绿"完全确定。

## 10. V1 审计与修复摘要

详见 [audit-v1.md](audit-v1.md)。V1 共修复 10 个真实缺陷（含 2 个 P0/P1 影响验收的风险）：
补丁层外部工具依赖（改为纯 Python 引擎）、JUnit 测试集校验、PowerContext 并发 bootstrap 崩溃、
citation 行号不一致、Windows 路径、库名注入面、批量写非事务、ingest 归档覆盖、
内容可复现排序与 CLI 错误处理；并补齐测试隔离（每进程独立测试库）与安装矩阵验证（3.12/3.13）。
