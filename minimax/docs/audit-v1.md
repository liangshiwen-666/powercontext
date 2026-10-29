# V1 可行性审计报告（`minimax/` 全部文件）

> 版本：V1（sddctx 1.0.0）｜审计与修复日期：2026-10-29
> 目标：对 `minimax/` 下**全部交付文件**做可行性审计（能否按文档在干净环境跑通、证据是否自洽、技术结合是否真实），
> 并据此产出 V1：修复缺陷、补测、对齐文档。
> 本报告是提交物的一部分，结论均可用文末命令复跑。

## 0. 结论摘要

| 维度 | 结论 |
|---|---|
| 交付完整性 | `minimax/` 共 **98 个受控文件**（45 个 Python 文件 / 10,343 行；文档 13 个 / 1,142 行），覆盖题面六类提交材料 |
| 可运行性 | 干净副本上 `setup.sh → verify-all.sh` 全流程 PASS（无需 API Key）；`demo.sh` 重放演示 PASS |
| 版本可行性 | Python **3.12 / 3.13 / 3.14** 实测均可安装并跑通（**3.11 不可行**：lock 内 `numpy 2.5.4` 要求 ≥3.12） |
| 技术真实性 | MiniMax 真实调用（1 次成功生成）、seekdb 真实建表/检索、PowerContext 真实写入 + 新进程读回，证据链可重算 |
| 修复缺陷 | 审计发现并修复 **15 项真实缺陷/风险**（含 3 项 P0：干净环境复现阻断 2 项、macOS 补丁工具依赖 1 项） |
| 残余风险 | macOS 未实机验证、>6 进程并发未压测、嵌入式 seekdb 未验证跨进程；详见 §7 |

审计由 5 条并行工作流完成：Lead（CLI/context/scripts/集成）+ 4 个专项审计（seekdb 资料层、补丁/验证层、
PowerContext 状态层、文档/证据/安装矩阵）。专项审计的原始报告（含逐项命令输出）作为开发记录保留在 `_research/`，
**不在提交物内**；本报告是其结论与最终验证的汇总。V1 的"改前→改后"差异见仓库的 V1 提交与 [CHANGELOG](../CHANGELOG.md)。

## 1. 文件清单与可行性结论（按目录）

> 可行性分级：**实测**＝本机真实运行验证；**静态**＝代码/结构审查通过；**生成物**＝运行时产物或证据，由命令再生。

### 1.1 根目录（7 个）

| 文件 | 作用 | 可行性 | 审计要点 |
|---|---|---|---|
| `README.md` | 总览、快速开始、六项验收对照、兼容性、来源 | 实测 | 已按 V1 对齐（版本/脚本/兼容性/措辞） |
| `CHANGELOG.md` | V1 变更记录 | 静态 | V1 新增 |
| `pyproject.toml` | 包定义、依赖、pytest/ruff 配置 | 实测 | `requires-python` 修正为 `>=3.12`；新增 ruff 正确性规则 |
| `requirements.lock` | 71 个固定版本 | 实测 | 3.12/3.13/3.14 三版本安装通过；文件头注明"≥3.12" |
| `.env.example` | 配置占位（无真实凭据） | 静态 | 与 `config.py` 环境变量一一对应 |
| `.gitignore` | 排除 `.data/`、caches、egg-info | 静态 | V1 补充 `*.egg-info/`、`build/`、`dist/`、`.ruff_cache/` |
| `.gitkeep` | 上游占位（保留） | 生成物 | 无影响 |

### 1.2 `sddctx/`（12 个，模块本体）

| 文件 | 作用 | 可行性 | V1 处理 |
|---|---|---|---|
| `cli.py` | 12 个子命令、证据汇总、live 重试与反馈 | 实测 | 补错误处理（seekdb 不可用/补丁缺失/空上下文）、`--json` 输出纯 JSON、`--version`、证据路径相对化、`db evidence` 失败非零退出、`state export` |
| `config.py` | 路径与连接配置（env 覆盖） | 实测 | 未改（审计通过） |
| `store.py` | seekdb 资料层（server + 可选 embedded） | 实测 | 修复 6 项：citation 行号强校验、Windows 路径归一、库名白名单、path 长度前置校验、批量写事务、embedded 测试真跑 |
| `ingest.py` | 规格/代码/测试切块 + 入库 + JSONL 快照 | 实测 | 归档文件名改微秒（防同秒覆盖）；specs 非递归（排除 history） |
| `context.py` | 上下文装配（需求号匹配 + 符号定位 + 套件枚举 + 状态） | 实测 | 输出确定性排序、状态 db 路径相对化、空结果 warning、去掉死代码 |
| `pcstore.py` | PowerContext 状态（写入/检索/跨进程/handoff） | 实测 | P0 并发 bootstrap 重试（10 个瞬态标记 + 退避 + busy_timeout）；异常统一包装；长文本 NFC 无损切分；fork 安全 loop |
| `minimax_client.py` | MiniMax HTTP 客户端 + 开发记录 + 哈希链 | 实测 | index 路径相对化；`base_resp "0"` 误判；`api_key_sha256` 脱敏审计；旧 diff 提示词标 Deprecated |
| `patch.py` | 补丁应用/回退 | 实测 | **改为纯 Python unified-diff 引擎**（零外部工具；支持多文件/CRLF/新建/删除/No-newline；上下文不匹配给出文件+hunk+行号） |
| `verify.py` | 测试执行与 before/after 判定 | 实测 | JUnit `classname::name` 集合严格比较；解释器路径绝对化（修 P0）；记录 `testcase_ids`/`code_version` |
| `protocols.py` | 数据契约 TypedDict | 静态 | 未改 |
| `__init__.py` | 版本号 | 静态 | `0.1.0 → 1.0.0` |
| `__main__.py` | `python -m sddctx` 入口 | 实测 | 未改 |

### 1.3 `tests/`（7 个，模块自身测试）

| 文件 | 覆盖 | 结果 |
|---|---|---|
| `test_store.py` | seekdb server/embedded、中文 ik、citation 行号、边界、错误文案 | 32 passed（含 embedded，需 pylibseekdb） |
| `test_ingest.py` | 切块规则、幂等、history 排除、embedded ingest | 通过 |
| `test_pcstore.py` | 跨进程读写、并发 bootstrap、长文本、3.13 | 30 passed |
| `test_patch.py` | 29 条：apply/reverse 逐字节、CRLF、错误信息、真实补丁 e2e | 通过 |
| `test_verify.py` | JUnit 集合一致性、make_before_tree 纯 Python | 12 passed |
| `test_minimax_client.py` | 记录落盘、脱敏、重试分类、sha 链 | 19 passed |
| `conftest.py` | 每进程独立测试库 + 会话末清理 | V1 新增（修复并发测试互相干扰） |

全量：**141 passed / 2 skipped**（3.12 / 3.13 / 3.14 三版本一致；2 skipped 为无 pylibseekdb 时的 embedded 用例）。

### 1.4 `demo/`（15 个，业务工程）

| 文件 | 作用 | 可行性 |
|---|---|---|
| `app/{main,routes,service,models,repo,errors}.py` | FastAPI 订单服务（内存仓储） | 实测：`uvicorn` 真实起服务，200/400/401 行为正确；`pytest` 24 passed |
| `specs/orders.md`（v2.0 生效） | REQ-ORD-001～009 + CONSTRAINTS C1–C5 | 静态 + 上下文中 9 条规格 citation 全部可解析 |
| `specs/history/orders.v1.md` | 初始规格（用于"规格更新重导入"） | 实测：A/B 上下文差异正确 |
| `tasks/orders-filters.task.md` | 任务书（要改什么/必须保持什么） | 静态 |
| `tests/target/*`（6 条） | 目标功能测试（修改前失败、修改后通过） | 实测：before 6 failed（断言失败，非环境错误）→ after 6 passed |
| `tests/regression/*`（18 条） | 回归测试（前后均通过） | 实测：前后均 18 passed |
| `pyproject.toml`、`README.md` | 业务工程依赖与说明 | 实测 |

### 1.5 `scripts/`（10 个）

| 脚本 | 作用 | 可行性 / V1 处理 |
|---|---|---|
| `setup.sh` | 安装 + 预检 + 建表 | 实测；V1 修 P0：Python ≥3.12 校验、venv/ensurepip 失败时 `uv venv --seed` 回退、无 uv 时给出可操作提示 |
| `verify-all.sh` | 一条命令全量验证（含证据自检） | 实测（干净副本 + 团队仓库 clone 均 PASS）；V1 新增 |
| `check-evidence.py` | 证据链只读自检（91 项，退出码=失败数） | 实测：91 passed / 0 failed；V1 新增 |
| `demo.sh` | 端到端重放演示 | 实测；V1 修 P0：解释器路径绝对化；横幅编号统一 |
| `spec-update.sh` | 规格更新重导入演示 | 实测；ingest JSON 落 `evidence/ingest/`（合法 JSON） |
| `state-readback.sh` | 新进程读回状态 | 实测（10 条记录，prepared ready） |
| `check-env.sh` | 环境/依赖/seekdb 连通性检查 | 实测 |
| `live-generate.sh` | 真实调用 MiniMax 并验证 | 实测（本次 V1 证据即由它生成） |
| `lint.sh` | ruff 质量门禁 | 实测（`sddctx/` + tests 通过） |
| `reset.sh` | 回到修改前/清空运行时数据 | 实测 |
| `diff_context.py` | 对比两份上下文 | 实测 |

### 1.6 `docs/`（4 个）

| 文件 | 内容 | 可行性 |
|---|---|---|
| `runbook.md` | 环境要求、seekdb 部署、安装、CLI 入口、测试、live 模式、版本对应、规格更新、重置、已知限制 | 实测：干净副本按此执行通过（V1 修正 Python 版本、venv 前置、§6.1 版本对应语义、before 数字前提） |
| `sample.md` | 任务描述、有效规格、初始数据、操作步骤、预期结果、重置 | 实测（V1 补充 reset 前置、明确 before/after 数字、状态 10 条记录预期） |
| `tech-note.md` | 一页技术说明（结构/选择方式/技术分工/取舍/边界） | 静态（V1 对齐：完整文件输出、纯 Python patch、测试隔离、embedded 可选） |
| `audit-v1.md` | 本报告 | — |

### 1.7 `patches/`、`records/`、`evidence/`（生成物，可重算）

| 组 | 文件 | 审计结论 |
|---|---|---|
| `patches/` | `0001-orders-filters.patch`（7,223 B）+ `meta.json` | 只改 `app/` 两个文件；sha256 与 meta/records/run.json 三处一致；apply/reverse 状态机正确 |
| `records/minimax/` | 1 次成功调用记录 + `index.jsonl` | 完整请求/响应、usage、trace_id、sha256 链；Authorization 已脱敏，无明文 key；index 路径相对 |
| `records/db/` | `seekdb-queries-*.json` | `SHOW CREATE TABLE` 含 `FULLTEXT … WITH PARSER ik`；计数 9/15/24（before）；按类型检索 + `UNION ALL` 三类联合检索均返回真实行 |
| `records/powercontext/` | `state-final-*.json` + `state-readback.json` | 写入进程与新进程读回均为 10 条，`prepared.status=ready` |
| `evidence/` | context ×3、logs ×15、run/verification、snapshots（before/after 各 7 文件） | before+patch==after 逐字节；citation 43/43 可解析；6 项检查全 PASS；JUnit 集合前后一致 |

## 2. 修复清单（按严重度）

| # | 级别 | 问题（审计发现） | 修复 | 验证 |
|---|---|---|---|---|
| 1 | **P0** | `demo.sh` 导出**相对** `.venv/bin/python` 给子进程；`verify` 在临时 before 树 cwd 下执行 → 干净 clone `FileNotFoundError` | 脚本内解释器绝对化 + `verify.run_pytest` 用 `abspath`（不 resolve 符号链接，保留 venv 环境） | 干净副本 `verify-all.sh` PASS；单测覆盖 |
| 2 | **P0** | 文档/`pyproject` 声称 Python 3.11，但 lock 内 `numpy 2.5.4` 要求 ≥3.12 → 3.11 无解 | `requires-python` 改 `>=3.12,<4.0`；README/runbook 改 3.12–3.14；lock 文件头注明 | 3.12.16/3.13.18/3.14.4 三环境全量测试 141 passed |
| 3 | **P0** | `setup.sh` 在缺 `python3-venv` 的宿主（本机 3.14）建 venv 失败且无有效提示 | 版本/venv 前置检查；失败时自动 `uv venv --seed --python <当前解释器>`；无 uv 时输出 apt/uv 安装提示 | 两条路径实测：有 uv → exit 0；无 uv → exit 2 + 可操作提示 |
| 4 | **P0** | 补丁层依赖 GNU `patch` 专有长选项（macOS BSD patch 不兼容；无 patch 环境直接崩） | 重写为**纯 Python unified-diff 引擎**（不 import subprocess/shutil） | 29 条 patch 单测；真实补丁 e2e；`PATH=/nonexistent` 下仍成功 |
| 5 | **P1** | PowerContext 两进程同时初始化全新 DB → 输家崩（`plan_changed` 等）并丢写 | 瞬态错误识别 + 1+4 次退避重试 + `busy_timeout=15s` | 全新库 2 进程 2/2、6 进程 0 失败；3.12/3.13 压测全过 |
| 6 | **P1** | `verify` 的"同一组测试"只比命令前缀+数量，改名/删测可漏过 | 解析 JUnit `classname::name` 集合，target/regression 分别严格比较 | 12 条单测（含"计数相同但名称不同 → FAIL"）+ 本次证据 detail "6/18 identical testcases" |
| 7 | **P1** | 入库 chunk 的 `line_start..line_end` 与 body 行数不一致可产生**假 citation** | `_normalise_chunk` 强校验行数，不匹配即 `ValueError` | 新增测试；证据 43/43 citation 实际解析通过 |
| 8 | **P1** | Windows 反斜杠 path 原样入库 → citation 非 POSIX | `_normalise_path()` 统一 `/`、禁控制字符 | 单测通过 |
| 9 | **P1** | seekdb 库名（来自 env）直接拼入标识符（注入面 + 误导错误） | 白名单 `[A-Za-z_][A-Za-z0-9_]{0,63}`，连接前报错 | 单测通过 |
| 10 | **P1** | 批量 upsert 非事务，半批失败留脏数据 | `BEGIN/COMMIT/ROLLBACK`（两者路线均支持） | 实测"第 2 行失败 → 0 行落库" |
| 11 | **P1** | `ingest` 归档文件名秒级 → 同秒两次覆盖 | 微秒时间戳 | 单测断言两次归档路径不同且都在 |
| 12 | **P1** | `records/minimax/index.jsonl` 记录绝对路径（不可移植）；`ingest --json` 混入人类摘要导致 `ingest-v*.json` 非法 JSON；DB 证据 schema 拼写 `sendb` | 路径相对化；`--json` 输出纯 JSON；schema 改 `seekdb-evidence/v1`；`ingest --json` 单文档 | 证据重生成后 JSON 校验通过 |
| 13 | **P1** | `context` 的 `state.db_path` 用绝对路径 → `context_sha256` 无法跨机复算 | 改为相对 `minimax/` 根 | 同输入两次运行 sha 一致；干净副本与仓库一致 |
| 14 | **P1** | 两个 pytest 进程共用 `sddctx_test` 库互相干扰（曾导致偶发失败） | 每进程独立库 `sddctx_test_<pid>` + 会话末 DROP | 并发 2 进程各 51 passed；无残留库 |
| 15 | **P2** | 文档数字/措辞不一致（banner 编号、脚本清单、docker tag、`.gitkeep` 断言、before 数字前提、§6.1 版本对应语义、tech-note 图注、`sendb` 笔误）；DB 证据的"三类联合检索"分支共用同一组关键词 → 实际只命中 spec | 逐项修正文档；**每个 UNION 分支改用各自命中的关键词**（spec 用中文需求词、code 用符号名、test 用测试函数名），现在一条语句稳定返回 spec/code/test 三类；`records/README` 说明绝对路径属环境信息；新增 `scripts/check-evidence.py` 自动核对证据链 | 自检 91 项 0 失败；`retrieval_all_kinds` 实测返回 3 种 kind |

## 3. 验证矩阵（V1 最终状态）

| 验证项 | 命令 | 结果 |
|---|---|---|
| 模块单测（3.12/3.13/3.14） | `python -m pytest tests -q` | **141 passed / 2 skipped**（三版本一致） |
| embedded seekdb 路线 | `/tmp/v1-store/bin/python -m pytest tests/test_store.py -q`（含 pylibseekdb） | **32 passed**（无 server 参与） |
| 业务工程 | `cd demo && python -m pytest -q` | **24 passed**（target 6 + regression 18） |
| 修改前/后矩阵 | `python -m sddctx verify`（或 `run`） | before target 6 failed / regression 18 passed；after 6+18 passed；6 项检查全 PASS |
| 干净副本全流程 | `setup.sh` → `verify-all.sh`（`/tmp/v1-review`） | **exit 0，VERIFY-ALL: PASS**（无 API Key） |
| 团队仓库全新 clone | sparse clone `powercontext-fork@提交commit` → `setup.sh`（3.14 宿主走 uv 回退）→ `verify-all.sh` | **exit 0，VERIFY-ALL: PASS**（head/树哈希与本地一致） |
| 证据链自检 | `python scripts/check-evidence.py` | **91 passed / 0 failed** |
| setup.sh 降级路径 | `PYTHON=python3`（3.14 无 ensurepip） | 有 uv：exit 0（自动回退）；无 uv：exit 2 + 安装提示 |
| 测试隔离 | 两个并发 `pytest tests/test_store.py tests/test_ingest.py` | 各 51 passed，互不影响 |
| CLI 错误路径 | seekdb 不可达 / 补丁缺失 / 空上下文 / 未知命令 | 无 traceback，非零退出 + 可操作提示 |
| 上下文确定性 | `context` 连跑两次 | 同 sha（`6a686d74…`）；`state.db_path` 为相对路径 |
| citation 真实性 | 解析 `evidence/run.json` 引用包的 43 条 citation | 43/43 在对应树内可解析 |
| JSON 合法性 | 遍历全部 `evidence/`、`records/` JSON | 全部可 `json.load` |
| 证据哈希链 | patch/meta/records/run/verification 交叉重算 | patch sha 四处一致（`ebdbe1c2…`）；响应 sha、上下文 sha 可重算（由 `scripts/check-evidence.py` 自动校验） |
| 快照一致性 | `snapshots/before` + patch → `snapshots/after` | 逐字节一致；`after` == `demo/app` |

## 4. 证据链核对方法（评审可复跑）

1. **MiniMax 参与**：`records/minimax/*.json` 的 `response` 里是模型返回的完整文件内容；
   `patches/0001-orders-filters.meta.json` 记录 `context_sha256`、`response_sha256`、`patch_sha256`；
   `patches/0001-orders-filters.patch` 的 sha256 与之相等。`evidence/snapshots/before + patch == after`。
2. **上下文正确**：`evidence/context/orders-filters.task-*.json` 的 `specs/code/tests` 每条带
   `citation = 路径:起-止行`；按文件重读行号可复核（本次 43/43 通过）。`spec-v1/spec-v2` 两份上下文
   证明规格更新后输出变化（`context_sha256` 不同）。
3. **数据库参与**：`records/db/*.json` 保存真实 `SHOW CREATE TABLE`（含 `WITH PARSER ik`）、入库计数、
   按类型与三类联合检索的实际返回行；这些行与上下文的 citations 一一对应。
4. **状态复用**：`records/powercontext/state-final-*.json` 是写入进程的记录，
   `state-readback.json` 是新进程（不同 pid）读回，两者 10 条 `(kind,text)` 完全一致，`prepared.status=ready`。
5. **代码修改有效**：`evidence/verification.json` 的 6 项检查 + `evidence/logs/*.json`（命令/退出码/计数/
   `code_version`）+ JUnit testcase 集合（before/after 各 6/18 完全相同）。
6. **测试日志版本**：每条日志的 `code_version{base_git_head, patch_sha256, tree_state}` 与提交树、补丁对应。

## 5. 兼容性矩阵

| Python | lock 安装 | `sddctx init` | toolkit 测试 | 结论 |
|---|---|---|---|---|
| 3.11 | ❌（numpy 2.5.4 要求 ≥3.12） | — | — | **不支持** |
| 3.12.16 | ✅ | ✅ | 141 passed / 2 skipped | **推荐基准** |
| 3.13.18 | ✅ | ✅ | 141 passed / 2 skipped | 支持 |
| 3.14.4 | ✅ | ✅ | 141 passed / 2 skipped | 支持 |

系统：Linux（WSL2 Ubuntu 26.04 实测）/ macOS（补丁层已无外部工具依赖，但**未实机验证**）；
Windows 仅支持 WSL2（PowerContext 限制）。seekdb：1.4.0 server（apt/docker）为主，嵌入式 `pylibseekdb` 为可选（实测可通过）。

## 6. 与题面六项验收的对应

| 验收项 | V1 状态 | 主要证据 |
|---|---|---|
| 能够运行 | ✅ 干净副本 `setup.sh → verify-all.sh` 通过 | §3 矩阵、`docs/runbook.md` |
| MiniMax 实际参与 | ✅ 真实调用 1 次成功（模型 M3.1-Flash），记录与 diff 对应 | `records/minimax/`、`patches/*.meta.json` |
| 上下文正确 | ✅ 9 规格 / 10 代码 / 24 测试，citation 全可解析；规格更新后变化 | `evidence/context/` |
| 数据库实际参与 | ✅ 真实 DDL + 检索，结果进入上下文 | `records/db/` |
| 状态能够复用 | ✅ 空状态写入 → 新进程读回 10 条 | `records/powercontext/` |
| 代码修改有效 | ✅ 同一组测试 before 6 failed → after 6 passed，回归 18 恒绿 | `evidence/verification.json`、`evidence/logs/` |

## 7. 残余风险与未验证项

1. **macOS 未实机验证**：补丁引擎已零外部依赖（`PATH=/nonexistent` 下通过），但未在 macOS 上跑完整流程。
2. **高并发**：PowerContext 已验证 2/6 进程与 3 线程；>6 进程并发写未压测（SQLite 单写者，极端可能 `database is locked`）。
3. **嵌入式 seekdb**：功能与持久化已验证，未验证跨进程同目录并发使用（文档已标注"单进程使用"）。
4. **补丁引擎边界**：不支持 fuzz/rename/binary/mode patch（本项目生成的补丁只含修改/新建文本文件）。
5. **检索能力**：仅全文检索（ik），未接 embedding 向量召回（题面未要求）。
6. **live 模式的网络/模型行为**：重试与反馈逻辑已实测；模型输出质量本身不保证，失败时会明确报错而不是静默成功。

## 8. 复现命令

```bash
# 1. 干净环境（Linux/WSL2，Python 3.12–3.14，seekdb 1.4.0 已启动）
cd minimax
bash scripts/setup.sh            # 安装 + 预检 + 建表
bash scripts/verify-all.sh       # 环境 + 单测 + 业务测试 + 重放演示 + 规格更新 + 证据汇总

# 2. 分步
bash scripts/demo.sh             # 重放 MiniMax 记录的修改并验证（无需 API Key）
bash scripts/spec-update.sh      # 规格更新 → 上下文 A/B 变化
bash scripts/state-readback.sh   # 新进程读回任务状态
python -m pytest tests -q        # 模块单测
python scripts/check-evidence.py # 证据链自检（91 项，只读）
cd demo && python -m pytest -q   # 业务工程测试

# 3. 现场重新生成（需要 Key）
export MINIMAX_API_KEY=sk-...
bash scripts/live-generate.sh
```
