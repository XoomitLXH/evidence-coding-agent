from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from coding_agent.agent_loop import AgentLoop


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
    def __init__(self, responses: list[dict[str, Any]]):
        self.responses = responses

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        return self.responses.pop(0)


class AgentLoopTests(unittest.TestCase):
    def test_malformed_tool_arguments_are_logged_without_crashing_the_loop(self) -> None:
        malformed_response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": "{not json"},
                            }
                        ],
                    }
                }
            ]
        }
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model = ScriptedModel([
                malformed_response,
                tool_response("finish", {"summary": "done"}, "call-2"),
            ])

            report = AgentLoop(root, model, max_steps=2).run("inspect repository")

            self.assertEqual(report["status"], "incomplete")
            events = [
                json.loads(line)
                for line in (root / "run.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            results = [event["result"] for event in events if event["type"] == "tool_result"]
            self.assertTrue(any(result.get("error") == "tool arguments must be a JSON object" for result in results))

    def test_finish_is_rejected_without_successful_verification(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model = ScriptedModel([tool_response("finish", {"summary": "done"}, "call-1")])

            report = AgentLoop(root, model, max_steps=1).run("do nothing")

            self.assertEqual(report["status"], "incomplete")
            self.assertTrue(report["evidence_required"])

    def test_agent_repairs_a_failing_repository_and_records_evidence(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "calculator.py").write_text(
                "def add(left, right):\n    return left - right\n",
                encoding="utf-8",
            )
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_calculator.py").write_text(
                "import unittest\n\nfrom calculator import add\n\n\n"
                "class CalculatorTests(unittest.TestCase):\n"
                "    def test_add(self):\n"
                "        self.assertEqual(add(2, 3), 5)\n",
                encoding="utf-8",
            )
            model = ScriptedModel(
                [
                    tool_response("run_command", {"command": "python3 -m unittest discover -s tests -v"}, "call-1"),
                    tool_response("read_file", {"path": "calculator.py"}, "call-2"),
                    tool_response(
                        "apply_patch",
                        {"path": "calculator.py", "old_text": "return left - right", "new_text": "return left + right"},
                        "call-3",
                    ),
                    tool_response("run_command", {"command": "python3 -m unittest discover -s tests -v"}, "call-4"),
                    tool_response("finish", {"summary": "Fixed addition and reran tests."}, "call-5"),
                ]
            )

            report = AgentLoop(root, model, max_steps=5).run("fix addition")

            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["phase"], "COMPLETE")
            self.assertEqual(report["modified_files"], ["calculator.py"])
            self.assertEqual([item["exit_code"] for item in report["verification"]], [1, 0])
            self.assertFalse(report["evidence_required"])
            events = (root / "run.jsonl").read_text(encoding="utf-8")
            self.assertIn('"type": "run_finished"', events)
