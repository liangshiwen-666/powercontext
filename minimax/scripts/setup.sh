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

echo "== 1/5 python =="
"$PYTHON" -V || {
  echo "error: '$PYTHON' is not runnable" >&2
  echo "hint: pass an explicit interpreter, e.g. PYTHON=python3.12 bash scripts/setup.sh" >&2
  exit 2
}
"$PYTHON" - <<'PY'
import sys

version = sys.version_info
print(f"python {version.major}.{version.minor}.{version.micro}")
if version < (3, 12):
    raise SystemExit(
        "error: Python >= 3.12 is required: requirements.lock pins numpy 2.5.4, "
        "whose Requires-Python is >=3.12 (3.11 cannot install the lock file)"
    )
PY

echo "== 2/5 install =="
find_uv() {
  command -v uv 2>/dev/null && return 0
  for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /usr/local/bin/uv /usr/bin/uv; do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

create_venv() {
  if [ -x "$VENV/bin/python" ]; then
    return 0
  fi
  rm -rf "$VENV"
  echo "creating venv at $VENV"
  if "$PYTHON" -m venv "$VENV" 2>/tmp/sddctx-venv.log; then
    return 0
  fi
  echo "warning: '$PYTHON -m venv' failed (the venv/ensurepip module may be missing):" >&2
  sed -n '1,4p' /tmp/sddctx-venv.log >&2 || true
  rm -rf "$VENV"
  UV_BIN=$(find_uv || true)
  if [ -n "$UV_BIN" ]; then
    echo "falling back to: $UV_BIN venv --seed $VENV --python $PYTHON" >&2
    if "$UV_BIN" venv --seed "$VENV" --python "$PYTHON" >&2; then
      return 0
    fi
    echo "retrying with the default uv interpreter (may download a managed Python)..." >&2
    if "$UV_BIN" venv --seed "$VENV" >&2; then
      return 0
    fi
  fi
  echo "error: cannot create a virtual environment with $PYTHON." >&2
  echo "hint: install the venv module, e.g. 'sudo apt install python3.12-venv'," >&2
  echo "      or install uv and run 'uv venv --seed .venv --python 3.12'." >&2
  return 1
}
if ! create_venv; then
  exit 2
fi
# shellcheck disable=SC1091
. "$VENV/bin/activate"
python -m pip install --upgrade pip -q || echo "warning: pip upgrade failed (continuing with the bundled pip)"

PIP_ARGS=()
if [ -n "$MIRROR" ]; then
  PIP_ARGS=(-i "$MIRROR")
fi

if [ -f requirements.lock ]; then
  echo "installing pinned requirements from requirements.lock"
  python -m pip install "${PIP_ARGS[@]}" -r requirements.lock
  python -m pip install "${PIP_ARGS[@]}" --no-deps -e .
else
  echo "installing project with demo extra"
  python -m pip install "${PIP_ARGS[@]}" -e ".[demo]"
fi

echo "== 3/5 versions =="
python - <<'PY'
import importlib.metadata as md
for name in ("powercontext", "fastapi", "pytest", "pymysql", "requests"):
    try:
        print(f"{name}=={md.version(name)}")
    except md.PackageNotFoundError:
        print(f"{name}: not installed")
PY

echo "== 4/5 seekdb connectivity =="
python - <<'PY'
import os
import sys

try:
    import pymysql
except ImportError:
    raise SystemExit("error: pymysql is missing from the environment")

host = os.environ.get("SEEKDB_HOST", "127.0.0.1")
port = int(os.environ.get("SEEKDB_PORT", "2881"))
user = os.environ.get("SEEKDB_USER", "root")
password = os.environ.get("SEEKDB_PASSWORD", "")
try:
    conn = pymysql.connect(host=host, port=port, user=user, password=password, connect_timeout=5)
except Exception as exc:  # noqa: BLE001
    raise SystemExit(
        f"error: cannot connect to seekdb at {host}:{port}: {exc}\n"
        "hint: start it with `sudo systemctl start seekdb` or\n"
        "      `docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb:latest`,\n"
        "      then re-run `bash scripts/setup.sh`."
    )
with conn.cursor() as cur:
    cur.execute("select version()")
    print("seekdb:", cur.fetchone()[0])
conn.close()
PY

echo "== 5/5 database schema =="
python -m sddctx init
echo
echo "setup complete. Next: bash scripts/demo.sh   (or bash scripts/verify-all.sh)"
