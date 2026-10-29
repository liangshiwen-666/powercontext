#!/usr/bin/env bash
# V1 full validation: everything a reviewer should run, in one command.
#
#   bash scripts/verify-all.sh
#
# Requires: setup.sh has been run once and seekdb is reachable.
# No MiniMax API key is needed (the recorded patch is replayed).
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

banner() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

banner "1/8 environment"
bash scripts/check-env.sh

banner "2/8 toolkit tests"
"$PYTHON" -m pytest tests -q

banner "3/8 business project tests (committed post-change tree)"
(cd demo && "$PYTHON" -m pytest -q)

banner "4/8 full replay demo (ingest -> context -> patch -> verify -> state)"
bash scripts/demo.sh

banner "5/8 spec-update demo (re-import a changed specification)"
bash scripts/spec-update.sh

banner "6/8 evidence self-check (patch/meta/records/context/state/logs/docs)"
"$PYTHON" scripts/check-evidence.py

banner "7/8 verification verdict"
"$PYTHON" - <<'PY'
import json
from pathlib import Path

verification = json.loads(Path("evidence/verification.json").read_text(encoding="utf-8"))
print("verdict.ok =", verification["verdict"]["ok"])
for check in verification["verdict"]["checks"]:
    print(f"  [{'PASS' if check['ok'] else 'FAIL'}] {check['check']}: {check['detail']}")

run = json.loads(Path("evidence/run.json").read_text(encoding="utf-8"))
print("context_sha256 =", run["context_sha256"])
print("patch_sha256   =", run["patch"]["sha256"])
print("code_version   =", run["code_version"]["base_git_head"])
PY

banner "8/8 evidence inventory"
find evidence records patches -type f | sort

echo
echo "VERIFY-ALL: PASS (see evidence/verification.json and scripts/check-evidence.py output)"
