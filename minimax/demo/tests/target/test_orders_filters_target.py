"""Target functional tests for the v2.0 filtering task.

These tests fail *before* the change because ``GET /orders`` ignores filter
query parameters (a functional failure, not an import or environment error),
and must pass after the change. They map to REQ-ORD-006 .. REQ-ORD-009.
"""

from __future__ import annotations

ALICE = {"X-User-Id": "u-alice"}


def test_filter_by_status(client):
    """REQ-ORD-006: only orders with the requested status are returned."""

    body = client.get("/orders", params={"status": "paid"}, headers=ALICE).json()
    assert body["total"] == 3
    assert [item["id"] for item in body["items"]] == ["o-1006", "o-1003", "o-1002"]
    assert {item["status"] for item in body["items"]} == {"paid"}


def test_filter_by_created_range_inclusive(client):
    """REQ-ORD-007: both range bounds are inclusive."""

    params = {"created_after": "2026-09-05", "created_before": "2026-09-16"}
    body = client.get("/orders", params=params, headers=ALICE).json()
    assert body["total"] == 4
    assert [item["id"] for item in body["items"]] == ["o-1006", "o-1005", "o-1004", "o-1003"]


def test_filter_by_min_amount_inclusive(client):
    """REQ-ORD-008: the lower amount bound is inclusive."""

    body = client.get("/orders", params={"min_amount": "100"}, headers=ALICE).json()
    assert body["total"] == 3
    assert [item["id"] for item in body["items"]] == ["o-1005", "o-1003", "o-1001"]


def test_filters_compose(client):
    """REQ-ORD-009: filters combine and ``total`` reflects the filtered count."""

    params = {"status": "paid", "min_amount": "50", "created_before": "2026-09-20T23:59:59Z"}
    body = client.get("/orders", params=params, headers=ALICE).json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == ["o-1003", "o-1002"]


def test_invalid_filter_values(client):
    """REQ-ORD-006/008: invalid enum and amount values are INVALID_FILTER."""

    for params, field in (
        ({"status": "bogus"}, "status"),
        ({"min_amount": "-1"}, "min_amount"),
        ({"min_amount": "abc"}, "min_amount"),
    ):
        resp = client.get("/orders", params=params, headers=ALICE)
        assert resp.status_code == 400, params
        error = resp.json()["error"]
        assert error["code"] == "INVALID_FILTER", params
        assert error["field"] == field, params


def test_invalid_created_range(client):
    """REQ-ORD-007: unparsable bounds and inverted ranges are INVALID_FILTER."""

    for params, field in (
        ({"created_after": "not-a-date"}, "created_after"),
        ({"created_before": "2026-13-99"}, "created_before"),
        ({"created_after": "2026-09-20", "created_before": "2026-09-01"}, "created_after"),
    ):
        resp = client.get("/orders", params=params, headers=ALICE)
        assert resp.status_code == 400, params
        error = resp.json()["error"]
        assert error["code"] == "INVALID_FILTER", params
        assert error["field"] == field, params
