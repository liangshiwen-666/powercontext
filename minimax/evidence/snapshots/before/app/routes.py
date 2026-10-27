"""HTTP routes for the orders service."""

from __future__ import annotations

from fastapi import APIRouter, Header, Request

from .service import get_order, list_orders, parse_pagination, parse_sort, require_user

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/orders")
def list_orders_route(
    request: Request,
    limit: str | None = None,
    offset: str | None = None,
    sort: str | None = None,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> dict[str, object]:
    user_id = require_user(x_user_id)
    limit_value, offset_value = parse_pagination(limit, offset)
    sort_value = parse_sort(sort)
    return list_orders(
        request.app.state.repo,
        user_id,
        limit=limit_value,
        offset=offset_value,
        sort=sort_value,
    )


@router.get("/orders/{order_id}")
def get_order_route(
    request: Request,
    order_id: str,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
) -> dict[str, str]:
    user_id = require_user(x_user_id)
    return get_order(request.app.state.repo, user_id, order_id)
