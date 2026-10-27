# 记忆当铺 · Memory Pawnshop

> Dify × PowerContext × seekdb ｜ OceanBase 2026 大赛「Just for Fun」赛道作品
> 仓库分支：`2026-competition-ecosystem` ｜ 资产目录：`dify/memory-pawnshop/`

---

## 一、这是个什么游戏

**雾河镇有家当铺，当的不是金银，是你夜里想起来会疼的那一件。**

你是「灯下记忆当铺」的掌柜。客人来典当一段记忆换钱；你能翻镇子的档案（客观真相），也能往客人脑子里塞一段「更好的记忆」（主观记忆）。三位客人，三张当票，一场越聊越离奇的夜晚。

| | |
| --- | --- |
| 一句话钩子 | 客人说的都不是真话，而你能决定他们记住什么。 |
| 核心循环 | 开场 → 翻档案（查真）→ 调酒（塞假）→ 客人醒来（按他信的说）→ 开票结算 |
| 单局时长 | 3 位客人 × 3 巡，评委 10~15 分钟可完整体验 |
| 跨会话 | 同一位客人的记忆 scope 常驻，第二天推门进来，他记得你昨晚塞给他的那句话 |
| 结局 | S/A/B/C/D 五种，点题句固定为「记忆不是用来骗的。它是用来在雾里认出彼此的。」 |

**为什么好玩**：玩家不是在「聊天」，而是在**操作真相与记忆之间的落差**——翻到的档案越接近真相，当票越贵；塞的假记忆越深，客人越信；但铺子只有 3200 元本钱，骗到最后可能把自己当破产。

---

## 二、技术分工（谁在干什么）

| 技术 | 版本 | 在本作品里的实际作用 | 可核对的证据 |
| --- | --- | --- | --- |
| **Dify** | 1.18.0（Docker Compose 自托管） | Chatflow 承担核心流程编排：意图解析 LLM → IF/ELSE 分流 → HTTP 调用记忆库/档案柜 → 客人台词与判定 LLM → 开票 Code 节点 → 会话变量赋值 → Answer；共 26 个节点 | DSL 文件 `DSL/memory-pawnshop.yml`；Dify 运行日志/节点执行记录 |
| **PowerContext** | 1.4.1（PyPI，HTTP API） | 记忆语义层：每位客人一个 scope（`npc/ayuan` 等），当铺档案一个只读 scope（`archive`）；负责写入、混合检索召回、PreparedContext、`revision`（记忆被复述改写）、`inactive`（遗忘/抹除）、跨会话保留 | 当票上的 `scope`/`memory_id`/`R{revision}`；`scripts/pc_smoke.py` 输出的读写记录 |
| **seekdb** | 1.4.x（嵌入式） | PowerContext 的**存储与检索引擎**（`POWERCONTEXT_SERVER_DATABASE_KIND=seekdb`）：所有记忆与档案的持久化、全文索引与向量索引都在 seekdb 里 | PowerContext 容器内 `/data/seekdb` 数据目录；`/health/ready` 的 `database=ready`；检索命中 `matched_by:["text","vector"]` |
| DeepSeek API | `deepseek-chat` | 意图解析、客人台词、判定 JSON | Dify 模型配置 |
| SiliconFlow `BAAI/bge-m3` | 1024 维 | PowerContext 的查询/索引向量（记忆与档案的语义召回） | `scripts/pc_smoke.py` 的 `matched_by` 含 `vector` |

> 设计要点：**「档案」和「记忆」用的是同一套 PowerContext API、不同的 scope**——档案是「可核对的过去」，记忆是「客人相信的过去」，两者在同一个记忆系统里对照，这正是玩法本身。

---

## 三、运行环境

| 项 | 要求 |
| --- | --- |
| 操作系统 | Linux（x86_64）或 Windows + WSL2；需要 Docker Engine 24+ 与 Docker Compose v2.24+ |
| 资源 | 4 核 / 8 GB 内存以上（Dify 全家桶 + 嵌入式 seekdb） |
| 网络 | 能访问 Docker Hub（或已配置可用镜像加速）、`api.deepseek.com`、`api.siliconflow.cn`，以及 PyPI（构建 PowerContext 镜像） |
| 端口 | Dify Web `3000`（可改）；PowerContext 映射到宿主 `127.0.0.1:17429` |
| 模型 Key | DeepSeek API Key、SiliconFlow API Key（用于 bge-m3 embedding） |

---

## 四、从零部署（评审按此步骤即可复现）

> 以下命令在「你要放 Dify 的目录」执行，全部使用占位配置，不会提交真实 Key。

### Step 0. 拿到 Dify 1.18.0 的 docker 目录

```bash
git clone --depth 1 --branch 1.18.0 --filter=blob:none --sparse \
  https://github.com/langgenius/dify.git dify
cd dify && git sparse-checkout set docker && cd docker
```

### Step 1. 应用本作品的配置

```bash
# 把本目录 docker/ 下的文件复制进 Dify 的 docker/ 目录
cp -r <本作品目录>/docker/.env.game .
cp -r <本作品目录>/docker/docker-compose.game.yaml .
cp -r <本作品目录>/docker/pc .

cp .env.example .env
cat .env.game >> .env          # 追加游戏所需配置（端口/向量库/SSRF/密钥占位）
```

打开 `docker/.env`，把两处占位改成你自己的 Key（也可以先留空，到 Dify 控制台里再填）：

```bash
GAME_SILICONFLOW_API_KEY=sk-你的硅基流动Key     # 供 PowerContext 做 bge-m3 embedding
```

`GAME_SILICONFLOW_API_KEY` 只给 powercontext 容器用；Dify 侧的 DeepSeek/SiliconFlow Key 在 Step 5 的控制台里填。

### Step 2. 构建并启动

```bash
# 只启动本作品需要的服务（不启用向量库/KB 容器；档案与记忆都在 PowerContext/seekdb 里）
COMPOSE_PROFILES=postgresql docker compose \
  -f docker-compose.yaml -f docker-compose.game.yaml \
  up -d --build
```

首次会构建 `powercontext` 镜像（从阿里云 PyPI 镜像安装 PowerContext 1.4.1 + 嵌入式 seekdb 依赖），约 2~5 分钟。

启动后自检：

```bash
curl -s http://127.0.0.1:17429/health/ready
# 期望：{"status":"ready","checks":{... "database":"ready","inference.embedding":"ready" ...}}
docker compose ps     # api / worker / web / db / redis / nginx / plugin_daemon / sandbox / powercontext 均 Up
```

### Step 3. 初始化游戏数据

```bash
cd <本作品目录>
pip install -r requirements.txt    # 仅标准库 + pyyaml，见 scripts/
python3 scripts/seed_pc.py         # 写入 4 个 scope、28 条档案、10 条客人初始记忆；生成 data/pc_manifest.json
```

执行成功标志：输出 `scope archive -> scp_...`、`wrote 28 archive memories`、`wrote 3/3/4 npc memories`，并做一次档案检索自检（应返回 A-03/Z-02/R-02 等）。

### Step 4. 导入 DSL

Dify 控制台（`http://localhost:3000`）→ 首次进入先注册管理员账号 → **创建应用 → 导入 DSL 文件**，选择 `DSL/memory-pawnshop.yml`。

导入后打开应用 → 右上角「发布」→ 在「探索/运行」里即可对话。

### Step 5. 配置模型供应商

在 Dify 控制台 → 设置 → 模型供应商：

1. **DeepSeek**：填 DeepSeek API Key；模型 `deepseek-chat`（用于意图解析、客人台词、判定）。
2. **SiliconFlow（硅基流动）**：填 SiliconFlow API Key；embedding 模型 `BAAI/bge-m3`（用于 Dify 的 embedding 能力；本作品的知识库未使用，但该供应商配置会让模型列表完整）。

> 说明：本作品**不使用 Dify 知识库**。世界档案存储在 PowerContext 的 `archive` scope 中，由 HTTP 节点检索——这样部署只需两个服务，也避免了「知识库向量库」这一层额外依赖。若你希望改成 Dify 知识库形态，可把 `data/archives.md` 导入知识库并替换 `HTTP·档案检索` 节点为知识检索节点。

### Step 6. 开始玩

在应用对话窗口输入：

```
开门
```

看到第一位客人阿沅的开场白与「当铺菜单」即成功。

入口：`http://localhost:3000/app/<app-id>`（或 探索 → 记忆当铺）。

---

## 五、验证样例

见 [VERIFY.md](VERIFY.md)：包含 3 位客人的完整输入序列、预期表现、跨会话记忆验证，以及如何一键恢复到初始状态。

---

## 六、外部服务与网络要求

| 依赖 | 用途 | 获取方式 | 网络 |
| --- | --- | --- | --- |
| Dify 1.18.0 镜像 | 编排与 UI | Docker Hub `langgenius/dify-*` | 需能拉取 Docker Hub 或配置镜像加速 |
| PowerContext 1.4.1 | 记忆 API | PyPI（容器构建时安装） | PyPI / 国内镜像 |
| DeepSeek API | LLM | https://platform.deepseek.com | `api.deepseek.com` |
| SiliconFlow API | Embedding | https://siliconflow.cn | `api.siliconflow.cn` |

PowerContext 容器需要访问 SiliconFlow（embedding）与 Dify 的 HTTP 节点需要访问 PowerContext（同一 compose 网络内 `http://powercontext:8000`，无需额外配置）。

---

## 七、已知限制

1. **Dify 1.18 的模型供应商是插件**：首次进入控制台需要在「模型供应商」里安装/启用 DeepSeek 与 SiliconFlow，需要能访问 Dify 插件市场；若内网无法访问，可提前离线安装对应 `.difypkg`（见 Dify 文档）。
2. 嵌入式 seekdb 首次启动需要初始化数据目录（约 20~60 秒），期间 `/health/ready` 返回 503；等待即可。
3. 每轮对话约 2 次 LLM 调用（意图 + 台词/判定），DeepSeek 下 4~8 秒/轮；可接受，但非流式。
4. 当票文案里的中文列宽在不同字体下可能轻微错位，不影响内容。
5. 本作品为单机单人体验，未做多用户鉴权；PowerContext 默认关闭鉴权（仅监听容器网络 + 本机 17429）。

---

## 八、目录结构

```
dify/memory-pawnshop/
├── README.md                  # 本文件
├── VERIFY.md                  # 验证样例与操作步骤
├── DSL/
│   ├── memory-pawnshop.yml    # Dify Chatflow DSL（导入用）
│   └── generator/             # DSL 生成器（可重新生成 DSL；Code 节点源码在此）
├── data/
│   ├── archives.md            # 雾河镇档案 28 条（游戏语料）
│   ├── guests.json            # 3 位客人结构化数据
│   └── seed_memories.json     # PowerContext 初始记忆
├── docker/
│   ├── .env.game              # 叠加到 Dify .env 的配置
│   ├── docker-compose.game.yaml
│   └── pc/                    # PowerContext 服务镜像（Dockerfile + entrypoint）
├── scripts/
│   ├── seed_pc.py             # 初始化 scope/档案/记忆
│   ├── pc_smoke.py            # 记忆闭环自检（scope/写入/混合检索/prepare/revision/forget）
│   └── verify_game.py         # 离线执行 DSL Code 节点并跑完整一局（无需 Dify 即可验证逻辑）
└── evidence/                  # 运行证据（PC 验证 JSON、离线全流程日志、Dify 截图）
```

---

## 九、提交材料对照

| 赛题要求 | 本作品对应 |
| --- | --- |
| Dify DSL | `DSL/memory-pawnshop.yml`（含全部节点、提示词、Code 源码） |
| 运行说明 | 本 README（创意/玩法/分工/环境/步骤/入口/网络/限制） |
| 验证样例 | `VERIFY.md` + `scripts/verify_game.py` + `evidence/` |
| seekdb 部署与读写验证 | `docker/pc/Dockerfile` + `.env.game`（`DATABASE_KIND=seekdb`）+ `evidence/pc_smoke_seekdb.json` |
| 预置记忆/互动状态初始化 | `scripts/seed_pc.py`、`data/seed_memories.json`、`data/pc_manifest.json` |
| 自定义后端/源码 | `docker/pc/`（PowerContext 服务封装）、`DSL/generator/`（DSL 生成器与 Code 节点源码） |
| 复用声明 | 无第三方工作流复用；Dify 为官方 1.18.0，PowerContext 为官方 1.4.1；`data/` 下内容为原创 |
