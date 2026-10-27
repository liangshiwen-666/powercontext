"""Domain models for the orders service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

ORDER_STATUSES: tuple[str, ...] = ("pending", "paid", "shipped", "cancelled")
SORT_KEYS: tuple[str, ...] = ("created_at_desc", "created_at_asc", "amount_desc")
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


@dataclass(frozen=True)
class Order:
    """A single order owned by exactly one user."""

    id: str
    user_id: str
    status: str
    amount: Decimal
    currency: str
    created_at: datetime

    def to_dict(self) -> dict[str, str]:
        created = self.created_at.isoformat()
        if created.endswith("+00:00"):
            created = created[: -len("+00:00")] + "Z"
        return {
            "id": self.id,
            "user_id": self.user_id,
            "status": self.status,
            "amount": format(self.amount, ".2f"),
            "currency": self.currency,
            "created_at": created,
        }
