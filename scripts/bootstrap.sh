#!/usr/bin/env bash
# 一键初始化：后端 venv + 依赖 + 测试，前端依赖
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> backend: venv + deps"
cd backend
python3.11 -m venv .venv 2>/dev/null || true
.venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt
echo "==> backend: pytest"
.venv/bin/pytest
cd ..

echo "==> web: npm install"
cd apps/web
npm install --no-fund --no-audit

echo "完成。启动："
echo "  backend: cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000"
echo "  web:     cd apps/web && npm run dev"
