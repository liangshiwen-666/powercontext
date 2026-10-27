# 订单查询服务规格 v2.0（生效版本）

> 文档状态：active ｜ 版本：2.0 ｜ 最后更新：2026-10-27
> 本版本在 1.0 基础上新增 REQ-ORD-006～009（查询筛选）与验收映射；REQ-ORD-001～005 与
> CONSTRAINTS 不变。历史版本见 `specs/history/orders.v1.md`。

## 1. 范围

本文档定义 `orders-service` 对外的 HTTP 行为。每条需求都必须可以由自动化测试验证；
实现代码位于 `app/`（`app/routes.py` 负责参数接收，`app/service.py` 负责校验与查询，
`app/errors.py` 定义错误信封，`app/repo.py` 提供数据，`app/models.py` 定义模型）。

## 2. 需求条目

### REQ-ORD-001 列表分页

`GET /orders` 返回当前调用者订单的分页结果。

- 响应体固定为 `{"items": [...], "total": <int>, "limit": <int>, "offset": <int>}`。
- `limit` 默认 20，取值范围 1–100（含两端）。
- `offset` 默认 0，必须 `>= 0`。
- `total` 是过滤后、分页前的订单总数。
- 违反上述约束返回 400，`code=INVALID_PAGINATION`，`field` 为 `limit` 或 `offset`。

### REQ-ORD-002 调用者隔离

- 请求必须携带 `X-User-Id` 请求头；缺失或全空白返回 401，`code=UNAUTHENTICATED`。
- 列表与单条查询都只能看到该调用者自己的订单，任何响应不得泄露他人订单的存在或内容。

### REQ-ORD-003 排序

- `sort` 可选值：`created_at_desc`（默认）、`created_at_asc`、`amount_desc`。
- 非法值返回 400，`code=INVALID_SORT`，`field=sort`。

### REQ-ORD-004 单条订单

- `GET /orders/{order_id}` 返回该订单；订单不存在或不属于调用者时返回 404，`code=ORDER_NOT_FOUND`。

### REQ-ORD-005 错误信封

- 所有 4xx 响应体统一为 `{"error": {"code": "...", "message": "...", "field": "..."}}`；
  `field` 只在字段级错误时出现。

### REQ-ORD-006 状态筛选（v2.0 新增）

- `GET /orders` 支持可选查询参数 `status`，取值 `pending`、`paid`、`shipped`、`cancelled`。
- 提供时只返回该状态的订单；省略时不按状态过滤。
- 非法值（大小写不敏感比较也不匹配）返回 400，`code=INVALID_FILTER`，`field=status`。
- 实现要点：在 `list_orders` 的过滤阶段应用，不能影响 `total` 的语义。

### REQ-ORD-007 创建时间范围筛选（v2.0 新增）

- 支持可选参数 `created_after`、`created_before`，接受 ISO-8601 日期（`2026-09-05`）或带时区
  日期时间（`2026-09-05T00:00:00Z`）；只给日期时按当天 00:00:00 UTC 解释。
- 语义：`created_after <= created_at <= created_before`，两端都包含；只给一端时另一端不限制。
- 无法解析返回 400，`code=INVALID_FILTER`，`field` 为对应参数名。
- `created_after` 晚于 `created_before` 返回 400，`code=INVALID_FILTER`，`field=created_after`。
- 实现要点：新增 `parse_created_bounds` 校验函数，与既有 `parse_pagination`/`parse_sort` 风格一致。

### REQ-ORD-008 金额下限筛选（v2.0 新增）

- 支持可选参数 `min_amount`，十进制数，必须 `>= 0`。
- 语义：`amount >= min_amount`（包含边界）。
- 非数字或负数返回 400，`code=INVALID_FILTER`，`field=min_amount`。
- 实现要点：新增 `parse_min_amount` 校验函数，使用 `Decimal` 比较，避免浮点误差。

### REQ-ORD-009 筛选组合（v2.0 新增）

- 任意筛选参数可以组合，结果必须同时满足所有条件。
- `total` 反映组合过滤后的数量，分页与排序在过滤之后应用。
- 未提供任何筛选参数时，输出必须与 v1.0 完全一致。

## 3. 必须保持的行为（CONSTRAINTS）

- C1 未提供 v2.0 新参数时，响应逐字段与 v1.0 一致（默认排序 `created_at_desc`、默认分页 20/0）。
- C2 调用者隔离不可退化：任何新参数都不能改变 `X-User-Id` 的作用域。
- C3 错误信封结构不变；新增的字段级错误只能使用 `INVALID_FILTER`。
- C4 `total` 始终等于过滤后、分页前的数量。
- C5 未知查询参数必须被忽略（向前兼容）。

## 4. 验收映射

| 需求 | 目标测试（`tests/target/`） | 回归测试（`tests/regression/`） |
|---|---|---|
| REQ-ORD-001 | — | `test_default_pagination_and_order`、`test_pagination_slice`、`test_limit_upper_bound`、`test_negative_offset` |
| REQ-ORD-002 | — | `test_missing_user_header`、`test_caller_isolation`、`test_get_foreign_order_is_not_disclosed` |
| REQ-ORD-003 | — | `test_sort_created_at_asc`、`test_sort_amount_desc`、`test_invalid_sort` |
| REQ-ORD-004 | — | `test_get_single_order`、`test_get_unknown_order` |
| REQ-ORD-005 | `test_invalid_filter_values`、`test_invalid_created_range` | `test_missing_user_header` |
| REQ-ORD-006 | `test_filter_by_status`、`test_invalid_filter_values` | — |
| REQ-ORD-007 | `test_filter_by_created_range_inclusive`、`test_invalid_created_range` | — |
| REQ-ORD-008 | `test_filter_by_min_amount_inclusive`、`test_invalid_filter_values` | — |
| REQ-ORD-009 | `test_filters_compose` | `test_unknown_query_parameters_are_ignored` |
