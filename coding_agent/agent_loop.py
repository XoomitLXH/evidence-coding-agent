from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from .event_log import EventLog
from .state import AgentState
from .tool_registry import TOOL_SPECS, ToolRegistry


class Model(Protocol):
    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]: ...


SYSTEM_PROMPT = """You are a local coding agent. Work only inside the provided workspace.
Use tools to inspect before editing. Prefer small apply_patch replacements. After every edit,
run an appropriate test, build, or static-check command. If a command fails, inspect its output,
repair the code, and rerun it. You may call finish only when the latest verification exits 0.
Never claim success without tool evidence. Keep changes focused and explain unresolved issues.
"""


class AgentLoop:
    def __init__(self, root: Path, model: Model, *, log_path: Path | None = None, max_steps: int = 24):
        self.root = root.resolve()
        self.model = model
        self.state = AgentState()
        self.log = EventLog(log_path or self.root / "run.jsonl")
        self.registry = ToolRegistry(self.root, self.state, self.log)
        self.max_steps = max_steps

    def run(self, task: str) -> dict:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Task: {task}\nWorkspace: {self.root}\nCurrent state: {self.state.prompt_context()}"},
        ]
        self.log.write("run_started", task=task, workspace=str(self.root))
        status = "incomplete"
        assistant_text = ""
        for step in range(1, self.max_steps + 1):
            self.state.turn = step
            try:
                response = self.model.complete(messages, TOOL_SPECS)
            except Exception as exc:
                assistant_text = str(exc)
                status = "error"
                self.log.write("model_error", error=assistant_text)
                break
            choice = (response.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            assistant_text = message.get("content") or ""
            self.log.write("model_response", step=step, message=message, state=self.state.prompt_context())
            tool_calls = message.get("tool_calls") or []
            messages.append(message)
            if not tool_calls:
                if self.state.phase == "COMPLETE":
                    status = "complete"
                    break
                messages.append({"role": "user", "content": f"You must use a tool. Verification gate state: {self.state.prompt_context()}"})
                continue
            for call in tool_calls:
                function = call.get("function") or {}
                name = function.get("name", "")
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError:
                    arguments = None
                self.log.write("tool_call", step=step, name=name, arguments=arguments)
                if not isinstance(arguments, dict):
                    result = {"ok": False, "error": "tool arguments must be a JSON object"}
                    self.log.write(
                        "tool_result",
                        name=name,
                        arguments=arguments,
                        result=result,
                        state=self.state.prompt_context(),
                    )
                else:
                    result = self.registry.call(name, arguments)
                messages.append({"role": "tool", "tool_call_id": call.get("id", f"call-{step}"),
                                 "name": name, "content": self.registry.encode(result)})
                if self.state.phase == "COMPLETE":
                    status = "complete"
                    break
            if status == "complete":
                break
            messages.append({"role": "user", "content": f"Continue the task. Current state: {self.state.prompt_context()}"})
        if status != "complete" and self.state.phase == "COMPLETE":
            status = "complete"
        report = self.report(status, assistant_text)
        self.log.write("run_finished", status=status, report=report)
        return report

    def report(self, status: str, assistant_text: str = "") -> dict:
        return {
            "status": status,
            "phase": self.state.phase,
            "summary": self.state.final_summary or assistant_text,
            "modified_files": sorted(self.state.modified_files),
            "verification": self.state.verification,
            "failed_commands": self.state.failed_commands,
            "ledger": self.state.ledger[-10:],
            "evidence_required": self.state.phase != "COMPLETE" and not self.state.can_finish(),
        }
