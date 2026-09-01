from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class SessionStore:
    """Small SQLite index for task metadata; detailed events remain in JSONL."""

    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(self.path), check_same_thread=False, timeout=30.0
        )
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    task TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    finished_at TEXT,
                    log_path TEXT NOT NULL,
                    report_json TEXT,
                    resumed_from TEXT,
                    draft_json TEXT,
                    pending_json TEXT,
                    session_json TEXT,
                    before_json TEXT,
                    require_draft_review INTEGER NOT NULL DEFAULT 0,
                    explicit_skills_json TEXT
                )
                """
            )
            columns = {
                row[1]
                for row in self._connection.execute("PRAGMA table_info(tasks)").fetchall()
            }
            if "resumed_from" not in columns:
                self._connection.execute("ALTER TABLE tasks ADD COLUMN resumed_from TEXT")
            for column in ("draft_json", "pending_json", "session_json", "before_json"):
                if column not in columns:
                    self._connection.execute(f"ALTER TABLE tasks ADD COLUMN {column} TEXT")
            if "require_draft_review" not in columns:
                self._connection.execute(
                    "ALTER TABLE tasks ADD COLUMN require_draft_review INTEGER NOT NULL DEFAULT 0"
                )
            if "explicit_skills_json" not in columns:
                self._connection.execute(
                    "ALTER TABLE tasks ADD COLUMN explicit_skills_json TEXT"
                )
            self._connection.commit()

    @staticmethod
    def _encode_report(report: Any) -> str | None:
        if report is None:
            return None
        return json.dumps(report, ensure_ascii=False, default=str)

    @staticmethod
    def _decode_report(raw: str | None) -> Any:
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None

    def upsert_task(self, task: dict[str, Any]) -> None:
        required = ("id", "task", "mode", "status", "created_at", "log_path")
        missing = [key for key in required if key not in task]
        if missing:
            raise ValueError(f"task metadata missing fields: {', '.join(missing)}")
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO tasks (
                    id, task, mode, status, created_at, finished_at, log_path, report_json,
                    resumed_from, draft_json, pending_json, session_json, before_json,
                    require_draft_review, explicit_skills_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    task=excluded.task,
                    mode=excluded.mode,
                    status=excluded.status,
                    created_at=excluded.created_at,
                    finished_at=excluded.finished_at,
                    log_path=excluded.log_path,
                    report_json=excluded.report_json,
                    resumed_from=excluded.resumed_from,
                    draft_json=excluded.draft_json,
                    pending_json=excluded.pending_json,
                    session_json=excluded.session_json,
                    before_json=excluded.before_json,
                    require_draft_review=excluded.require_draft_review,
                    explicit_skills_json=excluded.explicit_skills_json
                """,
                (
                    str(task["id"]),
                    str(task["task"]),
                    str(task["mode"]),
                    str(task["status"]),
                    str(task["created_at"]),
                    task.get("finished_at"),
                    str(task["log_path"]),
                    self._encode_report(task.get("report")),
                    task.get("resumed_from"),
                    self._encode_report(task.get("draft")),
                    self._encode_report(task.get("pending")),
                    self._encode_report(task.get("session")),
                    self._encode_report(task.get("before")),
                    1 if task.get("require_draft_review", False) else 0,
                    self._encode_report(task.get("explicit_skills")),
                ),
            )
            self._connection.commit()

    def _row_to_task(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["report"] = self._decode_report(item.pop("report_json", None))
        item["draft"] = self._decode_report(item.pop("draft_json", None))
        item["pending"] = self._decode_report(item.pop("pending_json", None))
        item["session"] = self._decode_report(item.pop("session_json", None))
        item["before"] = self._decode_report(item.pop("before_json", None)) or {}
        item["require_draft_review"] = bool(item.get("require_draft_review", 0))
        explicit_skills = self._decode_report(item.pop("explicit_skills_json", None))
        item["explicit_skills"] = (
            [str(value) for value in explicit_skills]
            if isinstance(explicit_skills, list)
            else []
        )
        return item

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT id, task, mode, status, created_at, finished_at, log_path, report_json, resumed_from, "
                "draft_json, pending_json, session_json, before_json, require_draft_review "
                ", explicit_skills_json "
                "FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        return self._row_to_task(row)

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, task, mode, status, created_at, finished_at, log_path, report_json, resumed_from, "
                "draft_json, pending_json, session_json, before_json, require_draft_review "
                ", explicit_skills_json "
                "FROM tasks ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [self._row_to_task(row) for row in rows if row is not None]

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
