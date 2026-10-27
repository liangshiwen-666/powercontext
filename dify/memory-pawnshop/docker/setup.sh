#!/usr/bin/env bash
# 一键部署：拉取 Dify 1.18.0 docker 目录 → 应用游戏配置 → 构建/启动 → 等待 PowerContext → 初始化数据
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
DIFY_DIR="${DIFY_DIR:-$HERE/dify}"
DIFY_TAG="${DIFY_TAG:-1.18.0}"

echo "==> [1/5] Dify docker 目录: $DIFY_DIR"
if [ ! -d "$DIFY_DIR/docker" ]; then
  git clone --depth 1 --branch "$DIFY_TAG" --filter=blob:none --sparse \
    https://github.com/langgenius/dify.git "$DIFY_DIR"
  git -C "$DIFY_DIR" sparse-checkout set docker
fi

cd "$DIFY_DIR/docker"

echo "==> [2/5] 应用配置 (.env / compose override / pc)"
[ -f .env ] || cp .env.example .env
if ! grep -q "GAME_ENV_APPLIED" .env 2>/dev/null; then
  cat "$HERE/.env.game" >> .env
fi
cp -f "$HERE/docker-compose.game.yaml" .
rm -rf pc
cp -r "$HERE/pc" .

echo "==> [3/5] 构建并启动（首次构建 PowerContext 镜像需要几分钟）"
COMPOSE_PROFILES=postgresql docker compose \
  -f docker-compose.yaml -f docker-compose.game.yaml up -d --build

echo "==> [4/5] 等待 PowerContext 就绪"
ready=0
for _ in $(seq 1 90); do
  if curl -fsS http://127.0.0.1:17429/health/ready >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 5
done
if [ "$ready" != "1" ]; then
  echo "PowerContext 未在 7 分钟内就绪，请检查: docker logs powercontext" >&2
  exit 1
fi
curl -s http://127.0.0.1:17429/health/ready; echo

echo "==> [5/5] 初始化游戏数据"
cd "$ROOT"
python3 scripts/seed_pc.py

cat <<EOF

部署完成。

下一步：
  1. 打开 Dify: http://localhost:3000 （首次进入请注册管理员账号）
  2. 设置 → 模型供应商：填入 DeepSeek API Key（deepseek-chat）与 SiliconFlow API Key（BAAI/bge-m3）
  3. 创建应用 → 导入 DSL → $ROOT/DSL/memory-pawnshop.yml
  4. 发布应用，在对话里输入「开门」开始游戏
EOF
