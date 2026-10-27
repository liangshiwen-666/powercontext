#!/usr/bin/env bash
# End-to-end replay demo of the SDD context toolkit.
#
#   bash scripts/demo.sh
#
# Produces, in order:
#   1. imported specs / code / tests in seekdb
#   2. a context pack with real citations (evidence/context/)
#   3. the recorded MiniMax patch replayed onto the business project
#   4. before/after test transcripts (evidence/logs/, evidence/verification.json)
#   5. task state persisted in PowerContext
#   6. the persisted state read back from a *new* process (module restart)
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON=${PYTHON:-.venv/bin/python}
if [ ! -x "$PYTHON" ]; then
  PYTHON=python3
fi
export SDDCTX_PYTHON="$PYTHON"

banner() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

banner "1/6 initialise seekdb schema"
"$PYTHON" -m sddctx init

banner "2/6 restore a clean starting point (pre-change code, empty task state)"
"$PYTHON" -m sddctx reset --demo --data

banner "3/6 full pipeline (ingest -> context -> patch replay -> verify -> state)"
"$PYTHON" -m sddctx run --fresh

banner "4/6 task state read back after the module process restarted"
"$PYTHON" -m sddctx state show

banner "5/6 context pack preview (specs / code / tests / state)"
"$PYTHON" -m sddctx context --print-text | head -120

banner "6/6 evidence written under evidence/"
find evidence -maxdepth 2 -type f -print | sort

echo
echo "demo complete: see evidence/verification.json and evidence/run.json"
