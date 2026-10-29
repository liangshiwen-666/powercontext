# 规有所依：面向软件持续演进的 SDD 上下文管理系统（MiniMax 赛道）

**版本 V1（1.0.0）** ｜ 变更见 [CHANGELOG.md](CHANGELOG.md) ｜ 全部文件可行性审计见 [docs/audit-v1.md](docs/audit-v1.md)

本目录是 OceanBase PowerContext 2026 生态赛题 **MiniMax 赛道**的提交物，内容全部由本作品新增
（上游 `minimax/` 仅有 `.gitkeep` 占位），并自包含运行所需的源码、脚本与证据。

目标：让 AI 开发助手在改代码前拿到**当前生效的规格、需要修改的代码、用于验收的测试**，
并在任务中断/进程重启后**复用已保存的任务进展**。本仓库用一次真实的局部修改
（订单查询服务增加筛选能力）完整验证了这条链路。

## 一句话流程

```
任务描述 → seekdb 检索规格/代码/测试 → 上下文包（含真实 path:line 引用）
        → MiniMax 返回修改后的完整文件 → 机械生成 unified diff 并应用
        → 目标测试由红转绿 + 回归测试全绿（同一组测试，JUnit testcase 集合校验）
        → PowerContext 保存任务状态 → 新进程读回并用于下一步
```

## 目录

| 路径 | 说明 |
|---|---|
| `sddctx/` | 上下文模块（Python 包 + CLI）：seekdb 资料层、PowerContext 状态层、MiniMax 客户端、纯 Python 补丁引擎、验证器 |
| `demo/` | 业务工程：FastAPI 订单查询服务 + 规格（v1.0 历史版 / v2.0 生效版）+ 目标/回归测试 |
| `patches/` | MiniMax 生成的补丁及哈希/记录索引 |
| `records/minimax/` | MiniMax 开发记录（请求/响应原文、参数、usage、trace id、sha256 链） |
| `records/db/` | seekdb 建表与检索 SQL 记录（真实执行输出） |
| `records/powercontext/` | 任务状态写入记录 + **新进程**读回记录（`state-readback.json`） |
| `evidence/` | 上下文包、before/after 代码快照、测试日志（命令/退出码/代码版本/testcase 集合）、验证结论、规格更新对比 |
| `scripts/` | `setup.sh` / `verify-all.sh` / `check-evidence.py` / `demo.sh` / `spec-update.sh` / `state-readback.sh` / `check-env.sh` / `live-generate.sh` / `lint.sh` / `reset.sh` / `diff_context.py` |
| `docs/` | [运行说明](docs/runbook.md) · [验证样例](docs/sample.md) · [一页技术说明](docs/tech-note.md) · [V1 审计报告](docs/audit-v1.md) |

## 快速开始（评审复现）

```bash
# 0. 准备 seekdb（已安装可跳过；二选一，见 docs/runbook.md）
#    docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb

# 1. 安装依赖（含 Python 版本/seekdb 连通性预检）+ 初始化数据库
bash scripts/setup.sh

# 2. 一条命令跑完全部验证（环境检查 + 单元测试 + 业务测试 + 重放演示 + 规格更新 + 证据汇总）
bash scripts/verify-all.sh
```

也可以分步执行：`bash scripts/demo.sh`（重放模式，**不需要 API Key**）、`bash scripts/spec-update.sh`。
现场重新调用模型：

```bash
export MINIMAX_API_KEY=sk-...
bash scripts/live-generate.sh     # 等价于 python -m sddctx run --live，失败会带校验/测试输出重试
```

## 六项验收对照

| 验收项 | 本仓库证据 |
|---|---|
| 能够运行 | `scripts/setup.sh` + `scripts/verify-all.sh`；`docs/runbook.md` 给出依赖版本、seekdb 初始化、CLI 入口与已知限制 |
| MiniMax 实际参与 | `records/minimax/*.json`（完整请求/响应）+ `patches/0001-orders-filters.patch` + `patches/*.meta.json`（上下文 sha256 ↔ 响应 sha256 ↔ 补丁 sha256 可核对） |
| 上下文正确 | `evidence/context/*.json`：每条含真实 `path:line` 引用；`bash scripts/spec-update.sh` 演示规格更新重导入后上下文变化（A/B 两份上下文 + sha256） |
| 数据库实际参与 | `records/db/*.json`：真实 `SHOW CREATE TABLE`（含 `WITH PARSER ik`）、入库计数、`MATCH…AGAINST` 检索命中；上下文 citations 即检索结果 |
| 状态能够复用 | `evidence/run.json` 的 PowerContext outcome；`records/powercontext/state-readback.json` 为**新进程**读回；`scripts/demo.sh` 内含该步骤 |
| 代码修改有效 | `evidence/verification.json`：同一组测试 before（target 6 failed / regression 18 passed）与 after（6 passed / 18 passed），并校验前后 JUnit testcase 集合完全一致；`evidence/snapshots/` 为修改前后快照 |

## 技术栈（实际采用的方式与版本）

| 技术 | 版本 / 方式 |
|---|---|
| MiniMax | HTTP API `POST https://api.minimax.cn/v1/chat/completions`（OpenAI 兼容），`model=MiniMax-M3.1-Flash`，Bearer 鉴权，无 SDK 依赖（`requests`） |
| seekdb | seekdb 1.4.0 `5.7.25-OceanBase seekdb-v1.4.0.0`；MySQL 协议客户端 `pymysql`；全文索引 `WITH PARSER ik`；嵌入式 `pylibseekdb` 为可选备选（已实测） |
| PowerContext | PyPI `powercontext==1.4.1`，进程内 `builtin` runtime + 文件型 SQLite（`sqlite+aiosqlite:////.../pc.db`） |
| 业务工程 | Python 3.11+ / FastAPI / pytest |
| 补丁应用 | 纯 Python unified-diff 引擎（不依赖 `patch`/`git`，V1 起） |

## 兼容性

- Python：**3.12 – 3.14**（`requirements.lock` 中 `numpy 2.5.4` 要求 ≥3.12，故 3.11 不可用；
  3.12.16 / 3.13.18 / 3.14.4 均已实测安装并跑通）。
- 操作系统：Linux / macOS（补丁引擎为纯 Python，不依赖 GNU `patch`/`git`）；Windows 请用 WSL2。
- seekdb：1.4.0 server（apt 或 docker），或嵌入式 `pylibseekdb`（仅 Linux/macOS）。

## 第三方组件与来源

| 组件 | 来源 / 许可 | 在本作品中的用途 | 是否修改 |
|---|---|---|---|
| PowerContext 1.4.1 | PyPI `powercontext`（Apache-2.0，<https://github.com/oceanbase/powercontext>） | 任务状态持久化与跨进程复用（进程内 builtin runtime + 文件 SQLite） | 未修改源码，仅调用公开 API |
| seekdb 1.4.0 | OceanBase 社区发行包 / `oceanbase/seekdb` 镜像 | 工程资料（规格/代码/测试）存储与中文全文检索 | 未修改 |
| MiniMax API | MiniMax 开放平台（`MiniMax-M3.1-Flash`，OpenAI 兼容 HTTP） | 依据上下文生成代码修改（完整文件级输出） | — |
| `demo/`、`sddctx/` | 本作品原创 | 业务工程与上下文模块本体 | — |

## 平台提交信息

测评平台的仓库地址锁定为团队仓库 `https://github.com/liangshiwen-666/powercontext-fork.git`；
本目录内容提交在分支 `2026-competition-ecosystem` 上，平台申报的 commit 以该分支的实际提交为准。

## 重置

```bash
bash scripts/reset.sh          # 业务工程回到修改前 + 清空运行时数据
bash scripts/setup.sh          # 重新初始化
```
