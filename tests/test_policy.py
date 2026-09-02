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

    def test_python_inline_check_with_code_operators_is_allowed(self) -> None:
        command = 'python3 -c "assert 2 > 1; print(\'check passed\')"'

        self.assertEqual(classify_command(command), "allow")

    def test_recursive_delete_requires_approval(self) -> None:
        self.assertEqual(classify_command("rm -rf generated"), "approval_required")

    def test_delete_command_requires_approval_when_wrapped(self) -> None:
        self.assertEqual(classify_command("sudo rm -rf generated"), "approval_required")

    def test_non_destructive_commands_are_allowed_without_a_whitelist(self) -> None:
        self.assertEqual(classify_command("custom-build --watch"), "allow")
        self.assertEqual(classify_command("pytest -q && echo verification-complete"), "allow")

    def test_allowed_command_has_chinese_reason(self) -> None:
        decision = command_decision("python -m unittest discover -s tests -v")
        self.assertEqual(decision["decision"], "allow")
        self.assertIn("允许执行", decision["reason"])
        self.assertTrue(decision["recommendation"])

    def test_unknown_command_is_allowed_without_confirmation(self) -> None:
        decision = command_decision("custom-build")
        self.assertEqual(decision["decision"], "allow")
        self.assertIn("允许执行", decision["reason"])

    def test_only_explicit_deletion_commands_require_approval(self) -> None:
        self.assertEqual(classify_command("git clean -fd"), "approval_required")
        self.assertEqual(classify_command("python -c \"from pathlib import Path; Path('note.txt').unlink()\""), "approval_required")

    def test_non_deleting_commands_are_allowed_even_when_they_overwrite_data(self) -> None:
        self.assertEqual(classify_command("git reset --hard HEAD"), "allow")
        self.assertEqual(classify_command("dd if=/dev/zero of=/tmp/scratch.bin"), "allow")

    def test_recursive_remove_has_chinese_guidance(self) -> None:
        decision = command_decision("rm -rf generated")
        self.assertEqual(decision["decision"], "approval_required")
        self.assertTrue(decision["reason"])
        self.assertTrue(decision["recommendation"])
