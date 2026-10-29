"""Order query service.

Implemented requirements (see ``specs/orders.md``):

* REQ-ORD-001  list pagination
* REQ-ORD-002  caller isolation
* REQ-ORD-003  sorting
* REQ-ORD-004  single order lookup
* REQ-ORD-006  status filtering
* REQ-ORD-007  creation-time range filtering
* REQ-ORD-008  minimum-amount filtering
* REQ-ORD-009  composable filters
"""

from __future__ import annotations

from datetime import date, datetime, timezone
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
    """REQ-ORD-006: validate and normalize an optional status filter."""

    if status_raw is None:
        return None

    status = status_raw.lower()
    if status not in ORDER_STATUSES:
        raise ApiError(
            400,
            "INVALID_FILTER",
            f"status must be one of {', '.join(ORDER_STATUSES)}",
            field="status",
        )
    return status


def _parse_created_bound(raw: str | None, *, field: str) -> datetime | None:
    """Parse an ISO-8601 date or timezone-aware datetime as a UTC datetime."""

    if raw is None:
        return None
    if raw == "":
        raise ApiError(
            400,
            "INVALID_FILTER",
            f"{field} must be a valid ISO-8601 date or timezone-aware datetime",
            field=field,
        )

    try:
        if len(raw) == 10:
            parsed_date = date.fromisoformat(raw)
            return datetime(parsed_date.year, parsed_date.month, parsed_date.day, tzinfo=timezone.utc)

        normalized = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError):
        raise ApiError(
            400,
            "INVALID_FILTER",
            f"{field} must be a valid ISO-8601 date or timezone-aware datetime",
            field=field,
        ) from None

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ApiError(
            400,
            "INVALID_FILTER",
            f"{field} must include a timezone",
            field=field,
        )
    return parsed.astimezone(timezone.utc)


def parse_created_bounds(
    created_after_raw: str | None,
    created_before_raw: str | None,
) -> tuple[datetime | None, datetime | None]:
    """REQ-ORD-007: parse inclusive creation-time bounds."""

    created_after = _parse_created_bound(created_after_raw, field="created_after")
    created_before = _parse_created_bound(created_before_raw, field="created_before")

    if (
        created_after is not None
        and created_before is not None
        and created_after > created_before
    ):
        raise ApiError(
            400,
            "INVALID_FILTER",
            "created_after must not be later than created_before",
            field="created_after",
        )

    return created_after, created_before


def parse_min_amount(min_amount_raw: str | None) -> Decimal | None:
    """REQ-ORD-008: parse an optional non-negative decimal amount."""

    if min_amount_raw is None:
        return None

    try:
        amount = Decimal(min_amount_raw)
    except (InvalidOperation, TypeError, ValueError):
        raise ApiError(
            400,
            "INVALID_FILTER",
            "min_amount must be a non-negative decimal number",
            field="min_amount",
        ) from None

    if not amount.is_finite() or amount < 0:
        raise ApiError(
            400,
            "INVALID_FILTER",
            "min_amount must be a non-negative decimal number",
            field="min_amount",
        )
    return amount


def _filter_orders(
    orders: list[Order],
    *,
    status: str | None,
    created_after: datetime | None,
    created_before: datetime | None,
    min_amount: Decimal | None,
) -> list[Order]:
    filtered = orders

    if status is not None:
        filtered = [order for order in filtered if order.status == status]

    if created_after is not None:
        filtered = [order for order in filtered if order.created_at >= created_after]

    if created_before is not None:
        filtered = [order for order in filtered if order.created_at <= created_before]

    if min_amount is not None:
        filtered = [order for order in filtered if order.amount >= min_amount]

    return filtered


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
    status: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    min_amount: Decimal | None = None,
) -> dict[str, object]:
    """Return one filtered page of the caller's orders."""

    orders = repo.list_for_user(user_id)
    orders = _filter_orders(
        orders,
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
