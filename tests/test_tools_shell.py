from __future__ import annotations

import unittest
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from coding_agent.draft import DraftChanges
from coding_agent.state import AgentState
from coding_agent.tools_shell import _command_arguments, run_command


class RunCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_python3_uses_the_active_interpreter(self) -> None:
        arguments = _command_arguments("python3 -c 'pass'")

        self.assertEqual(arguments[0], sys.executable)

    def test_deletion_command_waits_for_approval_without_modifying_workspace(self) -> None:
        target = self.root / "to-delete.txt"
        target.write_text("keep me\n", encoding="utf-8")
        state = AgentState()
        result = run_command(self.root, state, "rm -f to-delete.txt")

        self.assertFalse(result["ok"])
        self.assertEqual(result["risk"]["decision"], "approval_required")
        self.assertEqual(result["failure"]["type"], "awaiting_approval")
        self.assertEqual(result["changed_files"], [])
        self.assertEqual(result["deleted_files"], [])
        self.assertEqual(state.verification, [])
        self.assertTrue(target.exists())

    def test_approved_deletion_executes_in_real_workspace(self) -> None:
        target = self.root / "to-delete.txt"
        target.write_text("remove me\n", encoding="utf-8")
        state = AgentState()
        result = run_command(self.root, state, "rm -f to-delete.txt", approval_granted=True)

        self.assertTrue(result["ok"])
        self.assertFalse(target.exists())
        self.assertEqual(result["deleted_files"], ["to-delete.txt"])
        self.assertTrue(state.can_finish())

    def test_non_whitelisted_non_deleting_command_executes_without_approval(self) -> None:
        state = AgentState()
        result = run_command(self.root, state, "/bin/echo allowed-without-approval")

        self.assertTrue(result["ok"])
        self.assertEqual(result["risk"]["decision"], "allow")
        self.assertIsNone(result["failure"])
        self.assertEqual(result["changed_files"], [])
        self.assertEqual(len(state.verification), 1)

    def test_nonzero_command_returns_failure_evidence(self) -> None:
        result = run_command(self.root, AgentState(), "python -c 'raise SystemExit(2)'")

        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 2)
        self.assertEqual(result["failure"]["type"], "command_failed")
        self.assertEqual(result["evidence"]["tool"], "run_command")
        self.assertEqual(result["evidence"]["exit_code"], 2)

    def test_timeout_returns_timeout_evidence(self) -> None:
        result = run_command(
            self.root,
            AgentState(),
            "python -c \"__import__('time').sleep(2)\"",
            timeout_seconds=1,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 124)
        self.assertEqual(result["failure"]["type"], "timeout")
        self.assertEqual(result["evidence"]["exit_code"], 124)

    def test_command_verifies_draft_in_an_isolated_workspace_copy(self) -> None:
        (self.root / "value.txt").write_text("original\n", encoding="utf-8")
        drafts = DraftChanges(self.root)
        drafts.write_file("value.txt", "draft\n")

        result = run_command(
            self.root,
            AgentState(),
            "python -c \"assert open('value.txt').read() == 'draft\\n'\"",
            drafts=drafts,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["changed_files"], [])
        self.assertEqual((self.root / "value.txt").read_text(encoding="utf-8"), "original\n")


if __name__ == "__main__":
    unittest.main()
