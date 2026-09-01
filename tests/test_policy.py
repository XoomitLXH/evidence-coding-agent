from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from coding_agent.policy import PolicyError, classify_command, command_decision, safe_path


class PolicyTests(unittest.TestCase):
    def test_safe_path_rejects_workspace_escape(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with self.assertRaises(PolicyError):
                safe_path(Path(temp_dir), "../outside.py")

    def test_test_command_is_allowed(self) -> None:
        self.assertEqual(classify_command("python -m unittest discover -s tests -v"), "allow")

    def test_recursive_delete_is_rejected(self) -> None:
        self.assertEqual(classify_command("rm -rf generated"), "deny")

    def test_command_chaining_is_rejected(self) -> None:
        self.assertEqual(classify_command("pytest -q && echo unexpected"), "deny")

    def test_allowed_command_has_chinese_reason(self) -> None:
        decision = command_decision("python -m unittest discover -s tests -v")
        self.assertEqual(decision["decision"], "allow")
        self.assertIn("允许执行", decision["reason"])
        self.assertTrue(decision["recommendation"])

    def test_unknown_command_requests_confirmation(self) -> None:
        decision = command_decision("custom-build")
        self.assertEqual(decision["decision"], "approval_required")
        self.assertIn("需要确认", decision["recommendation"])

    def test_recursive_remove_has_chinese_guidance(self) -> None:
        decision = command_decision("rm -rf generated")
        self.assertEqual(decision["decision"], "deny")
        self.assertTrue(decision["reason"])
        self.assertTrue(decision["recommendation"])
