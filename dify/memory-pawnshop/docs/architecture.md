# 《记忆当铺》技术架构（v0 草案）

> 赛题：Dify × OceanBase / Just for Fun —— 用 Dify 做一个好玩的智能应用
> 交付分支：`2026-competition-ecosystem`，资产目录 `dify/`
> 本文只描述技术骨架，玩法与内容见 `design.md`

## 1. 一句话

玩家在 Dify 聊天窗口里玩"记忆当铺"：**Dify 负责流程编排与叙事，PowerContext 负责"客人的记忆"（可写入、可召回、可篡改、跨会话保留），Dify 知识库负责"镇子的档案"（客观事实检索），seekdb 可作为 PowerContext 的嵌入式存储后端用于记忆与检索的持久化。**

## 2. 组件与职责

| 组件 | 版本 | 在我们的作品里干什么 | 是否必需 |
| --- | --- | --- | --- |
| **Dify** | 1.18.0（Docker Compose 自托管） | Chatflow 入口：意图判断、节点编排、LLM 叙事、知识库检索、HTTP 调用、会话变量（信任/怀疑/轮次/当票） | 必需 |
| **PowerContext Server** | 1.4.1（PyPI，HTTP API，端口 17429） | 每位客人一个 scope；写入"真实/被植入/存疑"记忆；每轮召回成"客人此刻相信的事"；复述产生新 revision（记忆变异）；抹除=inactive；跨会话保留 | 必需 |
| **Dify 知识库** | Dify 内置 | 雾河镇档案（镇志/账本/讣告/传闻）向量检索；"翻档案"节点的事实来源 | 必需 |
| **seekdb** | 1.4.x（嵌入式） | PowerContext 的存储与索引后端（`POWERCONTEXT_SERVER_DATABASE_KIND=seekdb`）：记忆正文持久化 + 全文/向量检索 | 可选加分（跑不通降级 SQLite，作品仍合规） |
| SiliconFlow `BAAI/bge-m3` | API | PowerContext 记忆向量（1024 维）与 Dify 知识库 embedding | 必需 |
| DeepSeek API | `deepseek-chat` | 所有 LLM 节点（意图/台词/判定/结算） | 必需 |

## 3. 每个 turn 的数据流

```
玩家在 Dify 聊天窗输入
        │
        ▼
[Start] → [LLM: 意图与动作解析 JSON] ──┐
        │                              ├─ action=查档案 → [知识库检索: 档案] →（拼接上下文）
        │                              ├─ action=塞记忆 → [HTTP: PC 写记忆] →（拼接上下文）
        │                              └─ action=说话/其它 →（拼接上下文）
        ▼
[HTTP: PC 检索本客人 scope 的记忆]（query=本轮输入，limit N）
        │
        ▼
[LLM: 客人台词生成]（输入：档案片段 + 召回记忆 + 会话状态；输出：≤120 字台词）
        │
        ▼
[LLM: 判定]（输出严格 JSON：consistency / cited_memory / cited_archive / suspicion_delta）
        │
        ▼
[Code: 更新会话变量 trust/suspicion/ticket] → [Answer: 台词 + 当铺菜单 + 当票卡片]
        │
        ▼
[HTTP: PC 记忆整理]（把本轮"客人复述"写回：新 revision 或新条目）
```

要求：每轮 **≤2 次 LLM 调用**（解析+台词/判定合并落地时再压），HTTP 超时 15s，失败有兜底文案（"当铺的灯晃了一下"）。
纯文本输出，不用 Markdown 表格，避免聊天窗渲染问题。

## 4. PowerContext 使用面（技术结合的证据点）

- **scope 隔离**：`npc/<guest_id>`（如 `npc/ayuan`）；可选 `player/<player_id>` 记录玩家自己的"业障"（终局反转用）。
- **写入**：记忆正文 = 客人第一人称事实句；`tags` 带 `kind=truth|planted|doubt`、`case=<case_id>`。
- **召回**：每轮按玩家输入检索该 scope，取 top-k 注入台词 prompt。
- **revision（记忆变异）**：客人复述时产生新 revision，下一轮召回的是"变异后"版本 → 玩法上的"谎言长歪了"。
- **inactive（遗忘/抹除）**：玩家用"抹掉"操作 → 客人行为改变（对应 product 的 forget/reactivate）。
- **跨会话**：同一个 scope 在第二个 Dify 会话里继续可召回 → 验收样例的"新会话还记得你"。
- **Dashboard**（`POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true`）：截图作为"记忆读写记录"证据。

## 5. Dify 编排草图（Chatflow）

会话变量：`guest_id`、`round`、`trust`、`suspicion`、`tickets`（JSON 数组）、`phase`（opening/bargain/settle）。

节点：Start → LLM(意图 JSON) → IF/ELSE 分支（查档案 / 塞记忆 / 抹除 / 推进 / 结算）→ 合并 → HTTP(记忆召回) → LLM(台词) → LLM(判定) → Code(更新变量) → Answer。

> 待办：先用 Dify 导出一个空 Chatflow 的 DSL 作为模板，再增量加节点并每次重新导入验证（避免手写 DSL 结构错误）。

## 6. 部署与复现

**方式 A（本地开发，先跑通）**
1. WSL2 Ubuntu 内：`uv venv --python 3.12 && uv pip install "powercontext[cli,server]==1.4.1"`；`wsl_start_pc.sh` 启动，端口 17429，绑定 0.0.0.0。
2. Dify：`docker compose`（1.18.0，官方 compose）拉起到 `http://localhost:3000`；安装 DeepSeek 与 SiliconFlow 两个 provider。
3. Dify 里建知识库（导入 `data/archives.md` 切片）与 Chatflow（导入 `dify/*.yml`）。

**方式 B（评委复现）**
- `docker-compose.yaml`（Dify 官方）+ `docker-compose.pc.yaml`（PowerContext 服务，镜像/python:3.12 + pip 安装 + 卷挂载）
- `.env.example` 占位（两个 API Key、PC 地址、模型名），`seed` 脚本初始化 scope 与初始记忆，`verify` 脚本跑一遍验证样例并输出证据日志。
- 网络：Dify 容器通过 compose 网络访问 `http://powercontext:8000`；PC 需 `ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true`（或开启 token）。

## 7. 验收映射

| 验收项 | 我们的对应材料 |
| --- | --- |
| 能够运行 | compose + `.env.example` + 一键 seed/verify 脚本；DSL 导入说明 |
| 能体验主要功能 | `验证样例.md`：3 位客人各一条完整输入序列 + 预期表现 + 恢复初始状态方法 |
| 技术结合有效 | 节点执行记录（Dify 日志）、PowerContext memory id/revision、Dashboard 截图、seekdb 数据落盘截图 |
| 过程可复现 | 全部脚本 + 数据 + 占位配置；不依赖作者本地状态（seed 可重建） |

## 8. 风险与降级

| 风险 | 降级方案 |
| --- | --- |
| 嵌入式 seekdb 装不起来/版本冲突 | 保持 `DATABASE_KIND=sqlite`（默认），作品仍符合"PowerContext 单技术"要求，只少一个技术分点 |
| Dify 1.18 镜像拉取慢/资源不足 | 降到 1.17.1 官方 compose；或只起必需服务（api/worker/web/db/redis/nginx/vector store） |
| 远程 embedding 超时 | `RECALL_EMBEDDING_TIMEOUT_SECONDS=3`；超时自动降级词法检索，游戏不崩 |
| LLM 延迟高 | 融合节点减少调用次数；DeepSeek flash 模型；开启流式 |
| 玩家乱输入 | 意图解析兜底为"客人把话拉回菜单"，不会卡死 |
