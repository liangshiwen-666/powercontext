# 订单查询服务规格 v1.0（历史版本／初始数据）

> 文档状态：superseded ｜ 版本：1.0 ｜ 最后更新：2026-09-30
> 本文件是筛选能力上线前的规格，仅用于验证"规格更新后重新导入"的场景；
> 当前生效规格见 `specs/orders.md`（v2.0）。

## 1. 范围

本文档定义 `orders-service` 对外的 HTTP 行为。每条需求都必须可以由自动化测试验证；
实现代码位于 `app/`（`app/routes.py` 接收请求，`app/service.py` 校验与查询，
`app/errors.py` 定义错误信封，`app/repo.py` 提供数据，`app/models.py` 定义模型）。

## 2. 需求条目

### REQ-ORD-001 列表分页

`GET /orders` 返回当前调用者订单的分页结果。

- 响应体固定为 `{"items": [...], "total": <int>, "limit": <int>, "offset": <int>}`。
- `limit` 默认 20，取值范围 1–100（含两端）。
- `offset` 默认 0，必须 `>= 0`。
- `total` 是分页前的订单总数。
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

## 3. 必须保持的行为（CONSTRAINTS）

- C1 默认排序为 `created_at_desc`，默认分页为 `limit=20, offset=0`。
- C2 调用者隔离不可退化。
- C3 错误信封结构不可变。
- C5 未知查询参数必须被忽略（向前兼容）。

## 4. 验收映射

| 需求 | 回归测试（`tests/regression/`） |
|---|---|
| REQ-ORD-001 | `test_default_pagination_and_order`、`test_pagination_slice` |
| REQ-ORD-002 | `test_missing_user_header`、`test_caller_isolation` |
| REQ-ORD-003 | `test_sort_created_at_asc`、`test_invalid_sort` |
| REQ-ORD-004 | `test_get_single_order`、`test_get_unknown_order` |
| REQ-ORD-005 | `test_missing_user_header` |
