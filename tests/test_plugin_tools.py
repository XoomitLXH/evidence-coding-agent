from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from coding_agent.event_log import EventLog
from coding_agent.plugin_tools import load_plugin_tools, validate_tool_spec
from coding_agent.plugins import PluginManager
from coding_agent.state import AgentState
from coding_agent.tool_registry import ToolRegistry


SPEC = {
    "type": "function",
    "function": {
        "name": "hello_plugin",
        "description": "Return a greeting.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
}


class PluginToolsTests(unittest.TestCase):
    def test_validate_tool_spec_rejects_malformed_schema_and_unicode_names(self) -> None:
        with self.assertRaises(ValueError):
            validate_tool_spec("你好", SPEC)
        with self.assertRaises(ValueError):
            validate_tool_spec("hello_plugin", {"type": "function", "function": {"name": "hello_plugin", "parameters": {"type": "array"}}})

    def test_plugin_adapter_registers_tool_schema_and_handler(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plugin = root / "demo"
            (plugin / ".codex-plugin").mkdir(parents=True)
            (plugin / ".codex-plugin" / "plugin.json").write_text(json.dumps({"name": "demo", "tools": "tools.py"}), encoding="utf-8")
            (plugin / "tools.py").write_text(
                "SPEC = " + repr(SPEC) + "\n"
                "def register(registry):\n"
                "    registry.register_plugin_tool('hello_plugin', SPEC, lambda name: {'greeting': 'hello ' + name})\n",
                encoding="utf-8",
            )
            manager = PluginManager(root, [root], include_defaults=False)
            registry = ToolRegistry(root, AgentState(), EventLog(root / "events.jsonl"), plugin_manager=manager)

            result = registry.call("hello_plugin", {"name": "Ada"})

            self.assertTrue(result["ok"], result)
            self.assertEqual(result["greeting"], "hello Ada")
            self.assertIn("hello_plugin", {item["function"]["name"] for item in registry.tool_specs})

    def test_plugin_tool_name_collision_is_rejected_and_load_failure_is_warning(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plugin = root / "demo"
            (plugin / ".codex-plugin").mkdir(parents=True)
            (plugin / ".codex-plugin" / "plugin.json").write_text(json.dumps({"name": "demo", "tools": "tools.py"}), encoding="utf-8")
            (plugin / "tools.py").write_text(
                "def register(registry):\n"
                "    registry.register_plugin_tool('read_file', {'type': 'function'}, lambda: {})\n",
                encoding="utf-8",
            )
            manager = PluginManager(root, [root], include_defaults=False)
            ToolRegistry(root, AgentState(), EventLog(root / "events.jsonl"), plugin_manager=manager)

            self.assertTrue(any("工具加载失败" in warning for warning in manager.warnings))


if __name__ == "__main__":
    unittest.main()
