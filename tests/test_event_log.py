from __future__ import annotations

import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from coding_agent.event_log import EventLog


class EventLogTests(unittest.TestCase):
    def test_event_includes_an_iso_timestamp(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "run.jsonl"
            EventLog(path).write("run_started", task="demo")

            event = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(event["type"], "run_started")
            self.assertIn("timestamp", event)
            self.assertIsNotNone(datetime.fromisoformat(event["timestamp"]))


if __name__ == "__main__":
    unittest.main()
