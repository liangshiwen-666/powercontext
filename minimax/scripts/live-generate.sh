#!/usr/bin/env bash
# Generate the patch with the real MiniMax API, then run the full verification.
#
#   export MINIMAX_API_KEY=sk-...
#   bash scripts/live-generate.sh
#
# Steps:
#   1. restore the business project to the pre-change state
#   2. build the context pack and ask MiniMax for a unified diff
#      (git apply / test failures are fed back to the model, up to N attempts)
#   3. run the before/after test matrix and persist task state in PowerContext
#   4. print the resulting records and evidence paths
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=${PYTHON:-.venv/bin/python}
if [ ! -x "$PYTHON" ]; then
  PYTHON=python3
fi
export SDDCTX_PYTHON="$PYTHON"

if [ -z "${MINIMAX_API_KEY:-}" ]; then
  echo "MINIMAX_API_KEY is not set." >&2
  echo "  domestic: export MINIMAX_BASE_URL=https://api.minimax.cn/v1" >&2
  echo "  global  : export MINIMAX_BASE_URL=https://api.minimax.io/v1" >&2
  exit 2
fi

ATTEMPTS=${ATTEMPTS:-3}

echo "== 1/3 restore the pre-change business project =="
"$PYTHON" -m sddctx reset --demo || true

echo "== 2/3 generate the patch with MiniMax and verify ($ATTEMPTS attempts) =="
"$PYTHON" -m sddctx run --live --attempts "$ATTEMPTS"

echo "== 3/3 records =="
ls -1 records/minimax/ | tail -5
ls -1 patches/
echo
echo "task state as seen by a new process:"
"$PYTHON" -m sddctx state show
