# 技术使用记录（records/）

本目录收录三项指定技术的实际使用记录，全部由程序在运行时生成，可按文末命令重新生成核对。

> 说明：记录中的 `cwd`、`command`、`state/archive` 等字段可能包含**生成记录时的机器路径**
> （例如 `/mnt/d/...`、`/home/<user>/.venvs/...`）。这些是运行环境信息，不影响证据核对：
> 计数、哈希、citation、JSON 结构均可独立重算（见 `docs/audit-v1.md`）。

## `records/minimax/` —— MiniMax 开发记录

每次调用 MiniMax 生成修改都会落盘一个 JSON（文件名 `<UTC 时间戳>-<tag>.json`）：

| 字段 | 含义 |
|---|---|
| `endpoint` / `model` / `request.body` | 实际调用地址、模型与请求体（含发给模型的完整上下文） |
| `request.headers` | 脱敏后的请求头（`Authorization` 仅保留 `***REDACTED***`，另存 `api_key_sha256`） |
| `response.body` / `response.content` | 原始响应（含模型返回的完整文件内容；`=== FILE: app/xxx.py ===` 段） |
| `usage` / `trace_id` / `elapsed_ms` | token 用量、服务端 trace id、耗时 |
| `chain.*_sha256` | 任务文本 → 上下文 → 请求 → 响应 → 补丁的哈希链（可重算） |

模型被要求输出**被修改文件的完整新内容**（而不是 diff）：LLM 直接生成 unified diff 时 hunk 头
经常不自洽，评审无法复现；项目对模型输出做语法校验、写入文件、跑目标测试，全部通过后才用
`difflib` 机械生成 `patches/0001-orders-filters.patch` 作为 before→after 的可重放补丁。
失败尝试（格式错误/测试未过）同样完整保留，可看到"反馈重试"的开发过程。

`index.jsonl` 是每次调用的单行索引（`record_path` 为相对 `minimax/` 的路径），便于对照 `patches/*.patch`。

重新生成：`MINIMAX_API_KEY=... bash scripts/live-generate.sh`（评审复现不需要 Key）。

## `records/db/` —— seekdb 查询记录

`python -m sddctx run`（或 `python -m sddctx db evidence`）会执行并记录以下真实 SQL：

1. `SHOW CREATE TABLE ctx_doc` —— 建表语句（含 `FULLTEXT KEY ft_body (body) WITH PARSER ik`）；
2. `SELECT kind, COUNT(*) ... GROUP BY kind` —— 入库数量；
3. 按类型的检索：规格（`REQ-ORD-006 筛选 状态`）、代码（`status created_after min_amount …`）、测试
   （`筛选 状态 时间范围 金额下限 INVALID_FILTER …`）；
4. 一条 `UNION ALL` 语句同时返回 spec/code/test 三类命中（每类 2 条）。

文件中保存了每条 SQL 与**实际返回的行**，可直接复核上下文里的 `path:line` 引用是否真实。

## `records/powercontext/` —— 状态写入与复用记录

`state-*.json` 记录 PowerContext（进程内 builtin runtime + SQLite）中的任务状态：

- `records`：Atomic Memory 条目（决策 / 已完成 / 待办 / 验证结果）及其 artifact 引用（稳定排序）；
- `prepared`：`context.prepare()` 返回的 bounded context（带 citation）。

跨进程复用验证：`bash scripts/state-readback.sh` 在**新进程**中读取同一 `.data/powercontext/pc.db`，
写出 `records/powercontext/state-readback.json`（与写入进程的 `state-final-*.json` 记录一致）。

## 重新生成命令

```bash
# 全部记录（live 模式会真实调用 MiniMax）
MINIMAX_API_KEY=... bash scripts/live-generate.sh
# 仅数据库/状态记录（重放模式，不需要 Key）
bash scripts/demo.sh
```
