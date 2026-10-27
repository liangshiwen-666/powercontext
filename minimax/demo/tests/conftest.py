"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.repo import OrderRepository


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(OrderRepository()))
