from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    """统一使用 UTC 时间存储，避免本地时区变化影响排序和排查。"""

    return datetime.now(UTC).isoformat()


class LocalSqliteStore:
    """本项目业务数据 Store。

    这个类只负责保存“平台业务摘要”，不保存完整聊天历史：
    - threads：任务列表和当前状态。
    - runs：每次运行的开始、结束、失败原因。
    - review_findings：Reviewer Agent 发现的问题。
    - settings：本项目的少量键值配置。

    完整 messages 和 LangGraph thread state 由 checkpoint 数据库保存。
    """

    def __init__(self, db_path: Path):
        # check_same_thread=False 允许 FastAPI 后台任务、SSE 读取和工具写事件跨线程访问。
        # 但 sqlite3 的同一个连接不能无锁并发提交，所以必须用 RLock 串行化所有数据库操作。
        self.db_path = db_path
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._configure_connection()
        self._init_schema()

    def _configure_connection(self) -> None:
        """配置 SQLite 连接，提升本地多线程读写稳定性。

        WAL 模式允许读写更好地并发；busy_timeout 可以让短时间锁等待自动重试，
        避免 Agent 正在写事件时前端 SSE 读取刚好撞上锁就失败。
        """

        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=30000")
            # 事件记录有时会早于 thread 主记录写入；这里不启用外键强校验，
            # 由 delete_thread 主动清理附属记录即可，避免展示事件影响主任务。
            self._conn.execute("PRAGMA foreign_keys=OFF")

    def close(self) -> None:
        """关闭 SQLite 连接，主要供独立验证脚本释放临时数据库文件。"""

        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        """初始化业务表。

        CREATE TABLE IF NOT EXISTS 让服务可以重复启动；
        第一次启动会创建表，后续启动只复用已有 SQLite 文件。
        """

        with self._lock:
            self._conn.executescript(
                """
            CREATE TABLE IF NOT EXISTS threads (
              thread_id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              user_prompt TEXT,
              repo_url TEXT,
              repo_owner TEXT,
              repo_name TEXT,
              branch_name TEXT,
              pr_url TEXT,
              latest_run_status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runs (
              run_id TEXT PRIMARY KEY,
              thread_id TEXT NOT NULL,
              status TEXT NOT NULL,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              error TEXT,
              FOREIGN KEY(thread_id) REFERENCES threads(thread_id)
            );

            CREATE TABLE IF NOT EXISTS run_events (
              id TEXT PRIMARY KEY,
              thread_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              title TEXT NOT NULL,
              status TEXT NOT NULL,
              detail TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(thread_id) REFERENCES threads(thread_id)
            );

            CREATE TABLE IF NOT EXISTS review_findings (
              id TEXT PRIMARY KEY,
              thread_id TEXT NOT NULL,
              file TEXT NOT NULL,
              line INTEGER,
              severity TEXT NOT NULL,
              title TEXT NOT NULL,
              description TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(thread_id) REFERENCES threads(thread_id)
            );

            CREATE TABLE IF NOT EXISTS settings (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            """
            )
            self._ensure_column("threads", "user_prompt", "TEXT")
            self._conn.commit()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        """为旧 SQLite 数据库补充新增列。

        本项目会不断迭代字段，不能要求每次都删除 data/store.sqlite。
        PRAGMA table_info 可以判断列是否存在，缺失时用 ALTER TABLE 做轻量迁移。
        """

        with self._lock:
            rows = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            existing_columns = {row["name"] for row in rows}
            if column not in existing_columns:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def upsert_thread(
        self,
        *,
        thread_id: str,
        title: str,
        repo_url: str | None = None,
        repo_owner: str | None = None,
        repo_name: str | None = None,
        branch_name: str | None = None,
        pr_url: str | None = None,
        user_prompt: str | None = None,
        latest_run_status: str = "pending",
    ) -> None:
        with self._lock:
            now = utc_now()
            existing = self.get_thread(thread_id)
            created_at = existing["created_at"] if existing else now
            self._conn.execute(
                """
                INSERT INTO threads (
                  thread_id, title, user_prompt, repo_url, repo_owner, repo_name, branch_name, pr_url,
                  latest_run_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(thread_id) DO UPDATE SET
                  title=threads.title,
                  user_prompt=COALESCE(excluded.user_prompt, threads.user_prompt),
                  repo_url=COALESCE(excluded.repo_url, threads.repo_url),
                  repo_owner=COALESCE(excluded.repo_owner, threads.repo_owner),
                  repo_name=COALESCE(excluded.repo_name, threads.repo_name),
                  branch_name=COALESCE(excluded.branch_name, threads.branch_name),
                  pr_url=COALESCE(excluded.pr_url, threads.pr_url),
                  latest_run_status=excluded.latest_run_status,
                  updated_at=excluded.updated_at
                """,
                (
                    thread_id,
                    title,
                    user_prompt,
                    repo_url,
                    repo_owner,
                    repo_name,
                    branch_name,
                    pr_url,
                    latest_run_status,
                    created_at,
                    now,
                ),
            )
            self._conn.commit()

    def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM threads WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            return self._row_to_dict(row)

    def list_threads(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM threads ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    def update_thread_status(
        self,
        thread_id: str,
        status: str,
        *,
        pr_url: str | None = None,
        branch_name: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE threads
                SET latest_run_status = ?,
                    pr_url = COALESCE(?, pr_url),
                    branch_name = COALESCE(?, branch_name),
                    updated_at = ?
                WHERE thread_id = ?
                """,
                (status, pr_url, branch_name, utc_now(), thread_id),
            )
            self._conn.commit()

    def record_run(
        self,
        *,
        run_id: str,
        thread_id: str,
        status: str,
        error: str | None = None,
        finished: bool = False,
    ) -> None:
        with self._lock:
            now = utc_now()
            self._conn.execute(
                """
                INSERT INTO runs (run_id, thread_id, status, started_at, finished_at, error)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                  status=excluded.status,
                  finished_at=excluded.finished_at,
                  error=excluded.error
                """,
                (run_id, thread_id, status, now, now if finished else None, error),
            )
            self._conn.commit()

    def add_run_event(
        self,
        *,
        event_id: str,
        thread_id: str,
        kind: str,
        title: str,
        status: str,
        detail: str | None = None,
    ) -> None:
        """记录 Agent 运行过程中的简洁步骤。

        这些事件用于 Dashboard 实时展示“正在做什么”，不保存大段命令输出。
        相同 event_id 可以被更新，例如先写 in_progress，完成后改成 completed。
        """

        with self._lock:
            now = utc_now()
            self._conn.execute(
                """
                INSERT INTO run_events (
                  id, thread_id, kind, title, status, detail, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  kind=excluded.kind,
                  title=excluded.title,
                  status=excluded.status,
                  detail=excluded.detail,
                  updated_at=excluded.updated_at
                """,
                (event_id, thread_id, kind, title, status, detail, now, now),
            )
            self._conn.commit()

    def list_run_events(self, thread_id: str) -> list[dict[str, Any]]:
        """按创建顺序读取运行步骤。"""

        with self._lock:
            rows = self._conn.execute(
                """
                SELECT *
                FROM run_events
                WHERE thread_id = ?
                ORDER BY created_at ASC
                """,
                (thread_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def clear_run_events(self, thread_id: str) -> None:
        """清空某个 thread 的临时过程事件。

        run_events 只服务当前/最近一次运行的过程展示。
        """

        with self._lock:
            self._conn.execute("DELETE FROM run_events WHERE thread_id = ?", (thread_id,))
            self._conn.commit()

    def finish_open_run_events(self, thread_id: str, *, status: str = "completed") -> None:
        """把仍处于运行中的展示事件收尾。

        Agent 的官方 tool_calls 流有时只发起始事件，真正完成状态由工具内部事件记录。
        为避免前端在任务结束后还显示“运行中...”，任务完成或失败时统一关闭残留事件。
        """

        with self._lock:
            self._conn.execute(
                """
                UPDATE run_events
                SET status = ?,
                    updated_at = ?
                WHERE thread_id = ?
                  AND status IN ('pending', 'in_progress')
                """,
                (status, utc_now(), thread_id),
            )
            self._conn.commit()

    def get_latest_run(self, thread_id: str) -> dict[str, Any] | None:
        """读取某个 thread 最近一次运行记录。

        Dashboard 摘要需要把失败原因展示给前端，否则用户只能看到 error 状态，
        不知道是模型、Git、GitHub 还是本地权限导致的问题。
        """

        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM runs
                WHERE thread_id = ?
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (thread_id,),
            ).fetchone()
            return self._row_to_dict(row)

    def delete_thread(self, thread_id: str) -> bool:
        """删除一个 dashboard 会话及其业务附属记录。

        这里清理的是业务 Store，不直接清理 LangGraph checkpoint。
        本项目第一阶段列表页只读取 threads/runs/review_findings，
        所以删除这些记录后，前端侧边栏会立即干净。
        """

        with self._lock:
            existing = self.get_thread(thread_id)
            if existing is None:
                return False
            self._conn.execute("DELETE FROM review_findings WHERE thread_id = ?", (thread_id,))
            self._conn.execute("DELETE FROM run_events WHERE thread_id = ?", (thread_id,))
            self._conn.execute("DELETE FROM runs WHERE thread_id = ?", (thread_id,))
            self._conn.execute("DELETE FROM threads WHERE thread_id = ?", (thread_id,))
            self._conn.commit()
            return True

    def add_finding(
        self,
        *,
        finding_id: str,
        thread_id: str,
        file: str,
        line: int | None,
        severity: str,
        title: str,
        description: str,
        status: str = "open",
    ) -> None:
        with self._lock:
            now = utc_now()
            self._conn.execute(
                """
                INSERT INTO review_findings (
                  id, thread_id, file, line, severity, title, description, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  file=excluded.file,
                  line=excluded.line,
                  severity=excluded.severity,
                  title=excluded.title,
                  description=excluded.description,
                  status=excluded.status,
                  updated_at=excluded.updated_at
                """,
                (finding_id, thread_id, file, line, severity, title, description, status, now, now),
            )
            self._conn.commit()

    def list_findings(self, thread_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM review_findings WHERE thread_id = ? ORDER BY created_at ASC",
                (thread_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def set_setting(self, key: str, value: Any) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), utc_now()),
            )
            self._conn.commit()

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            if row is None:
                return default
            return json.loads(row["value"])
