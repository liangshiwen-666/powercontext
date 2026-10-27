# 规有所依：面向软件持续演进的 SDD 上下文管理系统（MiniMax 赛道）

本目录是 OceanBase PowerContext 2026 生态赛题 **MiniMax 赛道**的提交物，全部内容自包含
（官方 `2026-competition-ecosystem` 分支的 `minimax/` 目录原本只有 `.gitkeep`）。

目标：让 AI 开发助手在改代码前拿到**当前生效的规格、需要修改的代码、用于验收的测试**，
并在任务中断/进程重启后**复用已保存的任务进展**。本仓库用一次真实的局部修改
（订单查询服务增加筛选能力）完整验证了这条链路。

## 一句话流程

```
任务描述 → seekdb 检索规格/代码/测试 → 上下文包（含真实 path:line 引用）
        → MiniMax 生成 unified diff → 应用补丁 → 目标测试由红转绿 + 回归测试全绿
        → PowerContext 保存任务状态 → 新进程读回并用于下一步
```

## 目录

| 路径 | 说明 |
|---|---|
| `sddctx/` | 上下文模块（Python 包 + CLI），含 seekdb 资料层、PowerContext 状态层、MiniMax 客户端、补丁与验证 |
| `demo/` | 业务工程：FastAPI 订单查询服务 + 规格（v1.0 历史版 / v2.0 生效版）+ 目标/回归测试 |
| `patches/` | MiniMax 生成的补丁及哈希/记录索引 |
| `records/minimax/` | MiniMax 开发记录（请求/响应原文、参数、usage、trace id、哈希链） |
| `records/db/` | seekdb 初始化与检索 SQL 记录（真实执行输出） |
| `records/powercontext/` | 任务状态写入/读回记录 |
| `evidence/` | 上下文包、before/after 代码快照（`snapshots/`）、测试日志（命令、退出码、计数）、验证结论 |
| `scripts/` | 一键脚本：`setup.sh` / `demo.sh` / `spec-update.sh` / `check-env.sh` / `reset.sh` |
| `docs/` | [运行说明](docs/runbook.md) · [验证样例](docs/sample.md) · [一页技术说明](docs/tech-note.md) |

## 快速开始（评审复现）

```bash
# 0. 准备 seekdb（已安装可跳过；二选一，见 docs/runbook.md）
#    docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb

# 1. 安装依赖 + 初始化数据库
bash scripts/setup.sh

# 2. 一键跑完整验证样例
bash scripts/demo.sh

# 3. 查看证据
cat evidence/verification.json
cat evidence/run.json
```

`demo.sh` 不需要 MiniMax API Key：它重放 `patches/0001-orders-filters.patch`
（该补丁由 MiniMax 生成并记录在 `records/minimax/` 与 `patches/*.meta.json` 中）。
如需现场重新调用模型：

```bash
export MINIMAX_API_KEY=sk-...
python -m sddctx run --live        # 生成 → 应用 → 测试 → 记录，失败会自动带测试输出重试
```

## 六项验收对照

| 验收项 | 本仓库证据 |
|---|---|
| 能够运行 | `scripts/setup.sh` + `scripts/demo.sh`；`docs/runbook.md` 给出依赖版本、seekdb 初始化与调用入口 |
| MiniMax 实际参与 | `records/minimax/*.json`（完整请求/响应）+ `patches/0001-orders-filters.patch` + `patches/*.meta.json`（上下文 sha256 ↔ 响应 sha256 ↔ 补丁 sha256） |
| 上下文正确 | `evidence/context/*.json`：每条含真实 `path:line` 引用；`scripts/spec-update.sh` 演示规格更新重导入后上下文变化 |
| 数据库实际参与 | `records/db/*.json`：建表 DDL、入库行数、检索 SQL 与命中结果；`store.search` 的命中直接构成上下文的 citations |
| 状态能够复用 | `evidence/run.json` 中 PowerContext outcome；`scripts/demo.sh` 最后用**新进程** `state show` 读回 |
| 代码修改有效 | `evidence/verification.json`：同一组测试 before（target 6 failed / regression 18 passed）与 after（6 passed / 18 passed）的完整日志；`evidence/snapshots/` 为修改前后代码快照 |

## 技术栈（实际采用的方式与版本）

| 技术 | 版本 / 方式 |
|---|---|
| MiniMax | HTTP API `POST https://api.minimax.cn/v1/chat/completions`（OpenAI 兼容），`model=MiniMax-M3.1-Flash`，Bearer 鉴权，无 SDK 依赖（`requests`） |
| seekdb | seekdb 1.4.0 `5.7.25-OceanBase seekdb-v1.4.0.0`；MySQL 协议客户端 `pymysql`；全文索引 `WITH PARSER ik` |
| PowerContext | PyPI `powercontext==1.4.1`，进程内 `builtin` runtime + 文件型 SQLite（`sqlite+aiosqlite:////.../pc.db`） |
| 业务工程 | Python 3.11+ / FastAPI / pytest |

## 第三方组件与来源

| 组件 | 来源 / 许可 | 在本作品中的用途 | 是否修改 |
|---|---|---|---|
| PowerContext 1.4.1 | PyPI `powercontext`（Apache-2.0，<https://github.com/oceanbase/powercontext>） | 任务状态持久化与跨进程复用（进程内 builtin runtime + 文件 SQLite） | 未修改源码，仅调用公开 API |
| seekdb 1.4.0 | OceanBase 社区发行包 / `oceanbase/seekdb` 镜像 | 工程资料（规格/代码/测试）存储与中文全文检索 | 未修改 |
| MiniMax API | MiniMax 开放平台（`MiniMax-M3.1-Flash`，OpenAI 兼容 HTTP） | 依据上下文生成代码修改 | — |
| `demo/`、`sddctx/` | 本作品原创 | 业务工程与上下文模块本体 | — |

## 平台提交信息

测评平台的仓库地址锁定为团队仓库 `https://github.com/liangshiwen-666/powercontext-fork.git`；
本目录内容提交在分支 `2026-competition-ecosystem` 上，平台申报的 commit 以该分支的实际提交为准。

## 重置

```bash
bash scripts/reset.sh          # 业务工程回到修改前 + 清空运行时数据
bash scripts/setup.sh          # 重新初始化
```
