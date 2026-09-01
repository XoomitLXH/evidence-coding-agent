from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from coding_agent.config import Config


class ConfigTests(unittest.TestCase):
    def test_deepseek_default_ignores_unrelated_openai_proxy_environment(self) -> None:
        with patch.dict(
            os.environ,
            {"OPENAI_BASE_URL": "https://new.sharedchat.cc/codex"},
            clear=False,
        ):
            config = Config.from_env()

        self.assertEqual(config.base_url, "https://api.deepseek.com")

    def test_api_key_is_empty_when_no_supported_environment_variable_exists(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = Config.from_env()

        self.assertEqual(config.api_key, "")

    def test_deepseek_api_key_is_supported_and_openai_key_takes_precedence(self) -> None:
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "deepseek-key"}, clear=True):
            self.assertEqual(Config.from_env().api_key, "deepseek-key")
        with patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "deepseek-key", "OPENAI_API_KEY": "openai-key"},
            clear=True,
        ):
            self.assertEqual(Config.from_env().api_key, "openai-key")

    def test_deepseek_base_url_is_supported_before_openai_base_url(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_BASE_URL": "https://deepseek.example/",
                "OPENAI_BASE_URL": "https://openai.example/",
            },
            clear=True,
        ):
            config = Config.from_env()

        self.assertEqual(config.base_url, "https://deepseek.example")


if __name__ == "__main__":
    unittest.main()
