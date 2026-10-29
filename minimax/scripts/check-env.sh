#!/usr/bin/env bash
# Environment smoke test for the review machine.
#
#   bash scripts/check-env.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=${PYTHON:-.venv/bin/python}
if [ ! -x "$PYTHON" ]; then
  PYTHON=python3
fi
case "$PYTHON" in
  /*) ;;
  *) PYTHON="$PWD/$PYTHON" ;;
esac

echo "== platform =="
uname -a || true
"$PYTHON" -V

echo
echo "== python packages =="
"$PYTHON" - <<'PY'
import importlib.metadata as md
for name in ("powercontext", "fastapi", "pytest", "pymysql", "requests"):
    try:
        print(f"{name}=={md.version(name)}")
    except md.PackageNotFoundError:
        print(f"{name}: MISSING")
PY

echo
echo "== seekdb connectivity =="
"$PYTHON" - <<'PY'
try:
    import pymysql
except ImportError:
    raise SystemExit("pymysql is missing: run bash scripts/setup.sh first")
import os
host = os.environ.get("SEEKDB_HOST", "127.0.0.1")
port = int(os.environ.get("SEEKDB_PORT", "2881"))
user = os.environ.get("SEEKDB_USER", "root")
password = os.environ.get("SEEKDB_PASSWORD", "")
try:
    conn = pymysql.connect(host=host, port=port, user=user, password=password, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute("select version()")
        print(f"seekdb ok: {host}:{port} -> {cur.fetchone()[0]}")
    conn.close()
except Exception as exc:  # noqa: BLE001
    raise SystemExit(
        f"cannot connect to seekdb at {host}:{port}: {exc}\n"
        "Start seekdb first (see docs/runbook.md) or set SEEKDB_HOST/SEEKDB_PORT."
    )
PY

echo
echo "== business project tests (committed tree) =="
"$PYTHON" -m pytest demo/tests -q 2>&1 | tail -5

echo
echo "environment looks usable."
