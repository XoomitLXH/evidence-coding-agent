from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from coding_agent.state import AgentState
from coding_agent.tools_shell import run_command


class VerificationGateTests(unittest.TestCase):
    def test_failure_ledger_keeps_only_the_most_recent_entries(self) -> None:
        state = AgentState()

        for index in range(55):
            state.add_ledger(f"event-{index}")

        self.assertEqual(len(state.ledger), state.MAX_LEDGER_ENTRIES)
        self.assertEqual(state.ledger[0], "event-5")
        self.assertEqual(state.ledger[-1], "event-54")

    def test_command_that_changes_workspace_is_not_verification(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "mutate.py").write_text(
                "from pathlib import Path\nPath('result.txt').write_text('changed')\n",
                encoding="utf-8",
            )
            state = AgentState()

            result = run_command(root, state, "python3 mutate.py")

            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(result["changed_files"], ["result.txt"])
            self.assertFalse(state.can_finish())

    def test_clean_successful_command_is_verification_evidence(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "verify.py").write_text("print('verified')\n", encoding="utf-8")
            state = AgentState()
            state.mark_modified(["module.py"])

            result = run_command(root, state, "python3 verify.py")

            self.assertEqual(result["exit_code"], 0)
            self.assertTrue(state.can_finish())

    def test_new_modification_invalidates_previous_verification(self) -> None:
        state = AgentState()
        state.mark_modified(["module.py"])
        state.record_command("pytest -q", 0, "", 8)

        state.mark_modified(["module.py"])

        self.assertFalse(state.can_finish())
