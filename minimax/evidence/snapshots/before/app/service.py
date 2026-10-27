"""Order query service.

Implemented requirements (see ``specs/orders.md``):

* REQ-ORD-001  list pagination
* REQ-ORD-002  caller isolation
* REQ-ORD-003  sorting
* REQ-ORD-004  single order lookup

Filtering (REQ-ORD-006 .. REQ-ORD-009) is scheduled for v2.0 and is not
implemented in this version.
"""

from __future__ import annotations

from .errors import ApiError
from .models import DEFAULT_LIMIT, MAX_LIMIT, SORT_KEYS, Order
from .repo import OrderRepository


def require_user(x_user_id: str | None) -> str:
    """REQ-ORD-002: every request must identify the caller."""

    if x_user_id is None or not x_user_id.strip():
        raise ApiError(401, "UNAUTHENTICATED", "X-User-Id header is required", field="X-User-Id")
    return x_user_id.strip()


def _parse_int(raw: str | None, *, default: int, field: str) -> int:
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ApiError(
            400,
            "INVALID_PAGINATION",
            f"{field} must be an integer",
            field=field,
        ) from None


def parse_pagination(limit_raw: str | None, offset_raw: str | None) -> tuple[int, int]:
    """REQ-ORD-001: limit defaults to 20 and must stay in 1..100; offset >= 0."""

    limit = _parse_int(limit_raw, default=DEFAULT_LIMIT, field="limit")
    offset = _parse_int(offset_raw, default=0, field="offset")
    if limit < 1 or limit > MAX_LIMIT:
        raise ApiError(
            400,
            "INVALID_PAGINATION",
            f"limit must be between 1 and {MAX_LIMIT}",
            field="limit",
        )
    if offset < 0:
        raise ApiError(400, "INVALID_PAGINATION", "offset must be greater than or equal to 0", field="offset")
    return limit, offset


def parse_sort(sort_raw: str | None) -> str:
    """REQ-ORD-003: ``sort`` is one of the documented keys."""

    if sort_raw is None or sort_raw == "":
        return SORT_KEYS[0]
    if sort_raw not in SORT_KEYS:
        raise ApiError(
            400,
            "INVALID_SORT",
            f"sort must be one of {', '.join(SORT_KEYS)}",
            field="sort",
        )
    return sort_raw


def _sort_orders(orders: list[Order], sort: str) -> list[Order]:
    if sort == "created_at_asc":
        return sorted(orders, key=lambda order: order.created_at)
    if sort == "amount_desc":
        return sorted(orders, key=lambda order: order.amount, reverse=True)
    return sorted(orders, key=lambda order: order.created_at, reverse=True)


def list_orders(
    repo: OrderRepository,
    user_id: str,
    *,
    limit: int,
    offset: int,
    sort: str,
) -> dict[str, object]:
    """Return one page of the caller's orders (REQ-ORD-001/002/003)."""

    orders = _sort_orders(repo.list_for_user(user_id), sort)
    total = len(orders)
    page = orders[offset : offset + limit]
    return {
        "items": [order.to_dict() for order in page],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_order(repo: OrderRepository, user_id: str, order_id: str) -> dict[str, str]:
    """REQ-ORD-004: unknown or foreign orders are a plain 404."""

    order = repo.get_for_user(user_id, order_id)
    if order is None:
        raise ApiError(404, "ORDER_NOT_FOUND", f"order {order_id} was not found", field="order_id")
    return order.to_dict()
