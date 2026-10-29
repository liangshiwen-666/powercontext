"""Shared pytest hooks for the toolkit tests.

The seekdb-backed test modules use a database whose name is unique per pytest
process (``SDDCTX_TEST_DB`` or ``sddctx_test_<pid>``), so two test runs on the
same seekdb server cannot interfere with each other.  This hook drops the
per-process database when the session ends.
"""

from __future__ import annotations

import os

import pytest


def _test_db_name() -> str:
    return os.environ.get("SDDCTX_TEST_DB") or f"sddctx_test_{os.getpid()}"


@pytest.fixture(scope="session")
def test_db_name() -> str:
    """Name of the throw-away seekdb database used by this test session."""

    return _test_db_name()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    name = _test_db_name()
    if not name.startswith("sddctx_test"):
        return
    try:
        import pymysql
    except ImportError:  # pragma: no cover - server tests are skipped anyway
        return
    try:
        connection = pymysql.connect(
            host=os.environ.get("SEEKDB_HOST", "127.0.0.1"),
            port=int(os.environ.get("SEEKDB_PORT", "2881")),
            user=os.environ.get("SEEKDB_USER", "root"),
            password=os.environ.get("SEEKDB_PASSWORD", ""),
            connect_timeout=3,
        )
    except Exception:
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
    except Exception:
        pass
    finally:
        connection.close()
