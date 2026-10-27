# 任务：为订单查询接口增加筛选能力（orders-filters-v2）

## 任务目标

为 `GET /orders` 增加四个可选筛选参数，并实现组合语义：

- `status`：按订单状态筛选（REQ-ORD-006）
- `created_after` / `created_before`：按下单时间范围筛选，两端包含（REQ-ORD-007）
- `min_amount`：按金额下限筛选，包含边界（REQ-ORD-008）
- 多个筛选参数可组合（REQ-ORD-009）

非法筛选参数必须返回 400，统一使用错误信封且 `code=INVALID_FILTER`（REQ-ORD-005）。

## 依据（生效规格）

`specs/orders.md` v2.0：REQ-ORD-006、REQ-ORD-007、REQ-ORD-008、REQ-ORD-009，
以及验收映射表。

## 必须保持（回归约束）

- REQ-ORD-001 分页默认值与上下限、REQ-ORD-002 调用者隔离、REQ-ORD-003 排序、
  REQ-ORD-004 单条订单语义、REQ-ORD-005 错误信封。
- CONSTRAINTS C1：未提供新参数时输出与 v1.0 逐字段一致。
- CONSTRAINTS C2：调用者隔离不可退化。
- CONSTRAINTS C4：`total` 是过滤后、分页前的数量。
- CONSTRAINTS C5：未知查询参数必须被忽略。

## 修改范围

只修改 `app/` 下的实现代码（`routes.py`、`service.py` 等）。
不要修改 `tests/`（目标测试与回归测试由测试方提供，修改测试视为无效实现）。

## 验收

1. `pytest tests/target -q` 全部通过（修改前应全部失败）。
2. `pytest tests/regression -q` 全部通过（修改前后都必须通过）。
3. 目标测试与回归测试使用同一份测试文件，不允许删除断言或修改预期。
