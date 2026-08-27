from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class ScriptedDemoTests(unittest.TestCase):
    def test_demo_repairs_fixture_and_records_expected_tool_trajectory(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        with TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "demo-workspace"
            result = subprocess.run(
                [sys.executable, "examples/scripted_demo.py", "--workspace", str(workspace)],
                cwd=repo_root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["phase"], "COMPLETE")
            self.assertEqual(report["workspace"], str(workspace.resolve()))
            self.assertEqual(report["modified_files"], ["calculator.py"])
            self.assertEqual(report["verification_exit_codes"], [1, 0])
            self.assertEqual(
                (workspace / "calculator.py").read_text(encoding="utf-8"),
                "def add(left: int, right: int) -> int:\n    return left + right\n",
            )

            events = [
                json.loads(line)
                for line in (workspace / "run.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            tool_calls = [event["name"] for event in events if event["type"] == "tool_call"]
            self.assertEqual(
                tool_calls,
                ["run_command", "read_file", "apply_patch", "run_command", "finish"],
            )


if __name__ == "__main__":
    unittest.main()
