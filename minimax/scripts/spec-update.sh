#!/usr/bin/env bash
# Demonstrate that re-importing an updated specification changes the context.
#
#   bash scripts/spec-update.sh
#
# 1. import the v1.0 spec (demo/specs/history/orders.v1.md) -> context A
# 2. import the v2.0 spec (demo/specs/orders.md)            -> context B
# 3. print the requirement entries added/changed between A and B
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
export SDDCTX_PYTHON="$PYTHON"

TASK=demo/tasks/orders-filters.task.md
OUT=evidence/context
ING=evidence/ingest
mkdir -p "$OUT" "$ING"

echo "== import v1.0 spec (initial data) =="
"$PYTHON" -m sddctx ingest --specs demo/specs/history --json > "$ING/ingest-v1.json"
"$PYTHON" -m sddctx context --task "$TASK" --out "$OUT/spec-v1-context.json" >/dev/null
echo "   context A: $OUT/spec-v1-context.json"

echo "== re-import v2.0 spec (updated: REQ-ORD-006..009 added) =="
"$PYTHON" -m sddctx ingest --json > "$ING/ingest-v2.json"
"$PYTHON" -m sddctx context --task "$TASK" --out "$OUT/spec-v2-context.json" >/dev/null
echo "   context B: $OUT/spec-v2-context.json"

echo "== difference between context A and context B =="
"$PYTHON" scripts/diff_context.py "$OUT/spec-v1-context.json" "$OUT/spec-v2-context.json"

echo
echo "spec-update demo complete."
