"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
from app.main import create_app
from app.repo import OrderRepository
from fastapi.testclient import TestClient


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(OrderRepository()))
