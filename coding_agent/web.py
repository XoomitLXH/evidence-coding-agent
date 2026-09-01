from __future__ import annotations

import argparse
import difflib
import hashlib
import mimetypes
import json
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .agent_loop import AgentLoop, Model
from .plugins import PluginManager
from .config import Config
from .draft import DraftChanges
from .model_client import OpenAICompatibleClient
from .policy import PolicyError, safe_path
from .runtime import execute_python
from .session_store import SessionStore
from .tools_files import list_dir, read_file
from .workspace import iter_files


STATIC_ROOT = Path(__file__).with_name("static")
MAX_SNAPSHOT_FILE_BYTES = 160_000
MAX_SNAPSHOT_TOTAL_BYTES = 1_500_000
MAX_DIFF_CHARS = 180_000
MAX_REFERENCE_FILES = 600
MAX_EDITOR_FILE_BYTES = 400_000
MAX_EDITOR_REQUEST_BYTES = MAX_EDITOR_FILE_BYTES * 2 + 10_000
FINAL_STATUSES = {
    "complete",
    "error",
    "incomplete",
    "interrupted",
    "awaiting_approval",
    "review_required",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text_snapshot(root: Path) -> dict[str, str]:
    """Capture bounded UTF-8 text needed to render a task's post-run diff."""
    files: dict[str, str] = {}
    total = 0
    for path in iter_files(root):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > MAX_SNAPSHOT_FILE_BYTES or total + size > MAX_SNAPSHOT_TOTAL_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "\x00" in text:
            continue
        files[str(path.relative_to(root))] = text
        total += size
    return files


def _editor_file(root: Path, path: str) -> dict[str, Any]:
    """Read an editable UTF-8 workspace file without the agent tool's line numbers."""
    file_path = safe_path(root, path)
    if not file_path.is_file():
        raise PolicyError(f"not a file: {path}")
    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PolicyError("file is not valid UTF-8 text") from exc
    if len(text.encode("utf-8")) > MAX_EDITOR_FILE_BYTES:
        raise PolicyError("file exceeds 400 KB editor limit")
    return {"path": str(file_path.relative_to(root)), "content": text, "bytes": len(text.encode("utf-8"))}


def _save_editor_file(root: Path, path: str, content: str) -> dict[str, Any]:
    file_path = safe_path(root, path)
    if not isinstance(content, str):
        raise PolicyError("content must be a string")
    size = len(content.encode("utf-8"))
    if size > MAX_EDITOR_FILE_BYTES:
        raise PolicyError("file content exceeds 400 KB editor limit")
    if file_path.exists() and not file_path.is_file():
        raise PolicyError(f"not a file: {path}")
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(file_path.relative_to(root)), "bytes": size}


@dataclass
class TaskRecord:
    task_id: str
    prompt: str
    mode: str
    before: dict[str, str]
    status: str = "queued"
    created_at: str = field(default_factory=_now)
    finished_at: str | None = None
    report: dict[str, Any] | None = None
    log_path: Path | None = None
    resumed_from: str | None = None
    explicit_skills: list[str] = field(default_factory=list)
    require_draft_review: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)
    loop: AgentLoop | None = None
    condition: threading.Condition = field(default_factory=threading.Condition, repr=False)


class TaskManager:
    def __init__(
        self,
        root: Path,
        *,
        config: Config | None = None,
        model_factory: Callable[[], Model] | None = None,
        log_root: Path | None = None,
        session_db: Path | None = None,
        plugin_dirs: list[Path] | None = None,
        explicit_skills: list[str] | None = None,
    ):
        self.root = root.resolve()
        self.config = config or Config.from_env()
        self.model_factory = model_factory
        self.plugin_dirs = [Path(item).expanduser() for item in (plugin_dirs or [])]
        self.explicit_skills = list(explicit_skills or [])
        self.plugin_manager = PluginManager(self.root, self.plugin_dirs)
        self.tasks: dict[str, TaskRecord] = {}
        self._lock = threading.Lock()
        self._worker_threads: set[threading.Thread] = set()
        self._closing = False
        self._closed = False
        if log_root is None:
            root_key = hashlib.sha256(str(self.root).encode("utf-8")).hexdigest()[:16]
            log_root = Path(tempfile.gettempdir()) / "evidence-coding-agent-web" / root_key
        self.log_root = log_root.expanduser().resolve()
        self.log_root.mkdir(parents=True, exist_ok=True)
        self.session_store = SessionStore(session_db or (self.log_root / "sessions.sqlite3"))
        self._restore_tasks()

    def _task_metadata(self, record: TaskRecord) -> dict[str, Any]:
        loop = record.loop
        return {
            "id": record.task_id,
            "task": record.prompt,
            "mode": record.mode,
            "status": record.status,
            "created_at": record.created_at,
            "finished_at": record.finished_at,
            "log_path": str(record.log_path or (self.log_root / f"{record.task_id}.jsonl")),
            "report": record.report,
            "resumed_from": record.resumed_from,
            "explicit_skills": record.explicit_skills,
            "require_draft_review": record.require_draft_review,
            "before": record.before,
            "draft": loop.registry.drafts.to_dict() if loop else {},
            "pending": loop.pending_call if loop else None,
            "session": loop.session_snapshot() if loop else None,
        }

    def _persist(self, record: TaskRecord) -> None:
        self.session_store.upsert_task(self._task_metadata(record))

    @staticmethod
    def _read_events(path: Path) -> list[dict[str, Any]]:
        if not path.is_file():
            return []
        events: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict) and event.get("type"):
                        events.append(event)
        except OSError:
            return []
        return events

    def _restore_tasks(self) -> None:
        """Rehydrate metadata and event history, marking abandoned runs interrupted."""
        restored: dict[str, TaskRecord] = {}
        for item in self.session_store.list_tasks():
            task_id = str(item["id"])
            log_path = Path(item["log_path"]).expanduser()
            if not log_path.is_absolute():
                log_path = self.log_root / log_path
            status = str(item.get("status") or "incomplete")
            report = item.get("report")
            if status in {"queued", "pending", "running"}:
                status = "interrupted"
                report = {
                    "status": "interrupted",
                    "summary": "服务重启时任务被中断，可点击恢复任务重新执行。",
                    "modified_files": [],
                }
            finished_at = item.get("finished_at")
            if status == "interrupted":
                finished_at = finished_at or _now()
            elif status in {"awaiting_approval", "review_required"}:
                finished_at = None
            record = TaskRecord(
                task_id=task_id,
                prompt=str(item.get("task") or ""),
                mode=str(item.get("mode") or "execute"),
                before=dict(item.get("before") or {}),
                status=status,
                created_at=str(item.get("created_at") or _now()),
                finished_at=finished_at,
                report=report,
                log_path=log_path,
                resumed_from=item.get("resumed_from"),
                explicit_skills=[str(value) for value in (item.get("explicit_skills") or [])],
                require_draft_review=bool(
                    item.get("require_draft_review", status == "review_required")
                ),
                events=self._read_events(log_path),
            )
            snapshot = item.get("session")
            if status in {"awaiting_approval", "review_required"} and isinstance(snapshot, dict):
                try:
                    model = self.model_factory() if self.model_factory else OpenAICompatibleClient(self.config)
                    loop = AgentLoop(
                        self.root,
                        model,
                        log_path=log_path,
                        max_steps=self.config.max_steps,
                        event_listener=lambda event, target=record: self._append_event(target, event),
                        plugin_dirs=self.plugin_dirs,
                        explicit_skills=record.explicit_skills,
                        require_draft_review=record.require_draft_review,
                    )
                    loop.restore_session(snapshot)
                    record.loop = loop
                except Exception as exc:
                    record.status = "error"
                    record.finished_at = _now()
                    record.report = {
                        "status": "error",
                        "summary": f"任务会话恢复失败：{exc}",
                        "modified_files": [],
                    }
            elif status in {"awaiting_approval", "review_required"}:
                # Older records may only have the standalone draft/pending columns.
                draft = item.get("draft") if isinstance(item.get("draft"), dict) else {}
                pending = item.get("pending") if isinstance(item.get("pending"), dict) else None
                if draft or pending:
                    try:
                        model = self.model_factory() if self.model_factory else OpenAICompatibleClient(self.config)
                        loop = AgentLoop(
                            self.root,
                            model,
                            log_path=log_path,
                            max_steps=self.config.max_steps,
                            event_listener=lambda event, target=record: self._append_event(target, event),
                            plugin_dirs=self.plugin_dirs,
                            explicit_skills=record.explicit_skills,
                            require_draft_review=record.require_draft_review,
                        )
                        loop.registry.drafts = DraftChanges.from_dict(self.root, draft)
                        loop.pending_call = pending
                        loop.paused_status = status
                        record.loop = loop
                    except Exception:
                        pass
            restored[task_id] = record
            if status == "interrupted" or record.status == "error":
                self.session_store.upsert_task(self._task_metadata(record))
        with self._lock:
            self.tasks.update(restored)

    def create_task(self, prompt: str, mode: str = "execute", *, resumed_from: str | None = None, explicit_skills: list[str] | None = None, require_draft_review: bool | None = None) -> TaskRecord:
        cleaned_prompt = prompt.strip()
        if not cleaned_prompt or len(cleaned_prompt) > 12_000:
            raise ValueError("task must contain 1 to 12,000 characters")
        if mode not in {"execute", "plan"}:
            raise ValueError("mode must be execute or plan")
        record = TaskRecord(
            task_id=secrets.token_urlsafe(9),
            prompt=cleaned_prompt,
            mode=mode,
            before=_text_snapshot(self.root),
            resumed_from=resumed_from,
            explicit_skills=list(explicit_skills or self.explicit_skills),
            require_draft_review=(self._prompt_requires_draft_review(cleaned_prompt) if require_draft_review is None else bool(require_draft_review)),
        )
        record.log_path = self.log_root / f"{record.task_id}.jsonl"
        worker = threading.Thread(target=self._run, args=(record,), daemon=True)
        with self._lock:
            if self._closing:
                raise ValueError("服务正在关闭，暂不能创建任务")
            self.tasks[record.task_id] = record
            self._persist(record)
            self._worker_threads.add(worker)
            worker.start()
        return record

    @staticmethod
    def _prompt_requires_draft_review(prompt: str) -> bool:
        lowered = prompt.lower()
        return any(marker in prompt for marker in ("审阅", "等待我审阅", "草稿审阅")) or "review draft" in lowered or "draft review" in lowered

    def _append_event(self, record: TaskRecord, event: dict[str, Any]) -> None:
        with record.condition:
            record.events.append(event)
            record.condition.notify_all()
        self._persist(record)

    def resume_task(self, task_id: str) -> TaskRecord:
        record = self.get_task(task_id)
        if not record:
            raise KeyError("task not found")
        with self._lock:
            if self._closing:
                raise ValueError("服务正在关闭，暂不能恢复任务")
        with record.condition:
            if record.status not in {"interrupted", "error", "incomplete"}:
                raise ValueError("只有中断或未完成的任务可以恢复")
        resumed = self.create_task(
            record.prompt,
            record.mode,
            resumed_from=record.task_id,
            explicit_skills=record.explicit_skills,
            require_draft_review=record.require_draft_review,
        )
        return resumed

    def _resume_paused_task(
        self,
        task_id: str,
        continuation: Callable[[AgentLoop], dict[str, Any]],
        expected_status: str,
    ) -> TaskRecord:
        record = self.get_task(task_id)
        if not record:
            raise KeyError("task not found")
        with self._lock:
            if self._closing:
                raise ValueError("服务正在关闭，暂不能恢复任务")
            with record.condition:
                if record.status != expected_status or record.loop is None:
                    raise ValueError("任务当前没有可恢复的暂停操作")
                record.status = "running"
                record.finished_at = None
                record.report = None
                record.condition.notify_all()
            worker = threading.Thread(
                target=self._run_with_continuation,
                args=(record, continuation),
                daemon=True,
            )
            self._worker_threads.add(worker)
            self._persist(record)
            worker.start()
        return record

    def resume_after_approval(self, task_id: str, approved: bool) -> TaskRecord:
        if not isinstance(approved, bool):
            raise ValueError("approved must be a boolean")
        return self._resume_paused_task(
            task_id,
            lambda loop: loop.resume_after_approval(approved=approved),
            "awaiting_approval",
        )

    def resume_after_review(self, task_id: str, accepted: bool) -> TaskRecord:
        if not isinstance(accepted, bool):
            raise ValueError("accepted must be a boolean")
        return self._resume_paused_task(
            task_id,
            lambda loop: loop.resume_after_review(accepted=accepted),
            "review_required",
        )

    def _run(self, record: TaskRecord) -> None:
        self._run_with_continuation(record, None)

    def _run_with_continuation(
        self,
        record: TaskRecord,
        continuation: Callable[[AgentLoop], dict[str, Any]] | None,
    ) -> None:
        try:
            with record.condition:
                record.status = "running"
                record.condition.notify_all()
            self._persist(record)
            loop = record.loop
            if loop is None:
                model = self.model_factory() if self.model_factory else OpenAICompatibleClient(self.config)
                loop = AgentLoop(
                    self.root,
                    model,
                    log_path=record.log_path or self.log_root / f"{record.task_id}.jsonl",
                    max_steps=self.config.max_steps,
                    event_listener=lambda event: self._append_event(record, event),
                    plugin_dirs=self.plugin_dirs,
                    explicit_skills=record.explicit_skills,
                    require_draft_review=record.require_draft_review,
                )
                record.loop = loop
            if continuation is not None:
                report = continuation(loop)
            else:
                prompt = record.prompt
                if record.mode == "plan":
                    prompt += "\n\nStart with repository inspection and state a concise implementation plan before making edits."
                report = loop.run(prompt)
        except Exception as exc:
            report = {"status": "error", "summary": f"任务执行失败：{exc}", "modified_files": []}
        finally:
            with record.condition:
                record.report = report if "report" in locals() else {
                    "status": "error",
                    "summary": "任务执行在初始化时中断。",
                    "modified_files": [],
                }
                record.status = record.report["status"]
                record.finished_at = None if record.status in {"awaiting_approval", "review_required"} else _now()
                record.condition.notify_all()
            try:
                self._persist(record)
            finally:
                with self._lock:
                    self._worker_threads.discard(threading.current_thread())
                    should_close = self._closing and not self._worker_threads and not self._closed
                    if should_close:
                        self._closed = True
                if should_close:
                    self.session_store.close()

    def close(self, timeout: float = 5.0) -> None:
        """Wait for active workers before closing durable session storage."""
        with self._lock:
            self._closing = True
        deadline = time.monotonic() + timeout
        current = threading.current_thread()
        while True:
            with self._lock:
                workers = [worker for worker in self._worker_threads if worker is not current]
            if not workers:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            for worker in workers:
                worker.join(timeout=min(remaining, 0.2))
        with self._lock:
            if self._worker_threads or self._closed:
                return
            self._closed = True
        self.session_store.close()

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self.tasks.get(task_id)

    def reference_paths(self) -> list[str]:
        paths = sorted(
            (str(path.relative_to(self.root)) for path in iter_files(self.root)),
            key=str.lower,
        )
        return paths[:MAX_REFERENCE_FILES]

    @staticmethod
    def serialize(record: TaskRecord) -> dict[str, Any]:
        with record.condition:
            loop = record.loop
            return {
                "id": record.task_id,
                "task": record.prompt,
                "mode": record.mode,
                "status": record.status,
                "created_at": record.created_at,
                "finished_at": record.finished_at,
                "report": record.report,
                "resumed_from": record.resumed_from,
                "explicit_skills": record.explicit_skills,
                "require_draft_review": record.require_draft_review,
                "event_count": len(record.events),
                "log_path": str(record.log_path) if record.log_path else None,
                "draft": loop.registry.drafts.to_dict() if loop else {},
                "pending": loop.pending_call if loop else None,
                "session": loop.session_snapshot() if loop else None,
            }

    def list_serialized(self) -> list[dict[str, Any]]:
        with self._lock:
            records = list(self.tasks.values())
        records.sort(key=lambda item: (item.created_at, item.task_id), reverse=True)
        return [self.serialize(record) for record in records]

    def wait_for_events(self, record: TaskRecord, after: int, timeout: float) -> tuple[list[dict[str, Any]], bool]:
        with record.condition:
            if after >= len(record.events) and record.status not in FINAL_STATUSES:
                record.condition.wait(timeout=timeout)
            return record.events[after:], record.status in FINAL_STATUSES

    def diff(self, task_id: str) -> dict[str, Any]:
        record = self.get_task(task_id)
        if not record:
            raise KeyError("task not found")
        with record.condition:
            report = record.report or {}
            before = dict(record.before)
            draft_entries = record.loop.registry.drafts.diff() if record.loop else []
            draft_paths = [str(entry["path"]) for entry in draft_entries]
            paths = draft_paths or list(report.get("modified_files") or [])
            draft_by_path = {str(entry["path"]): entry for entry in draft_entries}
        after = _text_snapshot(self.root)
        chunks: list[str] = []
        for path in paths:
            draft = draft_by_path.get(path)
            if draft is not None:
                chunks.extend(str(draft.get("diff") or ""))
                continue
            old = before.get(path, "")
            new = after.get(path, "")
            if old == new:
                continue
            chunks.extend(
                difflib.unified_diff(
                    old.splitlines(keepends=True),
                    new.splitlines(keepends=True),
                    fromfile=f"a/{path}",
                    tofile=f"b/{path}",
                )
            )
        rendered = "".join(chunks)
        truncated = len(rendered) > MAX_DIFF_CHARS
        return {"task_id": task_id, "files": paths, "diff": rendered[:MAX_DIFF_CHARS], "truncated": truncated}


class CodingAgentHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def manager(self) -> TaskManager:
        return self.server.manager  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_error_json(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message}, status)

    def _body(self, *, max_bytes: int = 30_000) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if length > max_bytes:
            raise ValueError("request body is too large")
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("request body must be JSON") from exc
        if not isinstance(body, dict):
            raise ValueError("request body must be a JSON object")
        return body

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/api/bootstrap":
            self._send_json({
                "workspace": str(self.manager.root),
                "tree": list_dir(self.manager.root)["entries"],
                "plugins": self.manager.plugin_manager.metadata,
                "model": {
                    "name": self.manager.config.model,
                    "base_url": self.manager.config.base_url,
                    "model_ready": bool(self.manager.config.api_key),
                },
            })
            return
        if parsed.path == "/api/plugins":
            self._send_json(self.manager.plugin_manager.metadata)
            return
        if parsed.path == "/api/plugin-icon":
            plugin_name = query.get("plugin", [""])[0]
            icon_path = self.manager.plugin_manager.icon_path(plugin_name)
            if icon_path is None:
                self._send_error_json(HTTPStatus.NOT_FOUND, "plugin icon not found")
                return
            try:
                data = icon_path.read_bytes()
            except OSError:
                self._send_error_json(HTTPStatus.NOT_FOUND, "plugin icon not found")
                return
            content_type = mimetypes.guess_type(icon_path.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/api/tree":
            path = query.get("path", ["."])[0]
            try:
                self._send_json(list_dir(self.manager.root, path))
            except PolicyError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if parsed.path == "/api/references":
            self._send_json({"files": self.manager.reference_paths()})
            return
        if parsed.path == "/api/tasks":
            self._send_json({"tasks": self.manager.list_serialized()})
            return
        if parsed.path == "/api/file":
            path = query.get("path", [""])[0]
            try:
                if query.get("raw", [""])[0] == "1":
                    self._send_json(_editor_file(self.manager.root, path))
                else:
                    self._send_json(read_file(self.manager.root, path))
            except PolicyError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if parsed.path.startswith("/api/tasks/") and parsed.path.endswith("/draft"):
            task_id = parsed.path.split("/")[3]
            try:
                self._send_json(self.manager.diff(task_id))
            except KeyError:
                self._send_error_json(HTTPStatus.NOT_FOUND, "task not found")
            return
        if parsed.path.startswith("/api/tasks/") and parsed.path.endswith("/events"):
            self._stream_events(parsed.path.split("/")[3], query)
            return
        if parsed.path.startswith("/api/tasks/"):
            record = self.manager.get_task(parsed.path.rsplit("/", 1)[-1])
            if not record:
                self._send_error_json(HTTPStatus.NOT_FOUND, "task not found")
            else:
                self._send_json(self.manager.serialize(record))
            return
        if parsed.path == "/api/diff":
            task_id = query.get("task_id", [""])[0]
            try:
                self._send_json(self.manager.diff(task_id))
            except KeyError:
                self._send_error_json(HTTPStatus.NOT_FOUND, "task not found")
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        if route in {"/api/run", "/api/debug"}:
            self._execute_file(route)
            return
        if route.startswith("/api/tasks/") and route.endswith("/resume"):
            task_id = route.split("/")[3]
            try:
                record = self.manager.resume_task(task_id)
            except KeyError:
                self._send_error_json(HTTPStatus.NOT_FOUND, "task not found")
                return
            except ValueError as exc:
                self._send_error_json(HTTPStatus.CONFLICT, str(exc))
                return
            self._send_json(self.manager.serialize(record), HTTPStatus.ACCEPTED)
            return
        if route.startswith("/api/tasks/") and route.endswith("/approval"):
            task_id = route.split("/")[3]
            try:
                payload = self._body(max_bytes=8_000)
                approved = payload.get("approved")
                if not isinstance(approved, bool):
                    raise ValueError("approved must be a boolean")
                record = self.manager.resume_after_approval(task_id, approved)
            except KeyError:
                self._send_error_json(HTTPStatus.NOT_FOUND, "task not found")
                return
            except ValueError as exc:
                message = str(exc)
                status = HTTPStatus.BAD_REQUEST if "must be a boolean" in message else HTTPStatus.CONFLICT
                self._send_error_json(status, message)
                return
            self._send_json(self.manager.serialize(record), HTTPStatus.ACCEPTED)
            return
        if route.startswith("/api/tasks/") and route.endswith("/review"):
            task_id = route.split("/")[3]
            try:
                payload = self._body(max_bytes=8_000)
                accepted = payload.get("accepted")
                if not isinstance(accepted, bool):
                    raise ValueError("accepted must be a boolean")
                record = self.manager.resume_after_review(task_id, accepted)
            except KeyError:
                self._send_error_json(HTTPStatus.NOT_FOUND, "task not found")
                return
            except ValueError as exc:
                message = str(exc)
                status = HTTPStatus.BAD_REQUEST if "must be a boolean" in message else HTTPStatus.CONFLICT
                self._send_error_json(status, message)
                return
            self._send_json(self.manager.serialize(record), HTTPStatus.ACCEPTED)
            return
        if route != "/api/tasks":
            self._send_error_json(HTTPStatus.NOT_FOUND, "route not found")
            return
        try:
            payload = self._body()
            skills = payload.get("skills")
            if isinstance(skills, str):
                skills = [skills]
            if skills is not None and (not isinstance(skills, list) or not all(isinstance(item, str) for item in skills)):
                raise ValueError("skills must be a string or array of strings")
            record = self.manager.create_task(
                str(payload.get("task", "")),
                str(payload.get("mode", "execute")),
                explicit_skills=skills,
            )
        except ValueError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        self._send_json(self.manager.serialize(record), HTTPStatus.ACCEPTED)

    def _execute_file(self, route: str) -> None:
        """Save the editor draft, then execute the selected Python file."""
        try:
            payload = self._body(max_bytes=MAX_EDITOR_REQUEST_BYTES)
            path = payload.get("path")
            if not isinstance(path, str):
                raise ValueError("path must be a string")
            if "content" in payload:
                content = payload.get("content")
                if not isinstance(content, str):
                    raise ValueError("content must be a string")
                _save_editor_file(self.manager.root, path, content)
            result = execute_python(
                self.manager.root,
                path,
                mode="debug" if route == "/api/debug" else "run",
                timeout_seconds=payload.get("timeout_seconds"),
            )
        except (PolicyError, ValueError) as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        response = result.as_dict()
        if result.timed_out:
            response["error"] = result.error or "执行超时"
            self._send_json(response, HTTPStatus.REQUEST_TIMEOUT)
            return
        self._send_json(response)

    def do_PUT(self) -> None:
        if urlparse(self.path).path != "/api/file":
            self._send_error_json(HTTPStatus.NOT_FOUND, "route not found")
            return
        try:
            payload = self._body(max_bytes=MAX_EDITOR_REQUEST_BYTES)
            path = payload.get("path")
            content = payload.get("content")
            if not isinstance(path, str):
                raise ValueError("path must be a string")
            self._send_json(_save_editor_file(self.manager.root, path, content))
        except (PolicyError, ValueError) as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))

    def _stream_events(self, task_id: str, query: dict[str, list[str]]) -> None:
        record = self.manager.get_task(task_id)
        if not record:
            self._send_error_json(HTTPStatus.NOT_FOUND, "task not found")
            return
        # `after` is kept for existing clients; browser EventSource reconnects
        # with Last-Event-ID, which must be honored when no explicit cursor is
        # supplied in the URL.
        raw_cursor = query.get("after", [None])[0]
        if raw_cursor is None:
            raw_cursor = self.headers.get("Last-Event-ID", "0")
        try:
            cursor = max(0, int(raw_cursor or "0"))
        except ValueError:
            self._send_error_json(HTTPStatus.BAD_REQUEST, "event cursor must be an integer")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            events, finished = self.manager.wait_for_events(record, cursor, timeout=0.5)
            for event in events:
                cursor += 1
                frame = f"id: {cursor}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
                try:
                    self.wfile.write(frame.encode("utf-8"))
                except (BrokenPipeError, ConnectionResetError):
                    return
            if events:
                try:
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
            if finished:
                try:
                    self.wfile.write(b"event: end\ndata: {}\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                return
            try:
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    def _serve_static(self, path: str) -> None:
        asset = {"/": "index.html", "/styles.css": "styles.css", "/app.js": "app.js"}.get(path)
        if not asset:
            self._send_error_json(HTTPStatus.NOT_FOUND, "route not found")
            return
        file_path = STATIC_ROOT / asset
        if not file_path.is_file():
            self._send_error_json(HTTPStatus.NOT_FOUND, "static asset not found")
            return
        data = file_path.read_bytes()
        content_type = {"index.html": "text/html; charset=utf-8", "styles.css": "text/css; charset=utf-8", "app.js": "application/javascript; charset=utf-8"}[asset]
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def create_server(
    root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 50516,
    config: Config | None = None,
    model_factory: Callable[[], Model] | None = None,
    log_root: Path | None = None,
    session_db: Path | None = None,
    plugin_dirs: list[Path] | None = None,
    explicit_skills: list[str] | None = None,
) -> ThreadingHTTPServer:
    workspace = root.expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace does not exist: {workspace}")
    class CodingAgentServer(ThreadingHTTPServer):
        def server_close(self) -> None:
            manager = getattr(self, "manager", None)
            if manager is not None:
                manager.close()
            super().server_close()

    server = CodingAgentServer((host, port), CodingAgentHandler)
    server.manager = TaskManager(
        workspace,
        config=config,
        model_factory=model_factory,
        log_root=log_root,
        session_db=session_db,
        plugin_dirs=plugin_dirs,
        explicit_skills=explicit_skills,
    )  # type: ignore[attr-defined]
    return server


def main() -> int:
    parser = argparse.ArgumentParser(description="Evidence Coding Agent web interface")
    parser.add_argument("--repo", default=".", help="workspace the agent may inspect and edit")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50516)
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument(
        "--plugin-dir",
        action="append",
        default=[],
        metavar="PATH",
        help="additional plugin directory or plugin root (repeatable)",
    )
    parser.add_argument(
        "--skills",
        action="append",
        default=[],
        metavar="NAME[,NAME]",
        help="skills enabled for new tasks (repeatable or comma-separated)",
    )
    args = parser.parse_args()
    config = Config.from_env(model=args.model, base_url=args.base_url, max_steps=args.max_steps)
    skills: list[str] = []
    for value in args.skills:
        skills.extend(item.strip() for item in value.split(",") if item.strip())
    server = create_server(
        Path(args.repo),
        host=args.host,
        port=args.port,
        config=config,
        plugin_dirs=[Path(item).expanduser() for item in args.plugin_dir],
        explicit_skills=skills,
    )
    print(f"Evidence Coding Agent is listening at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
