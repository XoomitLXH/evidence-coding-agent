from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .event_log import EventLog
from .policy import PolicyError
from .state import AgentState
from .tools_files import apply_patch, list_dir, read_file, write_file
from .tools_search import search_code
from .tools_shell import run_command


TOOL_SPECS = [
    {"type": "function", "function": {"name": "list_dir", "description": "List files and directories inside the workspace.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "default": "."}}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a bounded line range from a text file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer", "default": 1}, "end_line": {"type": "integer", "default": 240}}, "required": ["path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "search_code", "description": "Search text or regex across repository files.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "path": {"type": "string", "default": "."}, "max_results": {"type": "integer", "default": 80}}, "required": ["query"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create or replace a text file. Use only when the intended content is known.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "apply_patch", "description": "Replace one exact text block in a file.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "run_command", "description": "Run a safe test, build, lint, or read-only repository command.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "timeout_seconds": {"type": "integer", "default": 60}}, "required": ["command"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "finish", "description": "Finish only after a successful verification command is recorded.", "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"], "additionalProperties": False}}},
]


class ToolRegistry:
    def __init__(self, root: Path, state: AgentState, log: EventLog):
        self.root, self.state, self.log = root, state, log
        self.handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "list_dir": lambda **kw: list_dir(root, **kw),
            "read_file": lambda **kw: read_file(root, **kw),
            "search_code": lambda **kw: search_code(root, **kw),
            "write_file": lambda **kw: write_file(root, state, **kw),
            "apply_patch": lambda **kw: apply_patch(root, state, **kw),
            "run_command": lambda **kw: run_command(root, state, **kw),
            "finish": self._finish,
        }

    def _finish(self, summary: str) -> dict:
        if not self.state.can_finish():
            return {"ok": False, "error": "verification gate blocked completion", "state": self.state.prompt_context()}
        self.state.phase = "COMPLETE"
        self.state.final_summary = summary
        return {"ok": True, "summary": summary}

    def call(self, name: str, arguments: dict[str, Any]) -> dict:
        if name not in self.handlers:
            return {"ok": False, "error": f"unknown tool: {name}"}
        try:
            result = self.handlers[name](**arguments)
        except (PolicyError, OSError, TypeError, ValueError) as exc:
            result = {"ok": False, "error": str(exc)}
        self.log.write("tool_result", name=name, arguments=arguments, result=result, state=self.state.prompt_context())
        return result

    @staticmethod
    def encode(result: dict) -> str:
        return json.dumps(result, ensure_ascii=True, default=str)
