#!/usr/bin/env bash
# 启动后端（FastAPI/Uvicorn）@ http://127.0.0.1:2024
set -e
cd "$(dirname "$0")/.." || exit 1
PY=python3
[ -x ".venv/bin/python3" ] && PY=".venv/bin/python3"
exec "$PY" -m uvicorn agent.app:app --host 127.0.0.1 --port 2024
