"""Order query service.

Implemented requirements (see ``specs/orders.md``):

* REQ-ORD-001  list pagination
* REQ-ORD-002  caller isolation
* REQ-ORD-003  sorting
* REQ-ORD-004  single order lookup
* REQ-ORD-006  status filtering
* REQ-ORD-007  created-at range filtering
* REQ-ORD-008  minimum amount filtering
* REQ-ORD-009  composable filters
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .errors import ApiError
from .models import DEFAULT_LIMIT, MAX_LIMIT, ORDER_STATUSES, SORT_KEYS, Order
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


def parse_status(status_raw: str | None) -> str | None:
    """REQ-ORD-006: optional ``status`` filter; comparison is case-insensitive."""

    if status_raw is None or status_raw.strip() == "":
        return None
    value = status_raw.strip().lower()
    if value not in ORDER_STATUSES:
        raise ApiError(
            400,
            "INVALID_FILTER",
            f"status must be one of {', '.join(ORDER_STATUSES)}",
            field="status",
        )
    return value


def _parse_datetime(raw: str, field: str) -> datetime:
    """Parse an ISO-8601 date or datetime into an aware UTC datetime."""

    text = raw.strip()
    candidate = text
    if candidate.endswith("Z") or candidate.endswith("z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        value = datetime.fromisoformat(candidate)
    except (TypeError, ValueError):
        raise ApiError(
            400,
            "INVALID_FILTER",
            f"{field} must be an ISO-8601 date or datetime",
            field=field,
        ) from None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_created_bounds(
    after_raw: str | None,
    before_raw: str | None,
) -> tuple[datetime | None, datetime | None]:
    """REQ-ORD-007: inclusive ``created_after`` / ``created_before`` bounds."""

    after: datetime | None = None
    before: datetime | None = None

    if after_raw is not None and after_raw.strip() != "":
        after = _parse_datetime(after_raw, "created_after")
    if before_raw is not None and before_raw.strip() != "":
        before = _parse_datetime(before_raw, "created_before")

    if after is not None and before is not None and after > before:
        raise ApiError(
            400,
            "INVALID_FILTER",
            "created_after must not be later than created_before",
            field="created_after",
        )
    return after, before


def parse_min_amount(min_amount_raw: str | None) -> Decimal | None:
    """REQ-ORD-008: optional non-negative decimal ``min_amount`` (inclusive)."""

    if min_amount_raw is None or min_amount_raw.strip() == "":
        return None
    try:
        value = Decimal(min_amount_raw.strip())
    except (InvalidOperation, ArithmeticError, ValueError, TypeError):
        raise ApiError(
            400,
            "INVALID_FILTER",
            "min_amount must be a non-negative decimal number",
            field="min_amount",
        ) from None
    if not value.is_finite():
        raise ApiError(
            400,
            "INVALID_FILTER",
            "min_amount must be a non-negative decimal number",
            field="min_amount",
        )
    if value < 0:
        raise ApiError(
            400,
            "INVALID_FILTER",
            "min_amount must be greater than or equal to 0",
            field="min_amount",
        )
    return value


def _sort_orders(orders: list[Order], sort: str) -> list[Order]:
    if sort == "created_at_asc":
        return sorted(orders, key=lambda order: order.created_at)
    if sort == "amount_desc":
        return sorted(orders, key=lambda order: order.amount, reverse=True)
    return sorted(orders, key=lambda order: order.created_at, reverse=True)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _filter_orders(
    orders: list[Order],
    *,
    status: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    min_amount: Decimal | None = None,
) -> list[Order]:
    """REQ-ORD-006/007/008/009: apply every supplied filter (AND semantics)."""

    result = orders
    if status is not None:
        result = [order for order in result if order.status.strip().lower() == status]
    if created_after is not None:
        result = [order for order in result if _as_utc(order.created_at) >= created_after]
    if created_before is not None:
        result = [order for order in result if _as_utc(order.created_at) <= created_before]
    if min_amount is not None:
        result = [order for order in result if Decimal(order.amount) >= min_amount]
    return result


def list_orders(
    repo: OrderRepository,
    user_id: str,
    *,
    limit: int,
    offset: int,
    sort: str,
    status: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    min_amount: Decimal | None = None,
) -> dict[str, object]:
    """Return one page of the caller's orders (REQ-ORD-001/002/003/006..009)."""

    orders = _filter_orders(
        repo.list_for_user(user_id),
        status=status,
        created_after=created_after,
        created_before=created_before,
        min_amount=min_amount,
    )
    orders = _sort_orders(orders, sort)
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
