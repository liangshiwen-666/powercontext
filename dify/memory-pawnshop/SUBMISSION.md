# 提交元数据（SUBMISSION）

> 本文件用于把"仓库 / 上游 / 分支 / commit / 文件清单"一次性对齐，填表时直接照抄。

## 一、填表值

| 表单字段 | 填写内容 |
| --- | --- |
| 代码仓库地址 | `https://github.com/liangshiwen-666/powercontext-fork.git`（平台已登记，锁定不可改） |
| **Branch** | **`2026-competition-ecosystem`** |
| **Commit id** | 本目录所在分支的最新提交，**40 位完整 SHA**，即 `git rev-parse HEAD` 的输出<br>（若平台只接受短 SHA，取前 40 位中的前 7 位也可定位；**建议填完整 40 位**） |

> 平台侧说明：本仓库是 `oceanbase/powercontext` 的**正规 fork**（GitHub `fork: true`），
> `2026-competition-ecosystem` 分支直接基于官方基线 `d647275` 开发，`behind_by = 0`。

## 二、上游与基线（可核对）

| 项 | 值 |
| --- | --- |
| 上游仓库 | <https://github.com/oceanbase/powercontext> |
| 官方基线分支 | `2026-competition-ecosystem` |
| 官方基线 commit | `d647275c308e09b7b623f322a26f5c4e85734fb0` |
| 本团队 fork | <https://github.com/liangshiwen-666/powercontext-fork> |
| 提交分支 | `2026-competition-ecosystem` |
| 分支与基线关系 | `status = ahead`，`ahead_by = 1171`，`behind_by = 0`，`merge_base = d647275` |
| 本作品路径 | `dify/memory-pawnshop/` |

核对方式（无需登录）：

```bash
curl -s "https://api.github.com/repos/oceanbase/powercontext/compare/2026-competition-ecosystem...liangshiwen-666:powercontext-fork:2026-competition-ecosystem" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status'], d['ahead_by'], d['behind_by'], d['merge_base_commit']['sha'])"
# 期望输出：ahead 1171 0 d647275c308e09b7b623f322a26f5c4e85734fb0
```

## 三、文件清单（对照评分要求）

| 评分要求 | 仓库内文件 | 说明 |
| --- | --- | --- |
| 可运行应用 | `DSL/memory-pawnshop.yml` | Dify 1.18 Chatflow DSL，26 节点 / 25 边，直接导入即可（导入 0 警告） |
| 复现步骤 | `README.md` + `VERIFY.md` | 环境、启动、导入、逐客人验证样例 |
| 初始化数据 | `data/archives.md`、`data/guests.json`、`data/seed_memories.json` | 28 条档案 + 3 位客人 + 10 条种子记忆 |
| 配套源码 | `DSL/generator/`、`scripts/`、`docker/` | DSL 生成器、Code 节点源码、seed/smoke/verify/ops 脚本、PowerContext 容器化 |
| 实测证据 | `evidence/` | Dify 逐轮实录、PC 读写记录、seekdb 健康检查、离线仿真日志 |
| 技术说明 | `docs/design.md`、`docs/architecture.md`、`docs/pc-api.md` | 设计、架构、PowerContext API 手册 |

## 四、已验证环境

- Dify **1.18.0**（Docker Compose 自托管，`COMPOSE_PROFILES=postgresql`）
- PowerContext **1.4.1**（PyPI，HTTP API），存储引擎 **seekdb**（`DATABASE_KIND=seekdb`）
- 模型：DeepSeek 插件 `langgenius/deepseek`，模型名 **`deepseek-flash`**（该插件在 1.18 中无 `deepseek-chat`）
- 检索：SiliconFlow `BAAI/bge-m3`（1024 维），PowerContext hybrid 检索（`matched_by:["text","vector"]`）

## 五、证据索引

| 文件 | 内容 |
| --- | --- |
| `evidence/dify_run_full.json` | 经 Dify Service API 的 14 轮完整对局（3 客人 + 3 张当票 + 结局 S） |
| `evidence/dify_run.json` | 另一局 6 轮快速验证（导入→发布→API Key→对话） |
| `evidence/pc_ready_*.json` | PowerContext `/health/ready`（`database=ready`） |
| `evidence/pc_stats_*.json` | 容器内 seekdb 数据目录与进程状态 |
| `evidence/containers_*.txt` | `docker ps` 容器清单（Dify + PowerContext 均 healthy） |
| `evidence/pc_smoke_seekdb.json` | seekdb 后端下的 scope/写入/混合检索记录 |
| `evidence/pc_smoke_sqlite.json` | sqlite 后端对照（证明 seekdb 是可切换的存储后端） |
| `evidence/offline_sim.log` | 离线仿真（直接执行 DSL Code 节点 + 真实 PowerContext）全流程 |
