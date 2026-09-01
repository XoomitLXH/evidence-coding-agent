from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    model: str
    max_steps: int = 24

    @classmethod
    def from_env(cls, *, model: str | None = None, base_url: str | None = None,
                 max_steps: int | None = None) -> "Config":
        key = os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
        # Keep DeepSeek as the safe default. An OpenAI proxy is only selected
        # when its matching key is present, avoiding accidental proxy leakage.
        env_url = os.environ.get("DEEPSEEK_BASE_URL")
        if not env_url and os.environ.get("OPENAI_API_KEY"):
            env_url = os.environ.get("OPENAI_BASE_URL")
        url = (base_url or env_url or "https://api.deepseek.com").rstrip("/")
        chosen_model = model or os.environ.get("MODEL", "deepseek-chat")
        steps = max_steps or int(os.environ.get("CODING_AGENT_MAX_STEPS", "24"))
        return cls(api_key=key, base_url=url, model=chosen_model, max_steps=steps)

    @property
    def completions_url(self) -> str:
        return self.base_url if self.base_url.endswith("/chat/completions") else f"{self.base_url}/chat/completions"
