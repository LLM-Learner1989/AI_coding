# AI_CODING

AI Coding 智能体。一个运行在**本地工作区**、面向 **GitHub 仓库**的「人在回路」编码智能体：用户用自然语言描述需求 → 智能体先输出技术方案并等待确认 → 用户确认后才实施代码、测试、提交并创建 GitHub Pull Request。除编码外，还支持项目分析、问答、仓库同步、工作区检查、代码审查（Reviewer 子 Agent）等只读任务。

## 技术栈

- **后端**：Python + FastAPI / Uvicorn；`deepagents`（DeepAgents）+ LangGraph / LangChain；Pydantic；httpx/requests
- **模型**：DeepSeek `deepseek-v4-pro`（OpenAI 兼容协议，经 `DEEPSEEK_BASE_URL` 接入；意图分类用低温度、无流式、小 `max_tokens` 的变体）
- **前端**：Vue 3 + Vite + Pinia + Axios + markdown-it + DOMPurify
- **存储**：SQLite 三库 + 本地文件工作区（见「数据层」）
- **代码托管**：仅 GitHub（REST API v3，Bearer Token 认证）

## 目录结构与模块职责

### `agent/` — 后端（核心）

| 路径 | 职责 |
| --- | --- |
| `agent/app.py` | FastAPI 应用入口：加载环境变量/日志、配 CORS、注册路由 |
| `agent/server.py` | **Agent 装配中心**：`get_agent(config)` 把模型、工具、子 Agent、backend、permissions、middleware、skills、checkpointer 串成一个 DeepAgent；按 thread 复用 `LocalShellBackend` |
| `agent/api/routes.py` | 健康检查 `GET /health` |
| `agent/api/dashboard_routes.py` | Dashboard REST + SSE：`/me`、`/options`、`/threads`、`/threads/{id}`、`DELETE`、`POST .../stream-message`（SSE 主链路） |
| `agent/core/runtime.py` | **任务调度中心**：任务路径分流、thread/run 状态、方案确认闭环、收尾写回记忆 |
| `agent/core/streaming_runtime.py` | 把 DeepAgents `stream_events(version="v3")` 的 raw events 转成前端事件 |
| `agent/core/task_intent.py` | 意图分类（coding/analysis/planning/qa/sync/inspect/review） |
| `agent/core/graph.py` | `get_store()` / `get_checkpointer()` / `get_langgraph_store()` 单例 |
| `agent/core/settings.py` | 路径与目录配置、`GITHUB_DEFAULT_BRANCH` |
| `agent/core/memory.py` / `repo_memory.py` / `repo_memory_update.py` | 工作区事实记忆 + 仓库级长期记忆（按 owner/repo 隔离） |
| `agent/core/checkpoint_history.py` | 从 checkpoint 整理前端可见历史正文 |
| `agent/core/middleware/` | 上下文注入、消息清洗、工具入参清洗、工具异常兜底等中间件 |
| `agent/tools/github_api.py` | GitHub REST API 低层封装（解析仓库 URL、创建/复用 PR、评论、读取 commits/files/comments） |
| `agent/tools/github_tools.py` | 把 GitHub API 包装成模型可调用工具（`open_github_pull_request` / `publish_github_pr_comment` / `get_github_pull_request_context`） |
| `agent/tools/` | 其余工具：`web_search`、`fetch_url`、review findings 等 |
| `agent/backends/local_shell.py` | **工程边界**：`LocalShellBackend` 实现 DeepAgents 文件协议与 `execute`，做虚拟路径映射、危险命令拦截、Token 脱敏、Git askpass 认证（macOS 用 bash askpass） |
| `agent/backends/workspace.py` / `permissions.py` | 工作区路径边界、命令白名单/安全校验 |
| `agent/store/sqlite_store.py` | 业务数据 `LocalSqliteStore` |
| `agent/skills/` / `agent/reviewer_rules/` | DeepAgents 技能与默认审查规则 |

### `ui/` — 前端

Vue 3 + Pinia。`src/stores/agent.js` 消费 SSE 事件；`src/api/client.js`/`sse.js` 做 REST/SSE；`vite.config.js` 把 `/dashboard/api` 代理到 `VITE_DASHBOARD_API_BASE_URL`（默认 `http://127.0.0.1:2024`）。

### 其余

- `scripts/`：`start_all.py`（一键起前后端）、`stop_all.py`（按端口停进程，macOS 用 `lsof`）、`start_backend.sh` / `start_ui.sh`（macOS 启动脚本）。旧的 `.cmd` 为 Windows 遗留，macOS 不再使用。
- `data/` / `logs/`：默认在项目内，但实际路径由 `.env` 的 `AICODING_DATA_DIR` / `AICODING_LOG_DIR` 决定。
- `.env` / `.env.example`：环境变量；`requirements.txt`：后端依赖清单。

## 数据层（三库）

- `store.sqlite` — 业务摘要（threads/runs/run_events/review_findings 等）
- `checkpoints.sqlite` — LangGraph thread state 与消息历史（**聊天正文唯一权威来源**）
- `langgraph_store.sqlite` — 仓库级长期记忆 `/memories/{owner}/{repo}.md`

## 常用启动命令（macOS）

> 后端依赖已装在项目根 `.venv`（Anaconda Python 3.14 创建）。不要用系统 `python3`（3.9，太旧）。

```bash
# 1.（首次）建虚拟环境并装依赖
/opt/anaconda3/bin/python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 配置 .env（已生成，按需改 GITHUB_TOKEN / DEFAULT_REPO_URL / GITHUB_DEFAULT_BRANCH）
# 直接编辑 .env

# 3.（首次）装前端依赖
cd ui && npm install && cd ..

# 4. 启动（后端 2024 / 前端 3000）
.venv/bin/python scripts/start_all.py
# 或分开：
#   ./scripts/start_backend.sh
#   ./scripts/start_ui.sh

# 停止
.venv/bin/python scripts/stop_all.py
```

后端健康检查：`curl http://127.0.0.1:2024/health`。

后端健康检查：`curl http://127.0.0.1:2024/health`。

## 环境变量（`.env`）

- `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` — 必填，模型接入
- `MAIN_MODEL`（默认 `deepseek-v4-pro`）
- `GITHUB_TOKEN` — GitHub PAT，经 Git askpass（`x-access-token` + token）注入，绝不写进命令/日志
- `GITHUB_API_BASE_URL`（默认 `https://api.github.com`）
- `GITHUB_DEFAULT_BRANCH`（默认 `main`；若仓库默认分支是 `master` 请改这里）
- `DEFAULT_REPO_URL`（默认占位 `https://github.com/owner/repo.git`；请改成你自己的默认仓库）
- `AI_WORKSPACE_ROOT` / `LOCAL_SHELL_WORKSPACE` — 工作区根目录（默认 `~/ai_workspace`）
- `AICODING_DATA_DIR`、`CHECKPOINT_DB_PATH`、`STORE_DB_PATH`、`LANGGRAPH_STORE_DB_PATH` — SQLite 路径
- `AICODING_LOG_DIR`、`AICODING_LOG_LEVEL`、`AICODING_LOG_WHEN`、`AICODING_LOG_INTERVAL`、`AICODING_LOG_RETENTION_DAYS` — 日志配置

## 数据流转（典型请求）

以「帮我给这个仓库增加一个部门管理模块」为例：

1. **前端**：`ChatComposer` → Pinia `submit()` → `POST /dashboard/api/threads/stream-message`（SSE）。
2. **API 边界**：`dashboard_routes.py` 规范化仓库 URL → `initialize_task_record` 建 thread → 先发 `thread_snapshot`/`user_message`/启动提示 → 后台线程跑 `run_agent_task`，主线程消费 `asyncio.Queue` 吐 SSE。
3. **调度**：`runtime.run_agent_task` 先过直达分支，再 `classify_task_kind` 判意图 → `coding`。
4. **人在回路**：coding 且无已确认方案 → 强制转 `run_plan_response_task`（`planning` 只读）。
5. **装配**：`_build_agent_for_runtime` → `server.get_agent` 组装 DeepAgent（模型 + 工具 + `LocalShellBackend` + 权限 + 中间件 + skills + checkpointer/store）。
6. **运行回传**：`streaming_runtime.run_agent_with_event_stream` 消费 `stream_events(v3)` → `text_delta`/`todo_delta` → SSE → 前端；Agent 用虚拟路径读写、`execute` 跑 git（Token 经 `GIT_ASKPASS` 注入），输出中文方案。
7. **确认实施**：用户「确认」→ `_is_approval_prompt` → 从 checkpoint 反查方案 → `coding`，改文件、测试、`git add/commit/push`、`open_github_pull_request` 建 PR。
8. **收尾**：结论写回 `langgraph_store.sqlite`；`store.sqlite` 更新状态/`pr_url`/`branch_name`；SSE 发 `thread_done`+`done`；刷新从 checkpoint 恢复历史。

**数据边界**：聊天正文以 `checkpoints.sqlite` 为唯一权威；`store.sqlite` 只存业务摘要；`langgraph_store.sqlite` 只存仓库记忆。

## 关键约定与注意事项

- **任务类型决定权限**：只有 `coding` 允许写文件/提交/push/建 PR；其余均为只读。权限在 runtime、系统提示词、`LocalShellBackend` 三层同时约束。
- **虚拟路径 vs 命令路径**：文件工具用 `/projects/<repo>/...`；`execute` 默认在 `projects` 目录下执行，命令里直接用 `<repo>`。
- **仅支持 GitHub**：`https://github.com/<owner>/<repo>`，不支持 Gitee/GitLab 等。
- **默认分支**：以 `GITHUB_DEFAULT_BRANCH` 为准（默认 `main`）；pull 和 PR base 都会读它。
- **输出语言**：面向用户用中文；代码/路径/命令/API 名可保留英文。
- **Token 安全**：所有进日志/模型上下文的文本经 `mask_token` 脱敏；token 绝不写进命令、文件、commit、PR。
