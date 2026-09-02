from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any


def tool_response(name: str, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ],
                }
            }
        ]
    }


class ScriptedModel:
    def __init__(self) -> None:
        self.responses = [
            tool_response(
                "apply_patch",
                {"path": "note.txt", "old_text": "draft", "new_text": "final"},
                "call-1",
            ),
            tool_response("run_command", {"command": "python3 -m compileall -q ."}, "call-2"),
            tool_response("finish", {"summary": "Updated note.txt and verified the workspace."}, "call-3"),
        ]

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        return self.responses.pop(0)


class FailingModel:
    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        raise RuntimeError("连接被拒绝")


class BlockingModel:
    started = threading.Event()
    release = threading.Event()

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        type(self).started.set()
        if not type(self).release.wait(timeout=5):
            raise RuntimeError("测试模型等待超时")
        return tool_response("finish", {"summary": "关闭竞态测试完成"}, "blocking-finish")


class SafeCommandThenFinishModel:
    """A restart-safe model that finishes after a non-deleting command runs."""

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        if not any(message.get("role") == "tool" for message in messages):
            return tool_response("run_command", {"command": "/bin/echo allowed-without-approval"}, "safe-command")
        return tool_response("finish", {"summary": "非删除命令已执行。"}, "safe-finish")


class DraftReviewModel:
    """A restart-safe model whose finish request requires draft review."""

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        tool_names = [str(message.get("name") or "") for message in messages if message.get("role") == "tool"]
        if "apply_patch" not in tool_names:
            return tool_response(
                "apply_patch",
                {"path": "note.txt", "old_text": "draft", "new_text": "final"},
                "draft-patch",
            )
        if "run_command" not in tool_names:
            return tool_response(
                "run_command",
                {"command": "python3 -m compileall -q ."},
                "draft-verify",
            )
        return tool_response("finish", {"summary": "草稿审阅完成。"}, "draft-finish")


class DraftCreatedFileModel:
    """A restart-safe model that creates a new file kept behind draft review."""

    content = "def oranges_rotting(grid: list[list[int]]) -> int:\n    return 0\n"

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        tool_names = [str(message.get("name") or "") for message in messages if message.get("role") == "tool"]
        if "write_file" not in tool_names:
            return tool_response(
                "write_file",
                {"path": "rotting_oranges.py", "content": self.content},
                "draft-write",
            )
        if "run_command" not in tool_names:
            return tool_response(
                "run_command",
                {"command": "python3 -m compileall -q ."},
                "draft-created-verify",
            )
        return tool_response("finish", {"summary": "新建草稿等待审阅。"}, "draft-created-finish")


class PolicyClarificationModel:
    """A restart-safe model that submits an invalid command once, then replans."""

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        if not any(message.get("role") == "tool" for message in messages):
            return tool_response(
                "run_command",
                {"command": ""},
                "invalid-command",
            )
        return tool_response("finish", {"summary": "已按允许范围重新规划完成。"}, "clarified-finish")


class DeletionApprovalModel:
    """Requests one destructive command, then finishes after approval."""

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        if not any(message.get("role") == "tool" for message in messages):
            return tool_response(
                "run_command",
                {"command": "rm -f approved-delete.txt"},
                "delete-command",
            )
        return tool_response("finish", {"summary": "已按允许执行删除并完成任务。"}, "delete-finish")


class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        (self.workspace / "note.txt").write_text("draft\n", encoding="utf-8")
        (self.workspace / "src").mkdir()
        (self.workspace / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
        (self.workspace / ".git").mkdir()
        (self.workspace / ".git" / "config").write_text("[core]\n", encoding="utf-8")

        from coding_agent.web import create_server

        self.server = create_server(
            self.workspace,
            host="127.0.0.1",
            port=0,
            model_factory=ScriptedModel,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def request_json(self, path: str, *, method: str = "GET", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    def wait_for_finished_task(self, task_id: str) -> dict[str, Any]:
        for _ in range(40):
            task = self.request_json(f"/api/tasks/{task_id}")
            if task["status"] in {"complete", "error", "incomplete"}:
                return task
            time.sleep(0.025)
        self.fail("background task did not finish")

    def wait_for_task_status(self, task_id: str, *statuses: str) -> dict[str, Any]:
        for _ in range(80):
            task = self.request_json(f"/api/tasks/{task_id}")
            if task["status"] in statuses:
                return task
            time.sleep(0.025)
        self.fail(f"background task did not reach any expected status: {statuses}")

    def replace_server(self, model_factory: type[Any], storage: Path) -> None:
        from coding_agent.web import create_server

        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.server = create_server(
            self.workspace,
            host="127.0.0.1",
            port=0,
            model_factory=model_factory,
            log_root=storage / "logs",
            session_db=storage / "sessions.sqlite3",
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base_url = f"http://{host}:{port}"

    def test_bootstrap_exposes_workspace_tree_without_secrets(self) -> None:
        payload = self.request_json("/api/bootstrap")

        self.assertEqual(payload["workspace"], str(self.workspace.resolve()))
        self.assertIn({"name": "note.txt", "type": "file"}, payload["tree"])
        self.assertIn("model_ready", payload["model"])
        self.assertNotIn("api_key", json.dumps(payload).lower())

    def test_plugin_icon_endpoint_serves_declared_local_asset(self) -> None:
        from coding_agent.web import create_server

        plugin_root = self.workspace / "plugin-root"
        plugin = plugin_root / "quality"
        (plugin / ".codex-plugin").mkdir(parents=True)
        (plugin / "assets").mkdir()
        (plugin / "assets" / "icon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
        (plugin / ".codex-plugin" / "plugin.json").write_text(
            json.dumps({"name": "quality", "interface": {"composerIcon": "assets/icon.svg"}}),
            encoding="utf-8",
        )
        server = create_server(
            self.workspace,
            host="127.0.0.1",
            port=0,
            model_factory=ScriptedModel,
            plugin_dirs=[plugin_root],
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        try:
            request = urllib.request.Request(f"http://{host}:{port}/api/plugin-icon?plugin=quality")
            with urllib.request.urlopen(request, timeout=3) as response:
                body = response.read()
                content_type = response.headers.get_content_type()
            self.assertEqual(content_type, "image/svg+xml")
            self.assertIn(b"<svg", body)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_reference_index_lists_nested_workspace_files(self) -> None:
        payload = self.request_json("/api/references")

        self.assertEqual(payload["files"], ["note.txt", "src/main.py"])
        self.assertNotIn(".git/config", payload["files"])

    def test_editor_can_save_and_read_raw_workspace_file_content(self) -> None:
        saved = self.request_json(
            "/api/file",
            method="PUT",
            payload={"path": "src/main.py", "content": "print('updated')\n"},
        )
        opened = self.request_json("/api/file?path=src%2Fmain.py&raw=1")

        self.assertEqual(saved["path"], "src/main.py")
        self.assertEqual(saved["bytes"], len("print('updated')\n".encode("utf-8")))
        self.assertEqual(opened["content"], "print('updated')\n")
        self.assertEqual((self.workspace / "src" / "main.py").read_text(encoding="utf-8"), "print('updated')\n")

    def test_editor_save_rejects_paths_outside_the_workspace(self) -> None:
        request = urllib.request.Request(
            f"{self.base_url}/api/file",
            data=json.dumps({"path": "../outside.txt", "content": "nope"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT",
        )

        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=3)

        self.assertEqual(raised.exception.code, 400)
        payload = json.loads(raised.exception.read().decode("utf-8"))
        raised.exception.close()
        self.assertIn("escapes the workspace", payload["error"])

    def test_editor_can_save_a_file_larger_than_a_task_request(self) -> None:
        content = "x" * 35_000

        saved = self.request_json(
            "/api/file",
            method="PUT",
            payload={"path": "note.txt", "content": content},
        )

        self.assertEqual(saved["bytes"], len(content.encode("utf-8")))
        self.assertEqual((self.workspace / "note.txt").read_text(encoding="utf-8"), content)

    def test_editor_can_open_a_new_draft_file_for_its_task(self) -> None:
        with TemporaryDirectory() as storage_dir:
            storage = Path(storage_dir)
            self.replace_server(DraftCreatedFileModel, storage)
            created = self.request_json(
                "/api/tasks",
                method="POST",
                payload={"task": "生成 rotting_oranges.py 并等待我审阅", "mode": "execute"},
            )
            self.wait_for_task_status(created["id"], "review_required")

            self.replace_server(DraftCreatedFileModel, storage)
            self.wait_for_task_status(created["id"], "review_required")

            opened = self.request_json(
                f"/api/file?path=rotting_oranges.py&raw=1&task_id={created['id']}"
            )

            self.assertEqual(opened["content"], DraftCreatedFileModel.content)
            self.assertTrue(opened["draft"])
            self.assertFalse((self.workspace / "rotting_oranges.py").exists())

    def test_background_task_publishes_events_and_a_bounded_diff(self) -> None:
        created = self.request_json(
            "/api/tasks",
            method="POST",
            payload={"task": "Change the note and verify it.", "mode": "execute"},
        )
        task = self.wait_for_finished_task(created["id"])
        diff = self.request_json(f"/api/diff?task_id={created['id']}")

        self.assertEqual(task["status"], "complete")
        self.assertEqual(task["report"]["modified_files"], ["note.txt"])
        self.assertEqual(diff["files"], ["note.txt"])
        self.assertIn("-draft", diff["diff"])
        self.assertIn("+final", diff["diff"])

        with urllib.request.urlopen(f"{self.base_url}/api/tasks/{created['id']}/events", timeout=3) as response:
            events = response.read().decode("utf-8")
        self.assertIn("event: run_finished", events)
        self.assertIn("tool_result", events)

    def test_background_task_failure_still_has_a_chinese_final_summary(self) -> None:
        from coding_agent.web import create_server

        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.server = create_server(
            self.workspace,
            host="127.0.0.1",
            port=0,
            model_factory=FailingModel,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.base_url = f"http://{host}:{port}"

        created = self.request_json(
            "/api/tasks",
            method="POST",
            payload={"task": "测试连接失败", "mode": "execute"},
        )
        task = self.wait_for_finished_task(created["id"])

        self.assertEqual(task["status"], "error")
        self.assertIn("任务执行失败", task["report"]["summary"])
        self.assertIn("连接被拒绝", task["report"]["summary"])

    def test_application_shell_serves_the_conversation_first_ui(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/", timeout=3) as response:
            document = response.read().decode("utf-8")
        with urllib.request.urlopen(f"{self.base_url}/app.js", timeout=3) as response:
            script = response.read().decode("utf-8")
        with urllib.request.urlopen(f"{self.base_url}/styles.css", timeout=3) as response:
            stylesheet = response.read().decode("utf-8")

        self.assertIn('id="task-input"', document)
        self.assertIn('id="conversation"', document)
        self.assertIn('id="inspector"', document)
        self.assertIn('id="editor-highlight"', document)
        self.assertIn("EventSource", script)
        self.assertIn("fetchJson(\"/api/references\")", script)
        self.assertIn("row.after(children)", script)
        self.assertIn("message.lastEventId", script)
        self.assertIn("startEventStream(taskId, activityLine, streamState.cursor, streamState)", script)
        self.assertIn("model_delta", script)
        self.assertIn("model_message_end", script)
        self.assertIn("turns: new Map()", script)
        self.assertIn("finalSummaryAppended", script)
        self.assertIn("model_error", script)
        self.assertIn("任务执行失败", script)
        self.assertIn("setActivity", script)
        self.assertIn("ensureActivityLine", script)
        self.assertIn("result.failure?.reason", script)
        self.assertIn("result.failure?.recovery", script)
        self.assertIn("renderEditorHighlight", script)
        self.assertIn("requestAnimationFrame", script)
        self.assertIn('id="inspector-resizer"', document)
        self.assertIn(
            "grid-template-columns: 244px minmax(0, 1fr) minmax(520px, 620px)",
            stylesheet,
        )
        self.assertIn(".editor-stage { position: relative; min-height: 0; overflow: auto;", stylesheet)
        self.assertIn(".activity-line", stylesheet)
        self.assertIn(".syntax-keyword", stylesheet)
        self.assertIn(".syntax-type", stylesheet)
        self.assertNotIn(".reasoning-block", stylesheet)
        self.assertNotIn(".stream-status", stylesheet)
        self.assertNotIn(".timeline-row", stylesheet)
        self.assertNotIn("reasoningBlock", script)
        self.assertNotIn("toolResultDetail", script)
        self.assertIn(
            ".app-shell, .app-shell:has(.inspector.hidden) { grid-template-columns: minmax(0, 1fr)",
            stylesheet,
        )
        self.assertIn(
            ".inspector-main { display: grid; min-width: 0; min-height: 0; grid-template-rows: 38px minmax(0, 1fr);",
            stylesheet,
        )
        self.assertIn(
            ".inspector-main:has(.editor-notice:not(.hidden))",
            stylesheet,
        )
        self.assertIn(
            "document.querySelector(\".app-shell\").style.removeProperty(\"grid-template-columns\")",
            script,
        )

    def test_application_shell_can_resume_a_policy_clarification_pause(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/app.js", timeout=3) as response:
            script = response.read().decode("utf-8")

        self.assertIn('awaiting_clarification: "等待补充说明"', script)
        self.assertIn("function resumeAfterClarification", script)
        self.assertIn("/clarify", script)
        self.assertIn("补充允许范围或换一种安全的验证方式", script)

    def test_workspace_sidebar_keeps_the_path_and_refresh_control_separate(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/", timeout=3) as response:
            document = response.read().decode("utf-8")
        with urllib.request.urlopen(f"{self.base_url}/styles.css", timeout=3) as response:
            stylesheet = response.read().decode("utf-8")

        self.assertIn('class="sidebar-section workspace-section"', document)
        self.assertIn('id="refresh-tree"', document)
        self.assertIn('class="workspace-path" id="workspace-path"', document)
        self.assertIn(
            ".workspace-section { display: grid; min-height: 0; flex: 1; grid-template-rows: 28px auto minmax(0, 1fr); }",
            stylesheet,
        )
        self.assertIn(
            ".section-heading { justify-content: space-between; gap: 8px; min-width: 0;",
            stylesheet,
        )
        self.assertIn(
            ".workspace-path { min-width: 0; overflow: hidden;",
            stylesheet,
        )

    def test_application_shell_includes_editable_ide_controls(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/", timeout=3) as response:
            document = response.read().decode("utf-8")
        with urllib.request.urlopen(f"{self.base_url}/app.js", timeout=3) as response:
            script = response.read().decode("utf-8")
        with urllib.request.urlopen(f"{self.base_url}/styles.css", timeout=3) as response:
            stylesheet = response.read().decode("utf-8")

        self.assertIn('id="editor-input"', document)
        self.assertIn('id="save-file"', document)
        self.assertIn('id="editor-reload"', document)
        self.assertIn('id="editor-notice"', document)
        self.assertIn("raw=1", script)
        self.assertIn('method: "PUT"', script)
        self.assertIn("saveActiveFile", script)
        self.assertIn(".inspector-main", stylesheet)
        self.assertIn(".editor-input", stylesheet)

    def test_application_shell_includes_run_and_debug_controls(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/", timeout=3) as response:
            document = response.read().decode("utf-8")
        with urllib.request.urlopen(f"{self.base_url}/app.js", timeout=3) as response:
            script = response.read().decode("utf-8")

        self.assertIn('id="run-file"', document)
        self.assertIn('id="debug-file"', document)
        self.assertIn("runActiveFile", script)
        self.assertIn("debugActiveFile", script)
        self.assertIn('const endpoint = mode === "debug" ? "/api/debug" : "/api/run"', script)
        self.assertIn("fetchJson(endpoint", script)
        self.assertIn("function isRunnableFile(path)", script)
        self.assertIn("!isRunnableFile(state.activeFile)", script)
        self.assertIn("仅支持 Python", script)
        self.assertIn("task_id: state.activeFileDraft ? state.activeTask : undefined", script)
        self.assertIn("const executionLocked = state.saving || state.executing;", script)
        self.assertNotIn("runFile.disabled = !state.activeFile || !runnable || locked;", script)
        self.assertIn("editorInput.disabled = false", script)

    def test_completed_task_clears_consumed_draft_editor_state(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/app.js", timeout=3) as response:
            script = response.read().decode("utf-8")

        self.assertIn(
            'if (complete && state.activeTask === taskId && state.activeFileDraft) {',
            script,
        )
        self.assertIn(
            'state.activeFileDraft = false;',
            script,
        )

    def test_run_endpoint_saves_content_and_returns_process_details(self) -> None:
        payload = self.request_json(
            "/api/run",
            method="POST",
            payload={
                "path": "src/main.py",
                "content": "print('edited')\n",
                "timeout_seconds": 3,
            },
        )

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "run")
        self.assertEqual(payload["path"], "src/main.py")
        self.assertEqual(payload["command"], "python3 src/main.py")
        self.assertEqual(payload["exit_code"], 0)
        self.assertIn("edited", payload["stdout"])
        self.assertEqual(payload["stderr"], "")
        self.assertGreaterEqual(payload["duration_ms"], 0)
        self.assertEqual((self.workspace / "src" / "main.py").read_text(encoding="utf-8"), "print('edited')\n")

    def test_run_endpoint_executes_review_draft_without_persisting(self) -> None:
        with TemporaryDirectory() as storage_dir:
            storage = Path(storage_dir)
            self.replace_server(DraftCreatedFileModel, storage)
            created = self.request_json(
                "/api/tasks",
                method="POST",
                payload={"task": "生成 rotting_oranges.py 并等待我审阅", "mode": "execute"},
            )
            self.wait_for_task_status(created["id"], "review_required")

            result = self.request_json(
                "/api/run",
                method="POST",
                payload={
                    "path": "rotting_oranges.py",
                    "content": DraftCreatedFileModel.content,
                    "task_id": created["id"],
                },
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["path"], "rotting_oranges.py")
            self.assertFalse((self.workspace / "rotting_oranges.py").exists())

    def test_debug_endpoint_returns_traceback_for_runtime_error(self) -> None:
        payload = self.request_json(
            "/api/debug",
            method="POST",
            payload={"path": "src/main.py", "content": "raise ValueError('boom')\n"},
        )

        self.assertFalse(payload["ok"])
        self.assertEqual(payload["mode"], "debug")
        self.assertEqual(payload["command"], "python3 -X faulthandler src/main.py")
        self.assertEqual(payload["exit_code"], 1)
        self.assertIn("ValueError: boom", payload["stderr"])
        self.assertIn("Traceback", payload["output"])
        self.assertIn("src/main.py", payload["output"])

    def test_run_and_debug_reject_unsafe_or_unsupported_requests(self) -> None:
        for endpoint in ("/api/run", "/api/debug"):
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.request_json(endpoint, method="POST", payload={"path": "../outside.py"})
            self.assertEqual(raised.exception.code, 400)
            raised.exception.close()

            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.request_json(endpoint, method="POST", payload={"path": "note.txt"})
            self.assertEqual(raised.exception.code, 400)
            error_payload = json.loads(raised.exception.read().decode("utf-8"))
            raised.exception.close()
            self.assertIn("Python", error_payload["error"])

    def test_run_endpoint_reports_timeout(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as raised:
            self.request_json(
                "/api/run",
                method="POST",
                payload={"path": "src/main.py", "content": "while True:\n    pass\n", "timeout_seconds": 0.05},
            )
        self.assertEqual(raised.exception.code, 408)
        payload = json.loads(raised.exception.read().decode("utf-8"))
        raised.exception.close()
        self.assertIn("超时", payload["error"])

    def test_agent_file_changes_auto_open_the_changed_file_unless_the_editor_is_dirty(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/app.js", timeout=3) as response:
            script = response.read().decode("utf-8")

        self.assertIn("async function handleAgentFileChange(event)", script)
        self.assertIn("function clearActiveEditor()", script)
        self.assertIn("const deletedFiles = Array.isArray(result.deleted_files) ? result.deleted_files : [];", script)
        self.assertIn('if (event.name === "run_command") {', script)
        self.assertIn("clearActiveEditor();", script)
        self.assertIn('if (!["write_file", "apply_patch"].includes(event.name)) return;', script)
        self.assertIn("if (!changedPath) return;", script)
        self.assertIn("if (state.dirty) {", script)
        self.assertIn("choosePreferredDraftFile", script)
        self.assertIn("await loadEditorFile(preferred || changedPath, { taskId: state.activeTask });", script)
        self.assertIn("activeFileDraft: false", script)
        self.assertIn("可编辑草稿", script)
        self.assertIn("loadTree().catch", script)
        self.assertNotIn(
            'if (!state.activeFile || !["write_file", "apply_patch"].includes(event.name)) return;',
            script,
        )

    def test_editor_can_save_active_task_draft_without_persisting_to_workspace(self) -> None:
        with TemporaryDirectory() as storage_dir:
            storage = Path(storage_dir)
            self.replace_server(DraftCreatedFileModel, storage)
            created = self.request_json(
                "/api/tasks",
                method="POST",
                payload={"task": "生成 rotting_oranges.py 并等待我审阅", "mode": "execute"},
            )
            self.wait_for_task_status(created["id"], "review_required")
            edited = "print('edited draft')\n"
            saved = self.request_json(
                "/api/file",
                method="PUT",
                payload={"path": "rotting_oranges.py", "content": edited, "task_id": created["id"]},
            )
            self.assertTrue(saved["draft"])
            loaded = self.request_json(
                f"/api/file?path=rotting_oranges.py&raw=1&task_id={created['id']}",
            )
            self.assertEqual(loaded["content"], edited)
            self.assertFalse((self.workspace / "rotting_oranges.py").exists())

    def test_task_manager_restores_completed_task_report_and_events_after_restart(self) -> None:
        from coding_agent.web import TaskManager

        with TemporaryDirectory() as storage_dir:
            storage = Path(storage_dir)
            first = TaskManager(
                self.workspace,
                model_factory=ScriptedModel,
                log_root=storage / "logs",
                session_db=storage / "sessions.sqlite3",
            )
            record = first.create_task("持久化测试")
            for _ in range(80):
                if record.status in {"complete", "error", "incomplete"}:
                    break
                time.sleep(0.025)
            self.assertEqual(record.status, "complete")
            event_count = len(record.events)
            report_summary = record.report["summary"]
            first.close(timeout=2)

            second = TaskManager(
                self.workspace,
                model_factory=ScriptedModel,
                log_root=storage / "logs",
                session_db=storage / "sessions.sqlite3",
            )
            restored = second.get_task(record.task_id)
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.status, "complete")
            self.assertEqual(restored.report["summary"], report_summary)
            self.assertGreaterEqual(len(restored.events), event_count)
            self.assertTrue(any(event["type"] == "run_finished" for event in restored.events))
            second.close(timeout=2)

    def test_task_manager_marks_inflight_task_interrupted_after_restart(self) -> None:
        from coding_agent.session_store import SessionStore
        from coding_agent.web import TaskManager

        with TemporaryDirectory() as storage_dir:
            storage = Path(storage_dir)
            log_path = storage / "logs" / "unfinished.jsonl"
            log_path.parent.mkdir(parents=True)
            log_path.write_text('{"type":"run_started","task":"未完成"}\n', encoding="utf-8")
            store = SessionStore(storage / "sessions.sqlite3")
            store.upsert_task({
                "id": "unfinished",
                "task": "未完成",
                "mode": "execute",
                "status": "running",
                "created_at": "2026-08-29T00:00:00+00:00",
                "finished_at": None,
                "log_path": str(log_path),
                "report": None,
            })
            store.close()
            manager = TaskManager(
                self.workspace,
                model_factory=ScriptedModel,
                log_root=storage / "logs",
                session_db=storage / "sessions.sqlite3",
            )
            restored = manager.get_task("unfinished")
            self.assertIsNotNone(restored)
            assert restored is not None
            self.assertEqual(restored.status, "interrupted")
            self.assertIn("服务重启", restored.report["summary"])
            manager.close(timeout=2)

    def test_non_deleting_command_completes_without_an_approval_pause(self) -> None:
        with TemporaryDirectory() as storage_dir:
            storage = Path(storage_dir)
            self.replace_server(SafeCommandThenFinishModel, storage)
            created = self.request_json(
                "/api/tasks",
                method="POST",
                payload={"task": "执行额外检查。", "mode": "execute"},
            )
            finished = self.wait_for_finished_task(created["id"])

            self.assertEqual(finished["status"], "complete")
            record = self.server.manager.get_task(created["id"])
            assert record is not None
            events = "\n".join(json.dumps(event, ensure_ascii=False) for event in record.events)
            self.assertNotIn('"status": "awaiting_approval"', events)
            self.assertIn("非删除命令已执行", finished["report"]["summary"])

    def test_approved_deletion_executes_once_and_task_finishes(self) -> None:
        with TemporaryDirectory() as storage_dir:
            storage = Path(storage_dir)
            target = self.workspace / "approved-delete.txt"
            target.write_text("remove me", encoding="utf-8")
            self.replace_server(DeletionApprovalModel, storage)
            created = self.request_json(
                "/api/tasks",
                method="POST",
                payload={"task": "删除已批准的临时文件", "mode": "execute"},
            )
            paused = self.wait_for_task_status(created["id"], "awaiting_approval")
            self.assertTrue(target.exists())
            self.assertIsNone(paused["finished_at"])
            self.assertEqual(paused["pending"]["name"], "run_command")

            resumed = self.request_json(
                f"/api/tasks/{created['id']}/approval",
                method="POST",
                payload={"approved": True},
            )
            self.assertEqual(resumed["status"], "running")
            finished = self.wait_for_finished_task(created["id"])

            self.assertEqual(finished["status"], "complete")
            self.assertFalse(target.exists())
            self.assertIsNotNone(finished["finished_at"])
            self.assertNotEqual(finished["status"], "awaiting_approval")
            record = self.server.manager.get_task(created["id"])
            assert record is not None
            delete_calls = [
                event
                for event in record.events
                if event.get("type") == "tool_call"
                and event.get("name") == "run_command"
                and event.get("arguments", {}).get("command") == "rm -f approved-delete.txt"
            ]
            self.assertEqual(len(delete_calls), 1)

    def test_review_pause_exposes_draft_after_restart_and_accepting_it_commits(self) -> None:
        with TemporaryDirectory() as storage_dir:
            storage = Path(storage_dir)
            self.replace_server(DraftReviewModel, storage)
            created = self.request_json(
                "/api/tasks",
                method="POST",
                payload={"task": "修改 note.txt 并等待我审阅草稿。", "mode": "execute"},
            )
            paused = self.wait_for_task_status(created["id"], "review_required")

            self.assertIsNone(paused["finished_at"])
            self.assertEqual((self.workspace / "note.txt").read_text(encoding="utf-8"), "draft\n")

            self.replace_server(DraftReviewModel, storage)
            draft = self.request_json(f"/api/tasks/{created['id']}/draft")
            self.assertEqual(draft["task_id"], created["id"])
            self.assertEqual(draft["files"], ["note.txt"])
            self.assertIn("-draft", draft["diff"])
            self.assertIn("+final", draft["diff"])

            resumed = self.request_json(
                f"/api/tasks/{created['id']}/review",
                method="POST",
                payload={"accepted": True},
            )
            self.assertEqual(resumed["status"], "running")
            finished = self.wait_for_finished_task(created["id"])
            self.assertEqual(finished["status"], "complete")
            self.assertEqual((self.workspace / "note.txt").read_text(encoding="utf-8"), "final\n")

    def test_review_rejects_a_non_boolean_decision(self) -> None:
        with TemporaryDirectory() as storage_dir:
            storage = Path(storage_dir)
            self.replace_server(DraftReviewModel, storage)
            review = self.request_json(
                "/api/tasks",
                method="POST",
                payload={"task": "审阅参数校验", "mode": "execute"},
            )
            self.wait_for_task_status(review["id"], "review_required")
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.request_json(
                    f"/api/tasks/{review['id']}/review",
                    method="POST",
                    payload={"accepted": 1},
                )
            self.assertEqual(raised.exception.code, 400)
            raised.exception.close()

    def test_policy_rejection_pauses_and_clarify_endpoint_resumes_without_replaying(self) -> None:
        with TemporaryDirectory() as storage_dir:
            storage = Path(storage_dir)
            self.replace_server(PolicyClarificationModel, storage)
            created = self.request_json(
                "/api/tasks",
                method="POST",
                payload={"task": "检查临时输出", "mode": "execute"},
            )
            paused = self.wait_for_task_status(created["id"], "awaiting_clarification")

            self.assertIsNone(paused["finished_at"])
            self.assertEqual(paused["report"]["failure"]["type"], "policy_rejected")
            self.assertIsNone(paused["pending"])

            record = self.server.manager.get_task(created["id"])
            assert record is not None
            self.assertEqual(len(record.events), 5)
            self.assertTrue(any(event["type"] == "run_paused" for event in record.events))

            resumed = self.request_json(
                f"/api/tasks/{created['id']}/clarify",
                method="POST",
                payload={"instruction": "只允许检查 temporary-output 目录，不要删除文件"},
            )
            self.assertEqual(resumed["status"], "running")
            finished = self.wait_for_finished_task(created["id"])
            self.assertEqual(finished["status"], "complete")
            self.assertIn("允许范围", finished["report"]["summary"])

            rejected_command_calls = [
                event
                for event in record.events
                if event["type"] == "tool_call"
                and event.get("name") == "run_command"
                and event.get("arguments", {}).get("command") == ""
            ]
            self.assertEqual(len(rejected_command_calls), 1)

    def test_policy_clarification_pause_ends_the_event_stream(self) -> None:
        with TemporaryDirectory() as storage_dir:
            self.replace_server(PolicyClarificationModel, Path(storage_dir))
            created = self.request_json(
                "/api/tasks",
                method="POST",
                payload={"task": "检查临时输出", "mode": "execute"},
            )
            paused = self.wait_for_task_status(created["id"], "awaiting_clarification")
            record = self.server.manager.get_task(created["id"])
            assert record is not None

            _, event_stream_finished = self.server.manager.wait_for_events(
                record,
                paused["event_count"],
                timeout=0,
            )

            self.assertTrue(event_stream_finished)

    def test_clarify_endpoint_validates_instruction_task_and_status(self) -> None:
        with TemporaryDirectory() as storage_dir:
            storage = Path(storage_dir)
            self.replace_server(PolicyClarificationModel, storage)
            created = self.request_json(
                "/api/tasks",
                method="POST",
                payload={"task": "检查临时输出", "mode": "execute"},
            )
            self.wait_for_task_status(created["id"], "awaiting_clarification")

            for payload in ({"instruction": ""}, {"instruction": "   "}):
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    self.request_json(
                        f"/api/tasks/{created['id']}/clarify",
                        method="POST",
                        payload=payload,
                    )
                self.assertEqual(raised.exception.code, 400)
                raised.exception.close()

            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.request_json(
                    "/api/tasks/missing/clarify",
                    method="POST",
                    payload={"instruction": "说明允许范围"},
                )
            self.assertEqual(raised.exception.code, 404)
            raised.exception.close()

            self.request_json(
                f"/api/tasks/{created['id']}/clarify",
                method="POST",
                payload={"instruction": "只允许检查目录"},
            )
            self.wait_for_finished_task(created["id"])
            with self.assertRaises(urllib.error.HTTPError) as raised:
                self.request_json(
                    f"/api/tasks/{created['id']}/clarify",
                    method="POST",
                    payload={"instruction": "再次说明"},
                )
            self.assertEqual(raised.exception.code, 409)
            raised.exception.close()

    def test_awaiting_clarification_survives_restart_and_can_resume(self) -> None:
        with TemporaryDirectory() as storage_dir:
            storage = Path(storage_dir)
            self.replace_server(PolicyClarificationModel, storage)
            created = self.request_json(
                "/api/tasks",
                method="POST",
                payload={"task": "重启后继续说明", "mode": "execute"},
            )
            self.wait_for_task_status(created["id"], "awaiting_clarification")

            self.replace_server(PolicyClarificationModel, storage)
            restored = self.request_json(f"/api/tasks/{created['id']}")
            self.assertEqual(restored["status"], "awaiting_clarification")
            self.assertIsNone(restored["finished_at"])
            self.assertIsInstance(restored["session"], dict)

            resumed = self.request_json(
                f"/api/tasks/{created['id']}/clarify",
                method="POST",
                payload={"instruction": "只允许检查 temporary-output 目录"},
            )
            self.assertEqual(resumed["status"], "running")
            self.assertEqual(self.wait_for_finished_task(created["id"])["status"], "complete")

    def test_task_manager_persists_event_metadata_while_task_is_running(self) -> None:
        from coding_agent.web import TaskManager

        BlockingModel.started.clear()
        BlockingModel.release.clear()
        with TemporaryDirectory() as storage_dir:
            storage = Path(storage_dir)
            manager = TaskManager(
                self.workspace,
                model_factory=BlockingModel,
                log_root=storage / "logs",
                session_db=storage / "sessions.sqlite3",
            )
            record = manager.create_task("事件持久化测试")
            self.assertTrue(BlockingModel.started.wait(timeout=2))

            persisted_records = []
            original_persist = manager._persist

            def track_persist(target: Any) -> None:
                persisted_records.append(target)
                original_persist(target)

            manager._persist = track_persist  # type: ignore[method-assign]

            manager._append_event(record, {"type": "model_delta", "content": "正在分析"})

            self.assertEqual(persisted_records, [record])
            BlockingModel.release.set()
            manager.close(timeout=2)

    def test_task_history_endpoint_replays_events_and_resume_creates_new_task(self) -> None:
        from coding_agent.web import create_server

        with TemporaryDirectory() as storage_dir:
            storage = Path(storage_dir)
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
            self.server = create_server(
                self.workspace,
                host="127.0.0.1",
                port=0,
                model_factory=FailingModel,
                log_root=storage / "logs",
                session_db=storage / "sessions.sqlite3",
            )
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            host, port = self.server.server_address[:2]
            self.base_url = f"http://{host}:{port}"
            created = self.request_json("/api/tasks", method="POST", payload={"task": "保留历史"})
            finished = self.wait_for_finished_task(created["id"])
            history = self.request_json("/api/tasks")
            self.assertTrue(any(item["id"] == created["id"] for item in history["tasks"]))
            with urllib.request.urlopen(f"{self.base_url}/api/tasks/{created['id']}/events?after=0", timeout=3) as response:
                event_stream = response.read().decode("utf-8")
            self.assertIn("event: run_started", event_stream)
            self.assertIn("event: model_error", event_stream)
            resumed = self.request_json(f"/api/tasks/{created['id']}/resume", method="POST", payload={})
            self.assertNotEqual(resumed["id"], created["id"])
            self.assertEqual(resumed["task"], finished["task"])
            self.assertEqual(resumed["resumed_from"], created["id"])
            self.wait_for_finished_task(resumed["id"])
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)

    def test_task_manager_close_timeout_keeps_storage_open_until_worker_finishes(self) -> None:
        from coding_agent.web import TaskManager

        BlockingModel.started.clear()
        BlockingModel.release.clear()
        with TemporaryDirectory() as storage_dir:
            storage = Path(storage_dir)
            manager = TaskManager(
                self.workspace,
                model_factory=BlockingModel,
                log_root=storage / "logs",
                session_db=storage / "sessions.sqlite3",
            )
            record = manager.create_task("关闭竞态测试")
            self.assertTrue(BlockingModel.started.wait(timeout=2))

            manager.close(timeout=0.01)
            self.assertIsNotNone(manager.session_store.get_task(record.task_id))

            BlockingModel.release.set()
            for _ in range(80):
                if record.status in {"complete", "error", "incomplete"}:
                    break
                time.sleep(0.025)
            self.assertEqual(record.status, "complete")
            manager.close(timeout=2)

    def test_application_shell_includes_persistent_task_history_controls(self) -> None:
        with urllib.request.urlopen(f"{self.base_url}/", timeout=3) as response:
            document = response.read().decode("utf-8")
        with urllib.request.urlopen(f"{self.base_url}/app.js", timeout=3) as response:
            script = response.read().decode("utf-8")

        self.assertIn('id="session-list"', document)
        self.assertIn("loadTaskHistory", script)
        self.assertIn("renderTaskHistory", script)
        self.assertIn("/api/tasks/", script)
        self.assertIn("/resume", script)
        self.assertIn("event_count", script)
        self.assertIn("/draft", script)
        self.assertIn("async function openTaskDraft", script)
        self.assertIn("await openTaskDraft(task.id)", script)
        self.assertIn("/approval", script)
        self.assertIn("/review", script)
        self.assertIn("等待命令审批", script)
        self.assertIn("等待草稿审阅", script)


if __name__ == "__main__":
    unittest.main()
