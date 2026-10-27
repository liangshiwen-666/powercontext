# 运行说明（Runbook）

> 适用版本：本目录（`minimax/`）提交物；最后更新 2026-10-27。
> 所有命令按顺序在**干净环境**中执行即可复现；不需要 MiniMax API Key（见 §6 的 live 模式）。

## 1. 环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| 操作系统 | Linux x86_64 / macOS | PowerContext 官方要求 macOS/Linux；Windows 可用 WSL2 |
| Python | 3.11 – 3.13 | 已验证 3.12.16；`powercontext==1.4.1` 要求 `>=3.11,<4.0` |
| seekdb | 1.4.0（`5.7.25-OceanBase seekdb-v1.4.0.0`） | 任选 §2 两种部署方式之一 |
| 网络 | 首次安装需要访问 PyPI | 可换镜像：`MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple bash scripts/setup.sh` |
| 其他 | `bash`、`patch`（GNU patch 2.x） | Ubuntu/macOS 自带 |

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

连接参数可通过环境变量覆盖：`SEEKDB_HOST`（默认 `127.0.0.1`）、`SEEKDB_PORT`（默认 `2881`）、
`SEEKDB_USER`（默认 `root`）、`SEEKDB_PASSWORD`（默认空）、`SEEKDB_DATABASE`（默认 `sddctx`）。

## 3. 安装依赖

```bash
cd minimax
bash scripts/setup.sh
```

脚本会：创建 `.venv` → 安装 `requirements.lock`（或 `pip install -e ".[demo]"`）→ 打印版本 →
执行 `python -m sddctx init` 建库建表。

实际版本（`requirements.lock` 锁定）：

| 组件 | 版本 |
|---|---|
| powercontext | 1.4.1（`[builtin]`，进程内） |
| pymysql | 1.2.0 |
| requests | 2.34.2 |
| fastapi | 0.162.0 |
| pytest | 9.1.1 |

## 4. 调用入口

上下文模块是一个 CLI（`python -m sddctx`），子命令如下：

| 命令 | 作用 | 主要输出 |
|---|---|---|
| `python -m sddctx init` | 建库建表（幂等） | 表结构、行数 |
| `python -m sddctx ingest [--specs DIR]` | 规格/代码/测试切块入 seekdb | `chunks`、归档 JSONL、库内计数 |
| `python -m sddctx context [--task FILE] [--out FILE]` | **构造开发上下文** | 上下文包 JSON（规格条目/代码符号/测试/状态，全部带 `path:line` 引用） |
| `python -m sddctx state show` | 读回 PowerContext 任务状态 | 记录列表 + prepared context |
| `python -m sddctx state save --entry kind:text` | 写入任务状态 | 记录引用 |
| `python -m sddctx patch generate [--live]` | 取补丁（`--live` 调 MiniMax，默认重放） | `patches/*.patch` + `.meta.json` |
| `python -m sddctx patch apply [--reverse] [--check-only]` | 应用/回退补丁 | apply 结果 |
| `python -m sddctx verify` | 跑 before/after 测试矩阵 | `evidence/verification.json`、`evidence/logs/*.json` |
| `python -m sddctx run [--live]` | **端到端**：导入→上下文→补丁→验证→状态 | `evidence/run.json` |
| `python -m sddctx reset [--demo] [--data]` | 回到修改前 / 清空运行时数据 | — |

输入/输出约定：

- 输入：一组任务描述文件（见 `demo/tasks/orders-filters.task.md`）、业务工程的 `specs/`、`app/`、`tests/`。
- 输出：`evidence/context/*.json`。字段含义：
  - `task`：任务 id、标题、涉及的规格编号；
  - `specs[]`：适用的规格条目（`requirement_id`、`citation`、原文摘录）；
  - `code[]`：需要修改的文件/符号（`citation`、`symbol`、摘录）与 `file_contents` 全文；
  - `tests[]`：目标测试（`suite=target`）与回归测试（`suite=regression`）；
  - `state`：PowerContext 中的已完成/待办/验证结果与 `next_action`；
  - `context_sha256`：上下文的哈希（规格变化后会变化）。

## 5. 测试命令

```bash
# 业务工程（评审重点）
cd demo && python -m pytest tests/regression -q    # 18 passed（修改前后都必须通过）
cd demo && python -m pytest tests/target -q        # 修改前 6 failed，修改后 6 passed
cd demo && python -m pytest -q                     # 全绿（24 passed）

# 上下文模块自身的单元测试
python -m pytest tests -q
```

日志统一带命令、退出码、计数与完整 stdout（`evidence/logs/*.json`、`evidence/logs/*.junit.xml`）。

## 6. 一键复现与 live 模式

```bash
bash scripts/demo.sh        # 重放模式：不需要 API Key
```

`demo.sh` 依次执行：初始化 → `run`（导入/上下文/补丁重放/验证/状态）→ **新进程** `state show`
（验证"重启后读取状态"）→ 打印上下文预览与证据文件清单。

现场调用 MiniMax（需要 Key）：

```bash
export MINIMAX_API_KEY=sk-xxx            # 国内站 key；国际站另设 MINIMAX_BASE_URL=https://api.minimax.io/v1
export MINIMAX_MODEL=MiniMax-M3.1-Flash  # 默认值
python -m sddctx run --live --attempts 3
```

`--live` 会把上下文发给 MiniMax，并把模型返回的**被修改文件的完整新内容**写成文件
（LLM 直接生成 unified diff 时 hunk 头经常不自洽，所以本项目让模型输出完整文件、由程序机械生成
diff）；随后立刻跑目标测试，若失败会把测试输出回喂模型重试，最多 `--attempts` 次。
全部通过后才由 `difflib` 生成 `patches/0001-orders-filters.patch`（before→after，可重放）。
每次调用都落盘到 `records/minimax/`（含脱敏请求头、响应原文、usage、trace id、sha256 链），
`patches/*.meta.json` 记录 **上下文 sha256 ↔ 响应 sha256 ↔ 补丁 sha256** 的对应关系。

## 6.1 代码版本对应

`evidence/run.json` 与 `evidence/run-live-generation.json` 中的 `code_version` 记录：

| 字段 | 含义 |
|---|---|
| `base_git_head` | 本次改动所基于的分支基线 commit（提交 commit 的祖先） |
| `demo_tree_state` | `after`（业务工程为修改后状态） |
| `patch_sha256` | 本次改动补丁的哈希（= `patches/0001-orders-filters.patch`） |
| `suite_files` | 使用的同一组测试文件（修改前后一致） |

因此"测试日志对应的代码版本"可校验为：**提交 commit 的树 = `base_git_head` + `patch_sha256` 对应的补丁**。

## 7. 规格更新演示

```bash
bash scripts/spec-update.sh
```

先用 `demo/specs/history/orders.v1.md`（初始版本）导入并生成上下文 A，再用 `demo/specs/orders.md`
（v2.0，新增 REQ-ORD-006～009）重新导入并生成上下文 B，最后打印两份上下文的差异。
这证明上下文来自数据库检索而非写死的答案。

## 8. 重置

```bash
bash scripts/reset.sh            # 业务工程回到修改前 + 清空 .data（PowerContext 状态、入库归档）
bash scripts/setup.sh            # 重新初始化
```

## 9. 已知限制

1. **MiniMax Key**：`--live` 模式需要有效 Key；评审复现走重放模式，不需要 Key。
2. **seekdb 依赖**：必须有可连通的 seekdb 实例（Docker 或 apt）。嵌入式 `pylibseekdb` 仅
   Linux/macOS 可用，本项目未作为默认路径。
3. **PowerContext 版本**：固定 `1.4.1`；其进程内 API 为 Core SDK，本项目已做薄封装。
4. **检索为全文检索**：未接 embedding，因此没有向量召回；中文检索依赖 `ik` parser。
5. **Windows 原生不支持**：请使用 WSL2（本项目在 WSL2 Ubuntu 26.04 + Windows 11 上开发验证）。
6. 业务工程为演示用内存数据（无外部数据库），这是刻意的：让"改代码→测试红绿"完全确定。
