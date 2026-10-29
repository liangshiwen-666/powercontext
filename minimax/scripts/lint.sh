#!/usr/bin/env bash
# Lint the toolkit (ruff). Optional: skipped when ruff is unavailable.
#
#   bash scripts/lint.sh            # check
#   bash scripts/lint.sh --fix      # apply safe fixes
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -n "${RUFF:-}" ]; then
  RUFF_BIN="$RUFF"
elif command -v ruff >/dev/null 2>&1; then
  RUFF_BIN=$(command -v ruff)
elif [ -x .venv/bin/ruff ]; then
  RUFF_BIN=.venv/bin/ruff
else
  echo "ruff not found — install it with: python -m pip install ruff   (or set RUFF=/path/to/ruff)"
  echo "lint skipped"
  exit 0
fi

echo "using $RUFF_BIN"
"$RUFF_BIN" check sddctx tests demo/app demo/tests scripts/diff_context.py "$@"
