#!/usr/bin/env bash
# 启动前端（Vite）@ http://127.0.0.1:3000
set -e
export VITE_DASHBOARD_API_BASE_URL=http://127.0.0.1:2024
cd "$(dirname "$0")/../ui" || exit 1
exec node node_modules/vite/bin/vite.js dev --host 127.0.0.1 --port 3000
