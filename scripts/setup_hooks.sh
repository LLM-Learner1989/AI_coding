#!/usr/bin/env bash
# 一键启用仓库级 git hooks：把 core.hooksPath 指向仓库内的 .githooks 目录。
# 新 clone 后运行一次即可：./scripts/setup_hooks.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

git config core.hooksPath "$REPO_ROOT/.githooks"
echo "已启用 git hooks：core.hooksPath = $REPO_ROOT/.githooks"
