# Session Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist web tasks and their event history so the coding agent can list, inspect, and replay tasks after a server restart, while marking interrupted runs honestly.

**Architecture:** Keep the existing per-task JSONL event log as the append-only event source and add a small SQLite index for task metadata and report snapshots. `TaskManager` writes metadata transitions transactionally, loads records at startup, replays JSONL events into each record, and exposes a resume endpoint that starts a new run from the persisted prompt and last report context.

**Tech Stack:** Python 3 standard library (`sqlite3`, `json`, `threading`), existing `EventLog`, `TaskManager`, `unittest`, HTTP JSON/SSE endpoints.

---

### Task 1: Define durable store and recovery contract

**Files:**
- Create: `coding_agent/session_store.py`
- Test: `tests/test_session_store.py`

- [ ] **Step 1: Write the failing tests**

```python
class SessionStoreTests(unittest.TestCase):
    def test_round_trip_task_metadata_and_report(self):
        store = SessionStore(self.db_path)
        store.upsert_task({"id": "t1", "task": "fix", "mode": "execute", "status": "complete", "created_at": "now", "finished_at": "later", "log_path": "/tmp/t1.jsonl", "report": {"status": "complete"}})
        self.assertEqual(store.get_task("t1")["report"]["status"], "complete")

    def test_list_tasks_is_newest_first(self):
        store = SessionStore(self.db_path)
        store.upsert_task({"id": "old", "task": "a", "mode": "execute", "status": "error", "created_at": "2026-08-28T00:00:00+00:00", "finished_at": None, "log_path": "/tmp/old.jsonl", "report": None})
        store.upsert_task({"id": "new", "task": "b", "mode": "execute", "status": "running", "created_at": "2026-08-29T00:00:00+00:00", "finished_at": None, "log_path": "/tmp/new.jsonl", "report": None})
        self.assertEqual([item["id"] for item in store.list_tasks()], ["new", "old"])
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `python3 -m unittest tests.test_session_store -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'coding_agent.session_store'`.

- [ ] **Step 3: Implement the minimal SQLite store**

Create `SessionStore` with a connection opened using `check_same_thread=False`, a lock, and this schema:

```sql
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, task TEXT NOT NULL, mode TEXT NOT NULL,
  status TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT,
  log_path TEXT NOT NULL, report_json TEXT
)
```

Implement `upsert_task(payload)`, `get_task(task_id)`, and `list_tasks()`; serialize `report` with `json.dumps(..., ensure_ascii=False)` and deserialize `report_json` back to a dict.

- [ ] **Step 4: Run the focused test and verify it passes**

Run: `python3 -m unittest tests.test_session_store -v`

Expected: 2 tests PASS.

### Task 2: Persist TaskManager transitions and restore history

**Files:**
- Modify: `coding_agent/web.py` (`TaskRecord`, `TaskManager.__init__`, `create_task`, `_append_event`, `_run`)
- Test: `tests/test_web.py`

- [ ] **Step 1: Add failing recovery tests**

Add tests that create a manager with a temporary `session_db`, create a task with a fake model, wait for completion, construct a second manager using the same database, and assert that the task status/report/event count are restored. Add a fixture with a JSONL task marked `running` and assert a new manager exposes it as `interrupted`.

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `python3 -m unittest tests.test_web.TaskManagerPersistenceTests -v`

Expected: FAIL because `TaskManager` does not accept a session database and does not load records.

- [ ] **Step 3: Implement persistence and replay**

Extend `TaskRecord` with `log_path`. Add an optional `session_db: Path | None` constructor argument; default to `<log_root>/sessions.sqlite3`. On initialization, load rows from `SessionStore`, create records without starting threads, read each JSONL file into `record.events`, and convert persisted `running`/`pending` statuses to `interrupted` with a report explaining the process ended during restart. Persist every status change, report, and event count through a helper `_persist(record)`.

`_append_event` must append to the in-memory list and call `_persist`; `_run` must persist `running` before invoking `AgentLoop`, then persist the final report/status in both success and exception paths.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run: `python3 -m unittest tests.test_web.TaskManagerPersistenceTests -v`

Expected: all persistence tests PASS and existing web tests remain green.

### Task 3: Replay history through SSE and add resume endpoint

**Files:**
- Modify: `coding_agent/web.py`, `coding_agent/static/app.js`
- Test: `tests/test_web.py`

- [ ] **Step 1: Add failing endpoint tests**

Test `GET /api/tasks` after manager restart includes the restored task, `GET /api/tasks/<id>/events?cursor=0` returns all historical `event:` frames, and `POST /api/tasks/<id>/resume` creates a new task linked to the original prompt.

- [ ] **Step 2: Implement the endpoints**

Add `GET /api/tasks` serialization for restored records, `GET /api/tasks/<id>/events` using the existing cursor logic, and `POST /api/tasks/<id>/resume` that rejects an active task and calls `create_task(record.prompt, record.mode)`. Include `resumed_from` in the new task payload and event log.

- [ ] **Step 3: Update the UI history behavior**

When opening a task, initialize the stream cursor at `0` so the browser renders persisted events before waiting for live events. Add a compact “恢复任务” action for `interrupted`, `error`, and `incomplete` tasks; after success, switch the active task to the returned new id.

- [ ] **Step 4: Run all tests and syntax checks**

Run: `python3 -m unittest discover -v`

Run: `node --check coding_agent/static/app.js`

Expected: all Python tests PASS and JavaScript syntax check exits 0.

### Self-review checklist

- JSONL remains append-only and is sufficient to replay model/tool events.
- SQLite stores metadata only; no API keys or editor contents are persisted.
- Restart never reports an unfinished model call as `complete`.
- Resume creates a distinct task id and leaves the original immutable.
- No step requires a git commit because the user explicitly asked not to submit yet.
