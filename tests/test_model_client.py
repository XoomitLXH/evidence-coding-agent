from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from coding_agent.config import Config
from coding_agent.model_client import ModelError, OpenAICompatibleClient


class FakeStreamingResponse:
    def __init__(self, lines: list[bytes]):
        self.lines = lines

    def __enter__(self) -> "FakeStreamingResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def __iter__(self):
        return iter(self.lines)


class ModelClientTests(unittest.TestCase):
    def test_empty_api_key_has_a_chinese_actionable_error(self) -> None:
        client = OpenAICompatibleClient(Config("", "https://api.deepseek.com", "deepseek-chat"))

        with self.assertRaises(ModelError) as raised:
            client.complete([], [])

        self.assertEqual(str(raised.exception), "未配置 API Key，请设置 OPENAI_API_KEY 或 DEEPSEEK_API_KEY")

    def test_stream_yields_content_and_reasoning_deltas(self) -> None:
        chunks = [
            'data: {"choices":[{"delta":{"role":"assistant","reasoning_content":"检查文件"}}]}\n'.encode("utf-8"),
            b'\n',
            'data: {"choices":[{"delta":{"content":"已找到问题"}}]}\n\n'.encode("utf-8"),
            b'data: [DONE]\n\n',
        ]
        client = OpenAICompatibleClient(Config("key", "https://api.deepseek.com", "deepseek-chat"))

        with patch("urllib.request.urlopen", return_value=FakeStreamingResponse(chunks)) as urlopen:
            events = list(client.stream([], []))

        self.assertEqual(
            events,
            [
                {"reasoning": "检查文件", "content": "", "tool_calls": [], "finish_reason": None},
                {"reasoning": "", "content": "已找到问题", "tool_calls": [], "finish_reason": None},
            ],
        )
        payload = json.loads(urlopen.call_args.kwargs["data"].decode("utf-8"))
        self.assertTrue(payload["stream"])

    def test_stream_preserves_fragmented_tool_call_arguments(self) -> None:
        chunks = [
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"read_file","arguments":"{\\"path\\":"}}]}}]}\n\n',
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"note.txt\\"}"}}]}}]}\n\n',
            b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n',
        ]
        client = OpenAICompatibleClient(Config("key", "https://api.deepseek.com", "deepseek-chat"))

        with patch("urllib.request.urlopen", return_value=FakeStreamingResponse(chunks)):
            events = list(client.stream([], []))

        self.assertEqual(events[-1]["finish_reason"], "tool_calls")
        self.assertEqual(events[0]["tool_calls"][0]["function"]["name"], "read_file")
        self.assertEqual(events[1]["tool_calls"][0]["function"]["arguments"], '\"note.txt\"}')


if __name__ == "__main__":
    unittest.main()
