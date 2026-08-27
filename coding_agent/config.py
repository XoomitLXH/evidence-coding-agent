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
        key = os.environ.get("OPENAI_API_KEY", "")
        url = (base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        chosen_model = model or os.environ.get("MODEL", "gpt-4o-mini")
        steps = max_steps or int(os.environ.get("CODING_AGENT_MAX_STEPS", "24"))
        return cls(api_key=key, base_url=url, model=chosen_model, max_steps=steps)

    @property
    def completions_url(self) -> str:
        return self.base_url if self.base_url.endswith("/chat/completions") else f"{self.base_url}/chat/completions"
