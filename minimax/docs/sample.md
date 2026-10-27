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

| 符号 | 文件 |
|---|---|
| `list_orders`、`_sort_orders`、`parse_pagination`、`parse_sort` | `demo/app/service.py` |
| `list_orders_route` | `demo/app/routes.py` |
| `ApiError`（错误信封） | `demo/app/errors.py` |
| `Order`、`DEFAULT_LIMIT` 等模型常量 | `demo/app/models.py` |

**测试**

| 类型 | 文件 | 用例 |
|---|---|---|
| 目标（修改前失败） | `demo/tests/target/test_orders_filters_target.py` | `test_filter_by_status`、`test_filter_by_created_range_inclusive`、`test_filter_by_min_amount_inclusive`、`test_filters_compose`、`test_invalid_filter_values`、`test_invalid_created_range` |
| 回归（始终通过） | `demo/tests/regression/test_orders_regression.py` | 18 个用例，覆盖 REQ-ORD-001～005 与 C1/C2/C4/C5 |

## 4. 初始数据

- 业务工程数据：内存种子数据 12 条订单（`u-alice` 7 条、`u-bob` 5 条），时间集中在 2026-09。
- 修改前代码：`patches/0001-orders-filters.patch` 的**反向**即为修改前快照；`evidence/` 中保留
  before 测试日志与上下文包。
- seekdb：空库开始，由 `python -m sddctx ingest` 全量导入（幂等，可反复执行）。
- PowerContext：空状态开始（`.data/powercontext/pc.db` 不存在），第一次运行时创建。

## 5. 操作步骤

```bash
cd minimax
bash scripts/setup.sh                 # 安装依赖 + 建表

# 1) 导入当前生效规格（v2.0）与代码/测试
python -m sddctx ingest

# 2) 生成开发上下文（核对规格/代码/测试来源）
python -m sddctx context --print-text

# 3) 重放 MiniMax 生成的补丁（或 --live 现场生成）
python -m sddctx patch generate
python -m sddctx patch apply

# 4) 跑 before/after 测试矩阵
python -m sddctx verify

# 5) 写入并读回任务状态（新进程）
python -m sddctx state show

# 或一条命令完成 1-5
python -m sddctx run
```

规格更新演示：

```bash
bash scripts/spec-update.sh
```

## 6. 预期结果

| 检查点 | 预期 |
|---|---|
| `ingest` | 入库 chunk 数 > 40；spec/code/test 三类均非空 |
| `context` | `specs` 含 REQ-ORD-006～009（citation 指向 `demo/specs/orders.md` 对应行）；`code` 含 `list_orders`、`list_orders_route`、`parse_pagination`；`tests` 同时包含 target 与 regression |
| `verify` before | `target` 退出码 ≠ 0 且 **6 failed**（功能失败，不是环境失败）；`regression` 18 passed |
| `verify` after | `target` 6 passed；`regression` 18 passed |
| `verify` 结论 | `evidence/verification.json` 中 6 项检查全部 `ok: true` |
| `state show`（新进程） | 能读到上文写入的 done/todo/verification/next_action 记录 |
| `spec-update.sh` | 上下文 A（v1.0）缺 REQ-ORD-006～009；上下文 B（v2.0）包含它们，`context_sha256` 不同 |

## 7. 重置方法

```bash
bash scripts/reset.sh     # 业务工程回到修改前状态 + 清空 .data
bash scripts/setup.sh     # 重新初始化 seekdb 表
```

如果只想清空状态：`rm -rf .data`（seekdb 中的资料可用 `python -m sddctx ingest` 重新导入）。
