"""Any OpenAI-chat-compatible endpoint provider (OpenRouter, local Ollama, etc.).

Uses the standard `/chat/completions` shape via httpx directly. No SDK
dependency, no API key ever logged.
"""
from __future__ import annotations

import httpx


class OpenAICompatProvider:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        max_tokens: int = 1024,
        temperature: float = 0.2,
        timeout: float = 30.0,
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout = timeout

    def complete(self, system: str, user: str) -> str:
        response = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "content-type": "application/json",
            },
            json={
                "model": self._model,
                "max_tokens": self._max_tokens,
                "temperature": self._temperature,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]
