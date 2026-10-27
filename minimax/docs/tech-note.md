# 一页技术说明：SDD 上下文模块的设计与分工

## 1. 模块结构

```
                          ┌────────────────────────── 任务描述（demo/tasks/*.md）
                          │
┌─────────────────────────▼─────────────────────────┐
│ CLI: python -m sddctx (init/ingest/context/…)      │
└───┬───────────────┬────────────────────┬──────────┘
    │               │                    │
┌───▼─────────┐ ┌───▼──────────┐ ┌───────▼────────────┐
│ ingest.py   │ │ store.py     │ │ pcstore.py         │
│ 切块        │ │ seekdb       │ │ PowerContext       │
│ spec/code/  │ │ FTS(ik) 检索 │ │ builtin runtime    │
│ test        │ │              │ │ + SQLite 文件       │
└───┬─────────┘ └───┬──────────┘ └───────┬────────────┘
    │               │                    │
    └───────┬───────┴────────┬───────────┘
            │                │
      ┌─────▼──────┐   ┌─────▼──────────────┐
      │ context.py │   │ state: done/todo/  │
      │ 上下文包    │   │ verification/next  │
      └─────┬──────┘   └────────────────────┘
            │
   ┌────────▼─────────┐    ┌──────────────────┐
   │ minimax_client   │───▶│ records/minimax/ │
   │ (unified diff)   │    └──────────────────┘
   └────────┬─────────┘
   ┌────────▼─────────┐    ┌──────────────────┐
   │ patch.py         │───▶│ demo/apply       │
   │ verify.py        │    │ before/after 矩阵 │
   └──────────────────┘    └──────────────────┘
```

一次 `python -m sddctx run` 完成：导入资料 → 选上下文 → 取补丁 → 应用 → 验证 → 存状态。

## 2. 上下文选择方式（为什么不是"固定答案"）

上下文完全由数据驱动，分三步：

1. **需求精确匹配**：从任务描述里抽取 `REQ-ORD-006` 形式的规格条目编号，逐条到 seekdb
   检索命中的规格小节（`MATCH(body) AGAINST(...)` + 需求号比对），保证"本次任务适用的规格条目"
   不靠模型猜。
2. **关键词召回 + 代码符号定位**：用任务/规格中的关键词与反引号里的符号名（`list_orders`、
   `parse_pagination` 等）检索代码块；命中的符号附带 `path:line` 引用，并把相关文件全文注入
   提示词，让补丁能直接落到正确的文件。
3. **状态注入**：从 PowerContext 读回已完成/待办/验证结果，作为上下文的 `next_action`。

每条条目都带 `citation = 相对路径:起始行-结束行`，可直接打开核对。规格更新后重新 `ingest`，
检索结果随库内容变化，上下文 JSON 的 `context_sha256` 也随之变化（见 `scripts/spec-update.sh`）。

## 3. 三项指定技术的分工

| 技术 | 在本项目中的实际职责 | 不承担什么 |
|---|---|---|
| **MiniMax**（`MiniMax-M3.1-Flash`，OpenAI 兼容 HTTP） | 唯一负责**生成代码修改**：接收"任务 + 规格 + 代码 + 测试 + 任务状态"上下文，返回被修改文件的**完整新内容**；项目把模型输出机械转换为 unified diff（LLM 直接生成 diff 的 hunk 头经常不自洽），并在目标测试失败/文件非法时把错误反馈回喂重试（全过程落盘到 `records/minimax/`） | 不负责检索、不负责判定验收（测试是本地跑的真实结果） |
| **seekdb 1.4.0**（MySQL 协议，`pymysql`） | 存放工程资料与索引：`ctx_doc` 表按 spec/code/test 切块，`FULLTEXT ... WITH PARSER ik` 支持中文检索；上下文的每条引用都来自它的查询结果 | 不存业务数据、不承担任务状态 |
| **PowerContext 1.4.1**（进程内 builtin + SQLite） | 保存并复用任务状态：决策/已完成/待办/验证结果以 Atomic Memory 写入；`record_task_outcome` 走 work contract → handoff → ack → outcome 链；模块重启后读回，并用于下一次上下文的 `next_action` | 不替代 seekdb 的资料检索 |

## 4. 关键取舍与验证

| 取舍 | 原因 | 证据 |
|---|---|---|
| PowerContext 用**进程内 builtin + SQLite**，不起 Server | 少一个常驻进程/端口，评审环境更不容易翻车；能力集合与 Server 路线一致 | `records/powercontext/` |
| seekdb 用 **server 路线**（MySQL 协议） | Windows 侧也能连；评审机可用 `oceanbase/seekdb` 镜像一条命令起 | `records/db/` |
| 补丁**重放**为默认，`--live` 才调模型 | 评审不需要我们的 API Key，也能复现"改代码 + 测试由红转绿" | `scripts/demo.sh` |
| 目标测试失败是**功能失败**，不是环境失败 | 题面明确禁止用缺依赖/起不来充当"修改前失败" | `evidence/logs/before-target.json` |

## 5. 边界与已知限制

- 目标是**局部修改**（题面要求），不是全自动软件工程；上下文选择依赖规范的规格条目编号。
- 中文全文检索依赖 seekdb 的 `ik` parser；换 `ngram` 可跑但准确率下降。
- PowerContext 进程内 runtime 的 API 属于 Core SDK（不在 HTTP 契约保证范围），已用薄封装隔离。
- 未配置 embedding，因此只做全文检索，不做向量召回（题面未要求）。
