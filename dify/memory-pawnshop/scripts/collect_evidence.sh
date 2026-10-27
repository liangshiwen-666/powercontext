#!/usr/bin/env bash
# 收集运行证据：Dify 容器日志摘要、PowerContext 健康/统计、当票与记忆检索结果。
set -u
OUT="${1:-/mnt/d/oceanbase/dify-game/submission/evidence}"
DIFY_DIR="${DIFY_DIR:-$HOME/dify/docker}"
mkdir -p "$OUT"
TS=$(date +%Y%m%d-%H%M%S)

echo "==> container status"
(cd "$DIFY_DIR" && COMPOSE_PROFILES=postgresql docker compose -f docker-compose.yaml -f docker-compose.game.yaml ps) > "$OUT/containers_$TS.txt" 2>&1

echo "==> powercontext health"
curl -s -m 20 http://127.0.0.1:17429/health/ready > "$OUT/pc_ready_$TS.json" 2>&1
curl -s -m 20 http://127.0.0.1:17429/v1/stats -X POST -H 'Content-Type: application/json' \
  -d '{"selection":{"kind":"all"}}' > "$OUT/pc_stats_$TS.json" 2>&1

echo "==> dify logs (api/worker)"
docker logs --tail 300 dify-api > "$OUT/dify_api_$TS.log" 2>&1 || true
docker logs --tail 200 dify-worker > "$OUT/dify_worker_$TS.log" 2>&1 || true
docker logs --tail 100 powercontext > "$OUT/powercontext_$TS.log" 2>&1 || true

echo "==> grep evidence lines"
grep -E "HTTP Request|http-request|memory\.search|atomic-memory|prepared" "$OUT/dify_api_$TS.log" 2>/dev/null | tail -30 >> "$OUT/evidence_lines_$TS.txt" || true

echo "written to $OUT (timestamp $TS)"
