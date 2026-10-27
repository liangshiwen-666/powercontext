"""In-memory order repository.

The service is deliberately storage-agnostic: the demo runs without any external
database so that the target change can be verified from a clean checkout.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from .models import Order


def _utc(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


SEED_ORDERS: tuple[Order, ...] = (
    # user alice: 7 orders
    Order("o-1001", "u-alice", "pending", Decimal("120.00"), "CNY", _utc(2026, 9, 1, 10, 0)),
    Order("o-1002", "u-alice", "paid", Decimal("99.50"), "CNY", _utc(2026, 9, 3, 9, 30)),
    Order("o-1003", "u-alice", "paid", Decimal("250.00"), "CNY", _utc(2026, 9, 5, 18, 15)),
    Order("o-1004", "u-alice", "shipped", Decimal("42.00"), "CNY", _utc(2026, 9, 8, 8, 0)),
    Order("o-1005", "u-alice", "cancelled", Decimal("300.00"), "CNY", _utc(2026, 9, 12, 12, 0)),
    Order("o-1006", "u-alice", "paid", Decimal("18.75"), "CNY", _utc(2026, 9, 15, 21, 45)),
    Order("o-1007", "u-alice", "shipped", Decimal("75.00"), "CNY", _utc(2026, 9, 20, 7, 10)),
    # user bob: 5 orders
    Order("o-2001", "u-bob", "paid", Decimal("60.00"), "CNY", _utc(2026, 9, 2, 11, 0)),
    Order("o-2002", "u-bob", "pending", Decimal("15.00"), "CNY", _utc(2026, 9, 6, 16, 20)),
    Order("o-2003", "u-bob", "cancelled", Decimal("500.00"), "CNY", _utc(2026, 9, 10, 13, 5)),
    Order("o-2004", "u-bob", "shipped", Decimal("33.30"), "CNY", _utc(2026, 9, 18, 19, 40)),
    Order("o-2005", "u-bob", "paid", Decimal("88.80"), "CNY", _utc(2026, 9, 25, 6, 55)),
)


class OrderRepository:
    """Read-only repository over a fixed set of orders."""

    def __init__(self, orders: tuple[Order, ...] | list[Order] = SEED_ORDERS) -> None:
        self._orders: list[Order] = list(orders)

    def list_for_user(self, user_id: str) -> list[Order]:
        return [order for order in self._orders if order.user_id == user_id]

    def get_for_user(self, user_id: str, order_id: str) -> Order | None:
        for order in self._orders:
            if order.id == order_id and order.user_id == user_id:
                return order
        return None
