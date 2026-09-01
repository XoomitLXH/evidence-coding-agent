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
        self.messages_seen: list[list[dict[str, Any]]] = []
        self.tools_seen: list[list[dict[str, Any]]] = []

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        self.messages_seen.append(list(messages))
        self.tools_seen.append(list(tools))
        return self.responses.pop(0)


class StreamingModel:
    def __init__(self):
        self.tools_seen = []
        self.messages_seen = []

    def stream(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]):
        self.messages_seen.append(list(messages))
        self.tools_seen.append(list(tools))
        yield {"reasoning": "先检查上下文。", "content": "", "tool_calls": [], "finish_reason": None}
        yield {"reasoning": "", "content": "我会继续处理。", "tool_calls": [], "finish_reason": "stop"}


class PhaseOnlyCompleteModel:
    def __init__(self, loop: AgentLoop):
        self.loop = loop

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        self.loop.state.phase = "COMPLETE"
        self.loop.state.failed_commands = 1
        return {"choices": [{"message": {"role": "assistant", "content": "已完成"}}]}


class ExplodingModel:
    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        raise RuntimeError("模型服务暂不可用")


class AgentLoopTests(unittest.TestCase):
    def test_phase_complete_without_verification_gate_does_not_report_complete(self) -> None:
        with TemporaryDirectory() as temp_dir:
            loop = AgentLoop(Path(temp_dir), object(), max_steps=1)
            loop.model = PhaseOnlyCompleteModel(loop)

            report = loop.run("执行验证")

            self.assertNotEqual(report["status"], "complete")
            self.assertIsNotNone(report["failure"])

    def test_plugin_schema_and_skill_prompt_reach_sync_model(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plugin = root / "demo"
            (plugin / ".codex-plugin").mkdir(parents=True)
            (plugin / "skills" / "demo").mkdir(parents=True)
            (plugin / ".codex-plugin" / "plugin.json").write_text(json.dumps({"name": "demo"}), encoding="utf-8")
            (plugin / "skills" / "demo" / "SKILL.md").write_text("---\nname: focused\ndescription: focused workflow\n---\nUse focused workflow.\n", encoding="utf-8")
            model = ScriptedModel([tool_response("finish", {"summary": "done"}, "finish-1")])

            report = AgentLoop(root, model, max_steps=1, plugin_dirs=[root], explicit_skills=["focused"]).run("inspect")

            self.assertEqual(report["status"], "complete")
            self.assertIn("read_pdf", {item["function"]["name"] for item in model.tools_seen[0]})
            self.assertIn("Use focused workflow.", model.messages_seen[0][0]["content"])

    def test_streaming_model_receives_dynamic_plugin_schema(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model = StreamingModel()

            AgentLoop(root, model, max_steps=1).run("inspect repository")

            self.assertEqual(len(model.tools_seen), 1)
            self.assertIn("read_pdf", {item["function"]["name"] for item in model.tools_seen[0]})

    def test_streaming_model_publishes_incremental_events_and_final_message(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            AgentLoop(root, StreamingModel(), max_steps=1).run("inspect repository")

            events = [
                json.loads(line)
                for line in (root / "run.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        types = [event["type"] for event in events]
        self.assertIn("model_message_start", types)
        self.assertIn("model_delta", types)
        self.assertIn("model_message_end", types)
        self.assertIn("model_response", types)
        delta_text = "".join(event.get("content", "") for event in events if event["type"] == "model_delta")
        self.assertEqual(delta_text, "我会继续处理。")

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

    def test_write_file_content_is_redacted_from_persisted_events(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            private_content = "private implementation detail"
            model = ScriptedModel([
                tool_response(
                    "write_file",
                    {"path": "notes.txt", "content": private_content},
                    "call-1",
                ),
            ])

            AgentLoop(root, model, max_steps=1).run("写入说明文件")

            events = (root / "run.jsonl").read_text(encoding="utf-8")
            self.assertNotIn(private_content, events)
            self.assertIn("content=29 \\u4e2a\\u5b57\\u7b26", events)

    def test_finish_is_allowed_for_a_read_only_task_without_running_a_command(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model = ScriptedModel([tool_response("finish", {"summary": "done"}, "call-1")])

            report = AgentLoop(root, model, max_steps=1).run("do nothing")

            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["verification"], [])
            self.assertFalse(report["evidence_required"])

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

            loop = AgentLoop(root, model, max_steps=5)
            initial = loop.run("fix addition")

            self.assertEqual(initial["status"], "review_required")
            self.assertTrue(loop.registry.drafts.accept())
            report = loop.resume_after_review(accepted=True)

            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["phase"], "COMPLETE")
            self.assertEqual(report["modified_files"], ["calculator.py"])
            self.assertEqual([item["exit_code"] for item in report["verification"]], [1, 0])
            self.assertFalse(report["evidence_required"])
            events = (root / "run.jsonl").read_text(encoding="utf-8")
            self.assertIn('"type": "run_finished"', events)

    def test_model_exception_has_a_recovery_record(self) -> None:
        with TemporaryDirectory() as temp_dir:
            report = AgentLoop(Path(temp_dir), ExplodingModel(), max_steps=1).run("修复项目")

        self.assertEqual(report["failure"]["type"], "model_request_failed")
        self.assertTrue(report["failure"]["recovery"])
        self.assertIn("模型请求失败", report["failure"]["label"])

    def test_denied_tool_call_is_attributed_to_policy(self) -> None:
        with TemporaryDirectory() as temp_dir:
            model = ScriptedModel([
                tool_response("run_command", {"command": "rm -rf temporary-output"}, "call-1"),
            ])

            report = AgentLoop(Path(temp_dir), model, max_steps=1).run("清理临时输出")

        self.assertEqual(report["failure"]["type"], "policy_rejected")
        self.assertIn("安全策略", report["failure"]["label"])

    def test_policy_rejection_pauses_and_clarification_resumes_without_replaying_command(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model = ScriptedModel([
                tool_response("run_command", {"command": "rm -rf temporary-output"}, "call-1"),
                tool_response("finish", {"summary": "已根据允许范围重新规划。"}, "call-2"),
            ])
            loop = AgentLoop(root, model, max_steps=2)

            initial = loop.run("清理临时输出")

            self.assertEqual(initial["status"], "awaiting_clarification")
            self.assertEqual(initial["failure"]["type"], "policy_rejected")
            self.assertEqual(len(model.messages_seen), 1)
            self.assertEqual(len(model.responses), 1)
            events = (root / "run.jsonl").read_text(encoding="utf-8")
            self.assertIn('"type": "run_paused"', events)
            self.assertNotIn('"type": "run_finished"', events)

            report = loop.resume_after_clarification("仅允许检查 temporary-output 目录，不要删除文件。")

            self.assertEqual(report["status"], "complete")
            self.assertEqual(len(model.messages_seen), 2)
            continuation = model.messages_seen[1]
            self.assertEqual(continuation[-3]["role"], "assistant")
            self.assertEqual(continuation[-2]["role"], "tool")
            self.assertIn("policy_rejected", continuation[-2]["content"])
            self.assertEqual(continuation[-1]["role"], "user")
            self.assertIn("仅允许检查 temporary-output 目录", continuation[-1]["content"])
            self.assertIn("不会自动重放", continuation[-1]["content"])

    def test_clarification_requires_a_non_empty_instruction_and_matching_pause(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model = ScriptedModel([
                tool_response("run_command", {"command": "rm -rf temporary-output"}, "call-1"),
            ])
            loop = AgentLoop(root, model, max_steps=1)

            with self.assertRaises(RuntimeError):
                loop.resume_after_clarification("说明")
            initial = loop.run("清理临时输出")
            self.assertEqual(initial["status"], "awaiting_clarification")
            with self.assertRaises(ValueError):
                loop.resume_after_clarification("   ")

    def test_nonzero_command_is_attributed_to_command_failure(self) -> None:
        with TemporaryDirectory() as temp_dir:
            model = ScriptedModel([
                tool_response("run_command", {"command": "python -c 'raise SystemExit(2)'"}, "call-1"),
            ])

            report = AgentLoop(Path(temp_dir), model, max_steps=1).run("执行验证")

        self.assertEqual(report["failure"]["type"], "command_failed")
        self.assertIn("退出码 2", report["failure"]["reason"])

    def test_unknown_command_pauses_without_consuming_another_model_response(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            model = ScriptedModel([
                tool_response("run_command", {"command": "echo approved-only"}, "call-1"),
                tool_response("finish", {"summary": "命令已执行。"}, "call-2"),
            ])

            report = AgentLoop(root, model, max_steps=2).run("运行额外检查")

            self.assertEqual(report["status"], "awaiting_approval")
            self.assertEqual(report["failure"]["type"], "awaiting_approval")
            self.assertEqual(len(model.messages_seen), 1)
            self.assertEqual(len(model.responses), 1)
            self.assertEqual(report["verification"], [])
            events = (root / "run.jsonl").read_text(encoding="utf-8")
            self.assertIn('"type": "run_paused"', events)
            self.assertNotIn('"type": "run_finished"', events)

    def test_approval_resumes_the_same_model_context_after_executing_pending_command(self) -> None:
        with TemporaryDirectory() as temp_dir:
            model = ScriptedModel([
                tool_response("run_command", {"command": "echo approved-only"}, "call-1"),
                tool_response("finish", {"summary": "命令已执行。"}, "call-2"),
            ])
            loop = AgentLoop(Path(temp_dir), model, max_steps=2)

            initial = loop.run("运行额外检查")
            report = loop.resume_after_approval(approved=True)

            self.assertEqual(initial["status"], "awaiting_approval")
            self.assertEqual(report["status"], "complete")
            self.assertEqual(len(model.messages_seen), 2)
            continuation = model.messages_seen[1]
            self.assertEqual(continuation[-3]["role"], "assistant")
            self.assertEqual(continuation[-2]["role"], "tool")
            self.assertIn("approved-only", continuation[-2]["content"])
            self.assertEqual(continuation[-1]["role"], "user")

    def test_approval_pause_survives_a_loop_snapshot_and_restores_context(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            initial_model = ScriptedModel([
                tool_response("run_command", {"command": "echo approved-only"}, "call-1"),
            ])
            first_loop = AgentLoop(root, initial_model, max_steps=2)

            initial = first_loop.run("运行额外检查")
            snapshot = first_loop.session_snapshot()
            resumed_model = ScriptedModel([
                tool_response("finish", {"summary": "命令已执行。"}, "call-2"),
            ])
            restored_loop = AgentLoop(root, resumed_model, max_steps=2)
            restored_loop.restore_session(snapshot)
            report = restored_loop.resume_after_approval(approved=True)

            self.assertEqual(initial["status"], "awaiting_approval")
            self.assertEqual(report["status"], "complete")
            continuation = resumed_model.messages_seen[0]
            self.assertEqual(continuation[-3]["role"], "assistant")
            self.assertEqual(continuation[-2]["role"], "tool")
            self.assertIn("approved-only", continuation[-2]["content"])
            self.assertEqual(continuation[-1]["role"], "user")

    def test_finish_with_drafts_pauses_for_review_without_writing_workspace(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = root / "note.txt"
            file_path.write_text("before\n", encoding="utf-8")
            model = ScriptedModel([
                tool_response("write_file", {"path": "note.txt", "content": "after\n"}, "call-1"),
                tool_response("run_command", {"command": "python -c \"assert __import__('pathlib').Path('note.txt').read_text() == 'after\\n'\""}, "call-2"),
                tool_response("finish", {"summary": "草稿已准备。"}, "call-3"),
                tool_response("finish", {"summary": "不应被请求。"}, "call-4"),
            ])

            report = AgentLoop(root, model, max_steps=4).run("更新说明")

            self.assertEqual(report["status"], "review_required")
            self.assertEqual(report["failure"]["type"], "review_required")
            self.assertEqual(file_path.read_text(encoding="utf-8"), "before\n")
            self.assertEqual(len(model.messages_seen), 3)
            self.assertEqual(len(model.responses), 1)

    def test_draft_review_pause_survives_a_loop_snapshot_until_accepted(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = root / "note.txt"
            file_path.write_text("before\n", encoding="utf-8")
            initial_model = ScriptedModel([
                tool_response("write_file", {"path": "note.txt", "content": "after\n"}, "call-1"),
                tool_response("run_command", {"command": "python -c \"assert __import__('pathlib').Path('note.txt').read_text() == 'after\\n'\""}, "call-2"),
                tool_response("finish", {"summary": "草稿已准备。"}, "call-3"),
            ])
            first_loop = AgentLoop(root, initial_model, max_steps=4)

            initial = first_loop.run("更新说明")
            snapshot = first_loop.session_snapshot()
            restored_loop = AgentLoop(root, ScriptedModel([]), max_steps=4)
            restored_loop.restore_session(snapshot)
            accepted = restored_loop.registry.drafts.accept()
            report = restored_loop.resume_after_review(accepted=True)

            self.assertEqual(initial["status"], "review_required")
            self.assertEqual(accepted["accepted"], ["note.txt"])
            self.assertEqual(file_path.read_text(encoding="utf-8"), "after\n")
            self.assertEqual(report["status"], "complete")
