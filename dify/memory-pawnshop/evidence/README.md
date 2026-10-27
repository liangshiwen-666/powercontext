# 运行证据 / Evidence

本目录收集「技术结合实际起作用」的可核对记录。

| 文件 | 说明 | 怎么复现 |
| --- | --- | --- |
| `pc_smoke_seekdb.json` | PowerContext（seekdb 后端）记忆闭环全量请求/响应：建 scope → 写 3 条记忆 → 混合检索（`matched_by:["text","vector"]`）→ PreparedContext（带 citation/字节预算）→ PUT 改写产生 revision 2 → forget 置 inactive 后检索不再命中 → 跨会话召回 → 两个 scope 隔离 | `python3 ../scripts/pc_smoke.py`（需先 `seed_pc.py` 或空库） |
| `pc_smoke_sqlite.json` | 同上，SQLite 后端（对照：证明两种存储后端下行为一致） | `PC_DATABASE_KIND=sqlite ...` |
| `offline_sim.log` | 无需 Dify：直接执行 DSL 中所有 Code 节点，并真实调用 PowerContext，跑完「开场→追问→上酒写记忆→开票→3 位客人→结局 S」；含当票（scope/R1/金额）与最终状态 | `python3 ../scripts/verify_game.py` |
| `dify_import.json`（Dify 起来后补） | DSL 导入结果、应用 ID | `python3 ../scripts/dify_ops.py import ...` |
| `dify_run.json`（Dify 起来后补） | 通过 Dify 服务 API 跑通验证样例的逐轮回答 | `python3 ../scripts/dify_ops.py demo --token <app-token> ...` |
| `dify_run_*.log`（Dify 起来后补） | Dify api/worker 节点执行日志摘要（HTTP 调用、LLM 调用） | `docker logs dify-api` |

> 关键核对点：
> 1. 当票中出现 `记忆库：npc/ayuan` 与 `存本：R1` → PowerContext 的 scope 与 revision 被 Dify 节点真实读取并展示；
> 2. 检索响应 `matched_by` 同时含 `text` 与 `vector` → seekdb 的全文索引与向量索引都在参与；
> 3. 同一客人的 scope 在第二个 Dify 会话中仍能召回上一局写入的「当票」记忆 → 跨会话记忆生效。
