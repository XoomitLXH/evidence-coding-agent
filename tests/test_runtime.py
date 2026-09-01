from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from coding_agent.runtime import execute_python


class RuntimeDebugTests(unittest.TestCase):
    def test_debug_discovers_matching_test_file_and_reports_failure(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "calculator.py").write_text(
                "def add(left, right):\n    return left - right\n",
                encoding="utf-8",
            )
            (root / "test_calculator.py").write_text(
                "import unittest\nfrom calculator import add\n\n"
                "class CalculatorTests(unittest.TestCase):\n"
                "    def test_add(self):\n"
                "        self.assertEqual(add(2, 3), 5)\n",
                encoding="utf-8",
            )

            result = execute_python(root, "calculator.py", mode="debug")

        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.debug_strategy, "tests")
        self.assertEqual(result.test_path, "test_calculator.py")
        self.assertIn("unittest discover", result.command)
        self.assertIn("FAIL", result.output)
        self.assertIn("AssertionError", result.output)

    def test_debug_falls_back_to_faulthandler_without_matching_tests(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "script.py").write_text("raise ValueError('boom')\n", encoding="utf-8")

            result = execute_python(root, "script.py", mode="debug")

        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.debug_strategy, "faulthandler")
        self.assertIsNone(result.test_path)
        self.assertEqual(result.command, "python3 -X faulthandler script.py")
        self.assertIn("ValueError: boom", result.output)


if __name__ == "__main__":
    unittest.main()
