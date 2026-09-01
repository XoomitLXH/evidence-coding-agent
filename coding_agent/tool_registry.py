from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .event_log import EventLog
from .draft import DraftChanges
from .policy import PolicyError
from .state import AgentState
from .tools_files import apply_patch, list_dir, read_file, write_file
from .tools_search import search_code
from .tools_shell import run_command


def _failure(failure_type: str, label: str, reason: str, recovery: str) -> dict[str, str]:
    return {
        "type": failure_type,
        "label": label,
        "reason": reason,
        "recovery": recovery,
    }


TOOL_SPECS = [
    {"type": "function", "function": {"name": "list_dir", "description": "List files and directories inside the workspace.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "default": "."}}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a bounded line range from a text file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer", "default": 1}, "end_line": {"type": "integer", "default": 240}}, "required": ["path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "search_code", "description": "Search text or regex across repository files.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "path": {"type": "string", "default": "."}, "max_results": {"type": "integer", "default": 80}}, "required": ["query"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create or replace a text file. Use only when the intended content is known.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "apply_patch", "description": "Replace one exact text block in a file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "run_command", "description": "Run a safe test, build, lint, or read-only repository command.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "timeout_seconds": {"type": "integer", "default": 60}}, "required": ["command"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "finish", "description": "Finish a read-only task when no command has failed. After modifying the workspace, finish only after a successful clean verification command is recorded.", "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"], "additionalProperties": False}}},
]


class ToolRegistry:
    def __init__(self, root: Path, state: AgentState, log: EventLog, drafts: DraftChanges | None = None, *, require_draft_review: bool = False, plugin_manager: Any | None = None):
        self.root, self.state, self.log = root.resolve(), state, log
        self.drafts = drafts or DraftChanges(self.root)
        self.require_draft_review = require_draft_review
        self._plugin_specs: dict[str, dict[str, Any]] = {}
        self._active_plugin_name: str | None = None
        self.handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "list_dir": lambda **kw: list_dir(self.root, drafts=self.drafts, **kw),
            "read_file": lambda **kw: read_file(self.root, drafts=self.drafts, **kw),
            "search_code": lambda **kw: search_code(self.root, **kw),
            "write_file": lambda **kw: write_file(self.root, state, drafts=self.drafts, **kw),
            "apply_patch": lambda **kw: apply_patch(self.root, state, drafts=self.drafts, **kw),
            "run_command": lambda **kw: run_command(self.root, state, drafts=self.drafts, **kw),
            "finish": self._finish,
        }
        if plugin_manager is not None:
            plugin_manager.load_tools(self)

    @property
    def tool_specs(self) -> list[dict[str, Any]]:
        """Core schemas followed by validated plugin schemas."""
        return list(TOOL_SPECS) + list(self._plugin_specs.values())

    def register_plugin_tool(self, name: str, spec: dict[str, Any], handler: Callable[..., dict[str, Any]], plugin_name: str | None = None) -> None:
        from .plugin_tools import validate_tool_spec

        validate_tool_spec(name, spec)
        if name in self.handlers or name in self._plugin_specs:
            raise ValueError(f"工具名称冲突: {name}")
        if not callable(handler):
            raise TypeError("工具 handler 必须可调用")
        self.handlers[name] = handler
        self._plugin_specs[name] = spec

    def _finish(self, summary: str) -> dict:
        if not self.state.can_finish():
            return {
                "ok": False,
                "failure": _failure(
                    "verification_failed",
                    "验证未通过",
                    "验证门禁阻止完成：最近一次命令失败，或最新修订尚未通过干净验证。",
                    "查看终端输出，修复问题后重新运行验证命令。",
                ),
                "state": self.state.prompt_context(),
            }
        draft_paths = self.drafts.paths()
        if draft_paths and self.require_draft_review:
            return {
                "ok": False,
                "failure": {
                    "type": "review_required",
                    "label": "等待审阅草稿",
                    "reason": "代码改动仍是草稿，需由用户审阅并接受后才能写入工作区。",
                    "recovery": "请在草稿审阅面板查看 diff，然后接受或拒绝这些改动。",
                },
                "drafts": draft_paths,
            }
        if draft_paths:
            accepted = self.drafts.accept()
            if accepted.get("conflicts"):
                return {"ok": False, "error": f"草稿存在文件冲突：{', '.join(accepted['conflicts'])}"}
        self.state.phase = "COMPLETE"
        self.state.final_summary = summary
        return {"ok": True, "summary": summary}

    def call(self, name: str, arguments: dict[str, Any]) -> dict:
        if name not in self.handlers:
            result: dict[str, Any] = {"ok": False, "error": f"unknown tool: {name}"}
        else:
            try:
                result = self.handlers[name](**arguments)
            except (PolicyError, OSError, TypeError, ValueError) as exc:
                result = {"ok": False, "error": str(exc)}
        result.setdefault("ok", "error" not in result)
        result.setdefault("evidence", self._evidence(name, arguments, result))
        self.log.write(
            "tool_result",
            name=name,
            arguments=self.display_arguments(arguments),
            result=result,
            state=self.state.prompt_context(),
        )
        return result

    def execute_approved_command(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute one command that the user approved after the agent was paused."""
        try:
            result = run_command(
                self.root,
                self.state,
                drafts=self.drafts,
                approval_granted=True,
                **arguments,
            )
        except (PolicyError, OSError, TypeError, ValueError) as exc:
            result = {"ok": False, "error": str(exc)}
        result.setdefault("ok", "error" not in result)
        result.setdefault("evidence", self._evidence("run_command", arguments, result))
        self.log.write(
            "tool_result",
            name="run_command",
            arguments=self.display_arguments(arguments),
            result=result,
            state=self.state.prompt_context(),
        )
        return result

    @staticmethod
    def display_arguments(arguments: Any) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            return {"invalid_arguments": "工具参数不是 JSON 对象"}
        display: dict[str, Any] = {}
        for key, value in arguments.items():
            if key in {"content", "old_text", "new_text"} and isinstance(value, str):
                display[key] = f"{len(value)} 个字符"
            else:
                display[key] = value
        return display

    @classmethod
    def _evidence(cls, name: str, arguments: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        parameters = []
        for key, value in cls.display_arguments(arguments).items():
            summary = str(value)
            if len(summary) > 240:
                summary = f"{summary[:237]}..."
            parameters.append(f"{key}={summary}")
        output = str(result.get("output") or result.get("error") or result.get("summary") or "无输出。")
        if len(output) > 1200:
            output = f"{output[-1200:]}\n（已省略较早输出）"
        return {
            "tool": name,
            "parameters": ", ".join(parameters),
            "exit_code": result.get("exit_code"),
            "duration_ms": result.get("duration_ms"),
            "output_summary": output,
        }

    @staticmethod
    def encode(result: dict) -> str:
        return json.dumps(result, ensure_ascii=True, default=str)
