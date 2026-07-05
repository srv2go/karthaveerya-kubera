"""LLM provider protocol.

An LLMProvider only ever returns raw text completions. It never sizes,
validates, or executes anything — `agent/analyst.py` parses its output into
a `TradeProposal`, and `risk/engine.py` is the sole authority on whether that
proposal becomes a signal.
"""
from __future__ import annotations

from typing import Protocol


class LLMProvider(Protocol):
    def complete(self, system: str, user: str) -> str:
        """Return the raw text completion for the given system/user prompts."""
        ...
