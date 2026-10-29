#!/usr/bin/env bash
# Read the task state back from a *new process* (module restart evidence).
#
#   bash scripts/state-readback.sh
#
# Writes records/powercontext/state-readback.json and prints a summary.
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

"$PYTHON" -m sddctx state export --out records/powercontext/state-readback.json
