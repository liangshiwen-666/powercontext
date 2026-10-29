# 一页技术说明：SDD 上下文模块的设计与分工（V1）

> V1（1.0.0）｜ 变更见 [CHANGELOG.md](../CHANGELOG.md) ｜ 全部文件可行性审计见 [audit-v1.md](audit-v1.md)

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
│ test        │ │ (server/嵌入) │ │ + SQLite 文件       │
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
   │ (完整文件输出)     │    └──────────────────┘
   └────────┬─────────┘
   ┌────────▼─────────┐    ┌──────────────────┐
   │ patch.py(纯Python)│───▶│ demo/apply       │
   │ verify.py        │    │ before/after 矩阵 │
   └──────────────────┘    └──────────────────┘
```

一次 `python -m sddctx run` 完成：导入资料 → 选上下文 → 取补丁 → 应用 → 验证 → 存状态。

## 2. 上下文选择方式（为什么不是"固定答案"）

上下文完全由数据驱动，分三步：

1. **需求精确匹配**：从任务描述抽取 `REQ-ORD-006` 形式的规格编号，逐条到 seekdb 检索命中的
   规格小节（`MATCH(body) AGAINST(...)` + 需求号比对），保证"本次任务适用的规格条目"不靠模型猜。
2. **符号定位 + 关键词召回**：从任务/规格的反引号中提取代码符号（`list_orders`、`parse_status`
   等）做定向检索，再用关键词补充召回；命中的符号带 `path:line` 引用，并把相关文件全文注入
   上下文，让补丁能落到正确文件。
3. **测试与状态注入**：目标测试按需求号匹配后按套件枚举（6 条），回归测试**整套枚举**（18 条，
   不依赖关键词运气）；从 PowerContext 读回已完成/待办/验证结果作为 `next_action`。

确定性：记录与测试条目按稳定键排序，同一输入两次运行的 `context_sha256` 完全一致；
规格更新后重新 `ingest` 会改变检索结果与哈希（`scripts/spec-update.sh` 实测 A/B 两份上下文）。

## 3. 三项指定技术的分工

| 技术 | 在本项目中的实际职责 | 不承担什么 |
|---|---|---|
| **MiniMax**（`MiniMax-M3.1-Flash`，OpenAI 兼容 HTTP） | 唯一负责**生成代码修改**：接收"任务 + 规格 + 代码 + 测试 + 任务状态"上下文，返回被修改文件的**完整新内容**；程序做 AST 语法校验 → 写盘 → 跑目标测试，失败把校验/测试输出回喂重试；全部通过后用纯 Python 引擎生成 unified diff（全过程落盘到 `records/minimax/`） | 不负责检索、不负责判定验收（测试是本地真实结果） |
| **seekdb 1.4.0**（MySQL 协议，`pymysql`；嵌入式 `pylibseekdb` 可选） | 存放工程资料与索引：`ctx_doc` 按 spec/code/test 切块，`FULLTEXT ... WITH PARSER ik` 支持中文检索；上下文的每条引用都来自它的查询结果 | 不存业务数据、不承担任务状态 |
| **PowerContext 1.4.1**（进程内 builtin + SQLite） | 保存并复用任务状态：决策/已完成/待办/验证结果以 Atomic Memory 写入；`record_task_outcome` 走 work contract → handoff → ack → outcome 链；模块重启后由**新进程**读回（`state export`），并用于下一次上下文的 `next_action` | 不替代 seekdb 的资料检索 |

## 4. 关键取舍与验证（V1 审计后）

| 取舍 | 原因 | 证据 |
|---|---|---|
| PowerContext 用**进程内 builtin + SQLite**，不起 Server | 少一个常驻进程/端口；能力集合与 Server 路线一致；V1 补了并发 bootstrap 重试与异常包装 | `records/powercontext/`、并发探针 |
| seekdb 用 **server 路线** 为主、嵌入式为可选备选 | 评审机一条 docker 命令即可；嵌入式已实测（无 server 也能完整跑资料层），但仅 Linux/macOS、单进程 | `docs/audit-v1.md`、`tests/test_store.py` |
| 补丁应用改为**纯 Python 引擎**（V1） | 评审机可能没有 GNU `patch`，macOS BSD `patch` 参数语义不同；纯 Python 精确匹配、原子写盘、可审计错误 | `sddctx/patch.py`、29 条单测 |
| 补丁**重放**为默认，`--live` 才调模型 | 评审不需要我们的 API Key，也能复现"改代码 + 测试由红转绿" | `scripts/demo.sh` |
| 目标测试失败是**功能失败**，不是环境失败 | 题面禁止用缺依赖/起不来充当"修改前失败"；`verify` 用 JUnit testcase 集合证明前后是同一组测试 | `evidence/logs/before-target.json`、`evidence/verification.json` |
| 测试库**每进程唯一**（V1） | 两个 pytest 进程共用 seekdb 时互不干扰（修复过偶发失败） | `tests/conftest.py`、并发隔离实测 |

## 5. 边界与已知限制

- 目标是**局部修改**（题面要求），不是全自动软件工程；上下文选择依赖规范的规格条目编号。
- 中文全文检索依赖 seekdb 的 `ik` parser；换 `ngram` 可跑但准确率下降。
- PowerContext 进程内 API 属于 Core SDK（不在 HTTP 契约保证范围），已用薄封装隔离。
- 未配置 embedding，因此只做全文检索，不做向量召回（题面未要求）。
- 补丁引擎精确匹配、无 fuzz，不支持 rename/binary；嵌入式 seekdb 未验证跨进程并发。
