# 验证样例：为订单查询接口增加筛选能力

本样例是一个可以从干净环境完整复现的 SDD 任务：规格先行、上下文驱动、测试验收。

## 1. 任务描述

> 为 `GET /orders` 增加 `status`、`created_after`、`created_before`、`min_amount` 四个可选筛选参数，
> 按生效规格 `specs/orders.md` v2.0 的 REQ-ORD-006～009 实现组合语义；非法参数返回 400
> `INVALID_FILTER`（沿用 REQ-ORD-005 错误信封）。只修改 `demo/app/` 下的实现代码，不得修改测试。

任务文件：`demo/tasks/orders-filters.task.md`

### 要改什么（功能）

1. `status` 筛选（REQ-ORD-006）：只返回指定状态的订单。
2. 创建时间范围筛选（REQ-ORD-007）：`created_after`/`created_before` 两端包含；日期字符串按 UTC 当天 00:00:00 解释。
3. 金额下限筛选（REQ-ORD-008）：`amount >= min_amount`，包含边界。
4. 组合筛选（REQ-ORD-009）：多条件同时生效，`total` 反映过滤后数量。
5. 非法参数统一 400 `INVALID_FILTER`（含 `field`），不再静默忽略。

### 哪些原有行为必须保持（回归）

| 约束 | 内容 |
|---|---|
| REQ-ORD-001 | `limit` 默认 20、范围 1–100；`offset >= 0`；响应含 `items/total/limit/offset` |
| REQ-ORD-002 | `X-User-Id` 必填；只能看到自己的订单，不得泄露他人订单 |
| REQ-ORD-003 | `sort` 取值 `created_at_desc`(默认)/`created_at_asc`/`amount_desc`；非法 → `INVALID_SORT` |
| REQ-ORD-004 | `GET /orders/{id}` 不存在或不属于调用者 → 404 `ORDER_NOT_FOUND` |
| REQ-ORD-005 | 所有 4xx 使用 `{"error":{"code","message","field"}}` 信封 |
| C1 | 不传 v2.0 新参数时，输出与 v1.0 逐字段一致 |
| C2 | 新参数不能改变调用者隔离作用域 |
| C4 | `total` 始终是过滤后、分页前的数量 |
| C5 | 未知查询参数被忽略（向前兼容） |

## 2. 有效规格

**生效版本**：`demo/specs/orders.md`（v2.0）——包含 REQ-ORD-001～009 与 CONSTRAINTS C1–C5。

**初始数据**：`demo/specs/history/orders.v1.md`（v1.0，只有 REQ-ORD-001～005）用于
"规格更新后重新导入"的对照。

上下文中**必须包含**的规格来源（评审核对点）：

| 规格条目 | 文件与行号（v2.0） |
|---|---|
| REQ-ORD-006 状态筛选 | `demo/specs/orders.md:44-49` |
| REQ-ORD-007 时间范围筛选 | `demo/specs/orders.md:51-58` |
| REQ-ORD-008 金额下限筛选 | `demo/specs/orders.md:60-65` |
| REQ-ORD-009 筛选组合 | `demo/specs/orders.md:67-71` |
| REQ-ORD-001～005、C1–C5 | `demo/specs/orders.md` 对应小节 |

## 3. 上下文中必须包含的代码与测试来源

**代码（需要修改的文件/符号）**

| 符号 | 文件 | 对应需求 |
|---|---|---|
| `list_orders`、`_sort_orders` | `demo/app/service.py` | REQ-ORD-001/003/006–009 |
| `parse_status`、`parse_created_bounds`、`parse_min_amount` | `demo/app/service.py` | REQ-ORD-006/007/008 |
| `parse_pagination`、`parse_sort`（必须保持的既有逻辑） | `demo/app/service.py` | REQ-ORD-001/003 |
| `list_orders_route` | `demo/app/routes.py` | 接收并校验筛选参数 |
| `ApiError`（错误信封） | `demo/app/errors.py` | REQ-ORD-005 |
| `Order`、`DEFAULT_LIMIT` 等模型常量 | `demo/app/models.py` | 数据模型与分页常量 |

> 实际选中的 10 个代码块由 seekdb 检索决定（见 `evidence/context/*.json` 的 `code[]`）；
> 评审只需核对"规格提到的符号都出现在上下文里、citation 能打开且行号正确"。

**测试**

| 类型 | 文件 | 用例 |
|---|---|---|
| 目标（修改前失败） | `demo/tests/target/test_orders_filters_target.py` | `test_filter_by_status`、`test_filter_by_created_range_inclusive`、`test_filter_by_min_amount_inclusive`、`test_filters_compose`、`test_invalid_filter_values`、`test_invalid_created_range` |
| 回归（始终通过） | `demo/tests/regression/test_orders_regression.py` | 18 个用例，覆盖 REQ-ORD-001～005 与 C1/C2/C4/C5 |

## 4. 初始数据

- 业务工程数据：内存种子数据 12 条订单（`u-alice` 7 条、`u-bob` 5 条），时间集中在 2026-09。
- 修改前后代码快照：`evidence/snapshots/before/app/` 与 `evidence/snapshots/after/app/`（含 sha256 与生成方式）；
  代码差异为 `patches/0001-orders-filters.patch`；`evidence/` 中另有 before/after 测试日志与上下文包。
- seekdb：空库开始，由 `python -m sddctx ingest` 全量导入（幂等，可反复执行）。
- PowerContext：空状态开始（`.data/powercontext/pc.db` 不存在），第一次运行时创建。

## 5. 操作步骤

> 提交树中的业务工程是**修改后**状态。手动重放补丁前要先回到修改前：
> `python -m sddctx reset --demo --data`；推荐直接 `bash scripts/demo.sh`（内部已做 reset + run）。

```bash
cd minimax
bash scripts/setup.sh                 # 安装依赖 + 建表（含 Python/seekdb 预检）

# 0) 回到修改前 + 清空任务状态（手动重放时必需；demo.sh 会自动做）
python -m sddctx reset --demo --data

# 1) 导入当前生效规格（v2.0）与代码/测试
python -m sddctx ingest

# 2) 生成开发上下文（核对规格/代码/测试来源；默认输出到 .data/context/）
python -m sddctx context --print-text

# 3) 重放 MiniMax 生成的补丁（或 --live 现场生成）
python -m sddctx patch generate
python -m sddctx patch apply          # 纯 Python 引擎，tool="python"

# 4) 跑 before/after 测试矩阵（含 JUnit testcase 集合一致性校验）
python -m sddctx verify

# 5) 写入/读回任务状态（新进程）
python -m sddctx state show
python -m sddctx state export --out records/powercontext/state-readback.json

# 或一条命令完成 0-5
python -m sddctx run --fresh

# 全量验证（推荐）
bash scripts/verify-all.sh
```

规格更新演示：

```bash
bash scripts/spec-update.sh
```

## 6. 预期结果

| 检查点 | 预期 |
|---|---|
| `ingest` | **修改前**（reset 后）：48 chunks（spec 9 / code 15 / test 24）；**修改后**（提交树）：53 chunks（9 / 20 / 24）。差异来自补丁新增的 5 个函数切块 |
| `context` | `specs` 含 REQ-ORD-006～009（citation 指向 `demo/specs/orders.md` 对应行）；`code` 含 `list_orders`、`list_orders_route`、`parse_status`、`parse_created_bounds`、`parse_min_amount`、`parse_pagination`；`tests` 同时包含 target 6 条与 regression 18 条 |
| `verify` before | `target` 退出码 ≠ 0 且 **6 failed**（功能失败，不是环境失败）；`regression` 18 passed |
| `verify` after | `target` 6 passed；`regression` 18 passed |
| `verify` 结论 | `evidence/verification.json` 中 6 项检查全部 `ok: true`，且 before/after 的 JUnit testcase 集合完全一致（detail 里显示 "6 identical testcases"） |
| `state show` / `state export`（新进程） | 执行 `run` 后应读到 **10 条**记录（decision/done/todo/todo_done/verification/next_step）且 `prepared.status=ready`；`records/powercontext/state-readback.json` 与新进程输出一致 |
| `spec-update.sh` | 上下文 A（v1.0）缺 REQ-ORD-006～009；上下文 B（v2.0）包含它们，`context_sha256` 不同 |
| 单元测试 | `python -m pytest tests -q` → 141 passed / 2 skipped（V1 实测） |
| 测试隔离 | 两个 pytest 进程同时跑 store/ingest 测试互不影响（每进程独立测试库，V1 实测） |

## 7. 重置方法

```bash
bash scripts/reset.sh     # 业务工程回到修改前状态 + 清空 .data
bash scripts/setup.sh     # 重新初始化 seekdb 表
```

如果只想清空状态：`rm -rf .data`（seekdb 中的资料可用 `python -m sddctx ingest` 重新导入）。
