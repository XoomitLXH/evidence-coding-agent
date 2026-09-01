from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

from .config import Config


class ModelError(RuntimeError):
    pass


class OpenAICompatibleClient:
    def __init__(self, config: Config):
        self.config = config

    @staticmethod
    def _iter_sse_payloads(response: Any) -> Iterator[dict[str, Any]]:
        """Yield decoded JSON payloads from an OpenAI-compatible SSE response."""
        for raw_line in response:
            if isinstance(raw_line, bytes):
                line = raw_line.decode("utf-8", errors="replace")
            else:
                line = str(raw_line)
            line = line.strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                data = line[5:].strip()
            else:
                continue
            if data == "[DONE]":
                return
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                yield payload

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.config.api_key:
            raise ModelError("未配置 API Key，请设置 OPENAI_API_KEY 或 DEEPSEEK_API_KEY")
        payload = {"model": self.config.model, "messages": messages, "tools": tools, "temperature": 0}
        request = urllib.request.Request(self.config.completions_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.config.api_key}"},
            method="POST")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise ModelError(f"模型请求失败：{_error_label(exc)}") from exc

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Iterator[dict[str, Any]]:
        if not self.config.api_key:
            raise ModelError("未配置 API Key，请设置 OPENAI_API_KEY 或 DEEPSEEK_API_KEY")
        payload = {
            "model": self.config.model,
            "messages": messages,
            "tools": tools,
            "temperature": 0,
            "stream": True,
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.config.completions_url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, data=body, timeout=120) as response:
                for payload in self._iter_sse_payloads(response):
                    choices = payload.get("choices") or []
                    choice = choices[0] if choices else {}
                    delta = choice.get("delta") or {}
                    tool_calls = delta.get("tool_calls") or []
                    yield {
                        "reasoning": delta.get("reasoning_content") or delta.get("reasoning") or "",
                        "content": delta.get("content") or "",
                        "tool_calls": tool_calls,
                        "finish_reason": choice.get("finish_reason"),
                    }
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise ModelError(f"模型请求失败：{_error_label(exc)}") from exc


def _error_label(exc: Exception) -> str:
    """Return request metadata without reading a response body that may echo secrets."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        reason = str(exc.reason).strip()
        return f"网络错误：{reason}" if reason else "网络错误"
    if isinstance(exc, TimeoutError):
        return "请求超时"
    return "未知网络错误"
