#!/usr/bin/env bash
# 启动 PowerContext（若未运行）和应用。Linux / WSL 使用；Windows 原生请用 scripts/start.ps1。
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || cp .env.example .env
[ -x .venv/bin/python ] || { echo "缺少 .venv，请先执行：uv sync --python 3.12"; exit 1; }

set -a
# shellcheck disable=SC1091
. ./.env
set +a

APP_PORT="${APP_PORT:-8080}"
APP_HOST="${APP_HOST:-127.0.0.1}"
PC_PORT="${PC_PORT:-8000}"
PC_URL="${POWERCONTEXT_BASE_URL:-http://127.0.0.1:${PC_PORT}}"

if ! command -v powercontext >/dev/null 2>&1; then
  echo "未找到 powercontext，请先安装：uv tool install \"powercontext[cli,server]==1.4.1\""
  echo "如已安装但不在 PATH，请把 \$HOME/.local/bin 加入 PATH。"
  exit 1
fi

if ! curl -sf -m 2 "$PC_URL/v1/server-info" >/dev/null 2>&1; then
  PC_HOME="${POWERCONTEXT_HOME:-$PWD/data/powercontext}"
  mkdir -p "$PC_HOME"
  cat > "$PC_HOME/pc.env" <<'EOF'
POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true
POWERCONTEXT_SERVER_ACCESS_MODE=disabled
EOF
  echo "启动 PowerContext（$PC_URL），数据目录 $PC_HOME"
  ( cd "$PC_HOME" && setsid nohup powercontext server run --host 127.0.0.1 --port "$PC_PORT" --env-file pc.env > server.log 2>&1 < /dev/null & )
  for _ in $(seq 1 60); do
    curl -sf -m 2 "$PC_URL/v1/server-info" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -sf -m 2 "$PC_URL/v1/server-info" >/dev/null 2>&1 || {
    echo "PowerContext 启动失败，查看 $PC_HOME/server.log"; exit 1; }
fi

echo "PowerContext: $PC_URL"
echo "应用地址: http://${APP_HOST}:${APP_PORT}"
exec .venv/bin/uvicorn app.main:app --host "$APP_HOST" --port "$APP_PORT"
