# orders-service（业务工程，供 SDD 上下文模块演示使用）

一个可独立运行、带完整测试的订单查询服务。它有两类测试：

| 目录 | 作用 | 修改前 | 修改后 |
|---|---|---|---|
| `tests/regression/` | 必须始终保持通过的既有行为（REQ-ORD-001～005、CONSTRAINTS） | 通过 | 通过 |
| `tests/target/` | 本次任务要实现的 v2.0 筛选能力（REQ-ORD-006～009） | **失败** | 通过 |

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查 |
| GET | `/orders` | 当前用户订单列表（分页/排序；v2.0 增加筛选） |
| GET | `/orders/{order_id}` | 单条订单（不存在或不属于调用者 → 404） |

请求头 `X-User-Id` 必填（`u-alice` / `u-bob`）。数据为内存种子数据，无外部依赖。

## 本地运行

```bash
cd minimax/demo
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"

pytest tests/regression -q   # 应全部通过
pytest tests/target -q       # 修改前应失败（v2.0 未实现）
pytest -q                    # 修改后应全部通过
```

启动服务（可选）：

```bash
uvicorn app.main:app --port 8000
curl -H 'X-User-Id: u-alice' 'http://127.0.0.1:8000/orders?status=paid'
```

## 规格

行为规格在 `specs/orders.md`（v2.0，生效版本）；`specs/history/orders.v1.md` 是变更前的
初始版本，用于演示"规格更新后重新导入"。任务说明见 `tasks/orders-filters.task.md`。
