from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from coding_agent import cli


class CliPluginTests(unittest.TestCase):
    def test_list_plugins_accepts_explicit_directory(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plugin = root / "demo"
            (plugin / ".codex-plugin").mkdir(parents=True)
            (plugin / ".codex-plugin" / "plugin.json").write_text(json.dumps({"name": "demo", "version": "1"}), encoding="utf-8")
            with patch.object(sys, "argv", ["coding-agent", "--repo", str(root), "--plugin-dir", str(root), "--list-plugins"]):
                with patch("builtins.print") as printer:
                    self.assertEqual(cli.main(), 0)
            payload = json.loads(printer.call_args.args[0])
            self.assertEqual(payload["plugins"][0]["name"], "demo")

    def test_task_is_required_without_list_plugins(self) -> None:
        with TemporaryDirectory() as directory:
            with patch.object(sys, "argv", ["coding-agent", "--repo", directory]):
                with self.assertRaises(SystemExit) as raised:
                    cli.main()
            self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
