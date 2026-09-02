from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from coding_agent.event_log import EventLog
from coding_agent.state import AgentState
from coding_agent.tool_registry import ToolRegistry


class ToolRegistryTests(unittest.TestCase):
    def test_finish_reports_verification_failure_and_keeps_state_incomplete(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state = AgentState()
            state.record_command("python3 -m pytest", 1, "No module named pytest", 10)
            registry = ToolRegistry(root, state, EventLog(root / "events.jsonl"))

            result = registry.call("finish", {"summary": "不应显示成功"})

            self.assertFalse(result["ok"])
            self.assertEqual(result["failure"]["type"], "verification_failed")
            self.assertNotEqual(state.phase, "COMPLETE")

    def test_registry_exposes_shared_drafts_and_keeps_agent_write_in_memory(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ToolRegistry(
                root,
                AgentState(),
                EventLog(root / "events.jsonl"),
                require_draft_review=True,
            )

            result = registry.call("write_file", {"path": "draft.txt", "content": "draft"})

            self.assertIsNotNone(registry.drafts)
            self.assertTrue(result["draft"])
            self.assertFalse((root / "draft.txt").exists())
            self.assertEqual(registry.drafts.read_text("draft.txt"), "draft")

    def test_write_file_evidence_summarizes_content_without_logging_it(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            log_path = root / "events.jsonl"
            registry = ToolRegistry(root, AgentState(), EventLog(log_path))

            result = registry.call(
                "write_file",
                {"path": "notes.txt", "content": "private implementation detail"},
            )

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["evidence"]["tool"], "write_file")
            self.assertIn("content=29 个字符", result["evidence"]["parameters"])
            self.assertNotIn("private implementation detail", result["evidence"]["parameters"])
            event = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertIn("content=29 个字符", event["result"]["evidence"]["parameters"])
            self.assertNotIn("private implementation detail", event["arguments"])

    def test_run_command_validates_the_registry_draft_not_the_real_workspace(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "value.txt").write_text("original\n", encoding="utf-8")
            registry = ToolRegistry(
                root,
                AgentState(),
                EventLog(root / "events.jsonl"),
                require_draft_review=True,
            )
            registry.call("write_file", {"path": "value.txt", "content": "draft\n"})

            result = registry.call(
                "run_command",
                {"command": "python -c \"assert open('value.txt').read() == 'draft\\n'\""},
            )

            self.assertTrue(result["ok"], result)
            self.assertEqual((root / "value.txt").read_text(encoding="utf-8"), "original\n")

    def test_finish_requires_draft_review_after_successful_validation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry = ToolRegistry(
                root,
                AgentState(),
                EventLog(root / "events.jsonl"),
                require_draft_review=True,
            )
            registry.call("write_file", {"path": "draft.txt", "content": "draft\n"})
            verification = registry.call("run_command", {"command": "python -c 'pass'"})

            result = registry.call("finish", {"summary": "已经完成"})

            self.assertTrue(verification["ok"], verification)
            self.assertFalse(result["ok"])
            self.assertEqual(result["failure"]["type"], "review_required")
            self.assertEqual(result["drafts"], ["draft.txt"])


if __name__ == "__main__":
    unittest.main()
