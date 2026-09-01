from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path

from coding_agent.session_store import SessionStore


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "sessions.sqlite3"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_round_trip_task_metadata_and_report(self) -> None:
        store = SessionStore(self.db_path)
        self.addCleanup(store.close)
        store.upsert_task({
            "id": "t1",
            "task": "fix",
            "mode": "execute",
            "status": "complete",
            "created_at": "now",
            "finished_at": "later",
            "log_path": "/tmp/t1.jsonl",
            "report": {"status": "complete"},
        })

        self.assertEqual(store.get_task("t1")["report"]["status"], "complete")

    def test_list_tasks_is_newest_first(self) -> None:
        store = SessionStore(self.db_path)
        self.addCleanup(store.close)
        store.upsert_task({
            "id": "old",
            "task": "a",
            "mode": "execute",
            "status": "error",
            "created_at": "2026-08-28T00:00:00+00:00",
            "finished_at": None,
            "log_path": "/tmp/old.jsonl",
            "report": None,
        })
        store.upsert_task({
            "id": "new",
            "task": "b",
            "mode": "execute",
            "status": "running",
            "created_at": "2026-08-29T00:00:00+00:00",
            "finished_at": None,
            "log_path": "/tmp/new.jsonl",
            "report": None,
        })

        self.assertEqual([item["id"] for item in store.list_tasks()], ["new", "old"])

    def test_round_trips_paused_agent_state(self) -> None:
        store = SessionStore(self.db_path)
        self.addCleanup(store.close)
        draft = {"note.txt": {"before_exists": True, "before": "old", "content": "new"}}
        pending = {"name": "run_command", "arguments": {"command": "python3 -m unittest"}}
        snapshot = {
            "task": "修复测试",
            "paused_status": "awaiting_approval",
            "messages": [{"role": "user", "content": "修复测试"}],
            "pending_call": pending,
        }
        store.upsert_task({
            "id": "paused",
            "task": "修复测试",
            "mode": "execute",
            "status": "awaiting_approval",
            "created_at": "now",
            "finished_at": None,
            "log_path": "/tmp/paused.jsonl",
            "report": {"status": "awaiting_approval"},
            "draft": draft,
            "pending": pending,
            "session": snapshot,
        })

        restored = store.get_task("paused")

        self.assertEqual(restored["draft"], draft)
        self.assertEqual(restored["pending"], pending)
        self.assertEqual(restored["session"], snapshot)

    def test_round_trips_draft_review_requirement(self) -> None:
        store = SessionStore(self.db_path)
        self.addCleanup(store.close)
        store.upsert_task({
            "id": "review",
            "task": "审阅草稿",
            "mode": "execute",
            "status": "review_required",
            "created_at": "now",
            "finished_at": None,
            "log_path": "/tmp/review.jsonl",
            "report": {"status": "review_required"},
            "require_draft_review": True,
        })

        restored = store.get_task("review")

        self.assertIs(restored["require_draft_review"], True)

    def test_migrates_existing_database_without_draft_review_column(self) -> None:
        connection = sqlite3.connect(self.db_path)
        connection.execute(
            """
            CREATE TABLE tasks (
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
                before_json TEXT
            )
            """
        )
        connection.commit()
        connection.close()

        store = SessionStore(self.db_path)
        self.addCleanup(store.close)
        store.upsert_task({
            "id": "legacy",
            "task": "旧任务",
            "mode": "execute",
            "status": "complete",
            "created_at": "now",
            "finished_at": "later",
            "log_path": "/tmp/legacy.jsonl",
            "report": None,
        })

        restored = store.get_task("legacy")

        self.assertIs(restored["require_draft_review"], False)


if __name__ == "__main__":
    unittest.main()
