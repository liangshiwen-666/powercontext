#!/usr/bin/env bash
# Create the Python environment and initialise the seekdb schema.
#
# Usage:
#   bash scripts/setup.sh                 # normal install (PyPI)
#   MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple bash scripts/setup.sh
#   PYTHON=python3.12 bash scripts/setup.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=${PYTHON:-python3}
VENV=${VENV:-.venv}
MIRROR=${MIRROR:-}

echo "== python =="
"$PYTHON" -V

if [ ! -d "$VENV" ]; then
  echo "== creating venv at $VENV =="
  "$PYTHON" -m venv "$VENV"
fi
# shellcheck disable=SC1091
. "$VENV/bin/activate"
python -m pip install --upgrade pip -q

PIP_ARGS=()
if [ -n "$MIRROR" ]; then
  PIP_ARGS=(-i "$MIRROR")
fi

if [ -f requirements.lock ]; then
  echo "== installing pinned requirements =="
  python -m pip install "${PIP_ARGS[@]}" -r requirements.lock
  python -m pip install "${PIP_ARGS[@]}" --no-deps -e .
else
  echo "== installing project with demo extra =="
  python -m pip install "${PIP_ARGS[@]}" -e ".[demo]"
fi

echo "== versions =="
python - <<'PY'
import importlib.metadata as md
for name in ("powercontext", "fastapi", "pytest", "pymysql", "requests"):
    try:
        print(f"{name}=={md.version(name)}")
    except md.PackageNotFoundError:
        print(f"{name}: not installed")
PY

echo "== seekdb schema =="
python -m sddctx init
echo
echo "setup complete. Next: bash scripts/demo.sh"
