from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class EventLog:
    def __init__(self, path: Path, listener: Callable[[dict[str, Any]], None] | None = None):
        self.path = path
        self.listener = listener
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event_type: str, **payload: Any) -> None:
        event = {
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=True, default=str) + "\n")
        if self.listener:
            try:
                self.listener(event)
            except Exception:
                # Observers such as the browser event stream cannot interrupt an agent run.
                pass
