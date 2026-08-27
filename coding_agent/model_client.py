from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .config import Config


class ModelError(RuntimeError):
    pass


class OpenAICompatibleClient:
    def __init__(self, config: Config):
        self.config = config

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.config.api_key:
            raise ModelError("OPENAI_API_KEY is not set")
        payload = {"model": self.config.model, "messages": messages, "tools": tools, "temperature": 0}
        request = urllib.request.Request(self.config.completions_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.config.api_key}"},
            method="POST")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            detail = getattr(exc, "read", lambda: b"")()
            raise ModelError(f"model request failed: {exc}; {detail[:500]!r}") from exc
