"""Anthropic Messages API provider.

Uses httpx directly (no anthropic SDK dependency) so the provider surface
stays small and easy to audit. No API key is ever logged.
"""
from __future__ import annotations

import httpx

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"


class AnthropicProvider:
    def __init__(
        self,
        api_key: str,
        model: str = "claude-opus-4-5-20251101",
        max_tokens: int = 1024,
        temperature: float = 0.2,
        timeout: float = 30.0,
    ):
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout = timeout

    def complete(self, system: str, user: str) -> str:
        response = httpx.post(
            _API_URL,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": _API_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": self._model,
                "max_tokens": self._max_tokens,
                "temperature": self._temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        data = response.json()
        parts = data.get("content", [])
        return "".join(part.get("text", "") for part in parts if part.get("type") == "text")
