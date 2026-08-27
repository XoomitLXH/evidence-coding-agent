from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from coding_agent.policy import PolicyError, classify_command, safe_path


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
