"""Regression tests: behaviour that must never change.

Covers REQ-ORD-001 .. REQ-ORD-005 and CONSTRAINTS C1, C2, C4, C5 of
``specs/orders.md``. These tests pass before and after the v2.0 change.
"""

from __future__ import annotations

ALICE = {"X-User-Id": "u-alice"}
BOB = {"X-User-Id": "u-bob"}


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_default_pagination_and_order(client):
    resp = client.get("/orders", headers=ALICE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 20
    assert body["offset"] == 0
    assert body["total"] == 7
    assert [item["id"] for item in body["items"]] == [
        "o-1007",
        "o-1006",
        "o-1005",
        "o-1004",
        "o-1003",
        "o-1002",
        "o-1001",
    ]


def test_pagination_slice(client):
    body = client.get("/orders", params={"limit": 2, "offset": 3}, headers=ALICE).json()
    assert body["total"] == 7
    assert body["limit"] == 2
    assert body["offset"] == 3
    assert [item["id"] for item in body["items"]] == ["o-1004", "o-1003"]


def test_pagination_beyond_end(client):
    body = client.get("/orders", params={"limit": 5, "offset": 50}, headers=ALICE).json()
    assert body["items"] == []
    assert body["total"] == 7


def test_limit_upper_bound(client):
    assert client.get("/orders", params={"limit": 100}, headers=ALICE).status_code == 200
    resp = client.get("/orders", params={"limit": 101}, headers=ALICE)
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "INVALID_PAGINATION"
    assert error["field"] == "limit"


def test_limit_lower_bound_and_type(client):
    for raw in ("0", "-3", "abc"):
        resp = client.get("/orders", params={"limit": raw}, headers=ALICE)
        assert resp.status_code == 400, raw
        assert resp.json()["error"]["code"] == "INVALID_PAGINATION"


def test_negative_offset(client):
    resp = client.get("/orders", params={"offset": -1}, headers=ALICE)
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "INVALID_PAGINATION"
    assert error["field"] == "offset"


def test_sort_created_at_asc(client):
    body = client.get("/orders", params={"sort": "created_at_asc"}, headers=ALICE).json()
    assert [item["id"] for item in body["items"]] == [
        "o-1001",
        "o-1002",
        "o-1003",
        "o-1004",
        "o-1005",
        "o-1006",
        "o-1007",
    ]


def test_sort_amount_desc(client):
    body = client.get("/orders", params={"sort": "amount_desc"}, headers=ALICE).json()
    assert [item["amount"] for item in body["items"]] == [
        "300.00",
        "250.00",
        "120.00",
        "99.50",
        "75.00",
        "42.00",
        "18.75",
    ]


def test_invalid_sort(client):
    resp = client.get("/orders", params={"sort": "bogus"}, headers=ALICE)
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "INVALID_SORT"
    assert error["field"] == "sort"


def test_missing_user_header(client):
    resp = client.get("/orders")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


def test_blank_user_header(client):
    resp = client.get("/orders", headers={"X-User-Id": "   "})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHENTICATED"


def test_caller_isolation(client):
    body = client.get("/orders", headers=BOB).json()
    assert body["total"] == 5
    assert {item["id"] for item in body["items"]} == {
        "o-2001",
        "o-2002",
        "o-2003",
        "o-2004",
        "o-2005",
    }


def test_get_single_order(client):
    resp = client.get("/orders/o-1001", headers=ALICE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "o-1001"
    assert body["status"] == "pending"
    assert body["amount"] == "120.00"


def test_get_unknown_order(client):
    resp = client.get("/orders/o-9999", headers=ALICE)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ORDER_NOT_FOUND"


def test_get_foreign_order_is_not_disclosed(client):
    resp = client.get("/orders/o-1001", headers=BOB)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ORDER_NOT_FOUND"


def test_item_shape(client):
    item = client.get("/orders", headers=ALICE).json()["items"][0]
    assert set(item) == {"id", "user_id", "status", "amount", "currency", "created_at"}


def test_unknown_query_parameters_are_ignored(client):
    body = client.get("/orders", params={"unknown_param": "x"}, headers=ALICE).json()
    assert body["total"] == 7
