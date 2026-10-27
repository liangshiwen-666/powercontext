#!/usr/bin/env bash
# Reset the demo to a reproducible starting point.
#
#   bash scripts/reset.sh          # revert the business project + clear runtime data
#   bash scripts/reset.sh --demo   # revert the business project only
#   bash scripts/reset.sh --data   # clear runtime data only (.data/)
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=${PYTHON:-.venv/bin/python}
if [ ! -x "$PYTHON" ]; then
  PYTHON=python3
fi

MODE=${1:-all}
case "$MODE" in
  --demo) "$PYTHON" -m sddctx reset --demo ;;
  --data) "$PYTHON" -m sddctx reset --data ;;
  all|"") "$PYTHON" -m sddctx reset --demo --data ;;
  *) echo "usage: $0 [--demo|--data]" >&2; exit 2 ;;
esac
echo "reset complete ($MODE)"
