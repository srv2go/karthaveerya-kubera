"""Market context builder + LLM analyst.

The analyst only ever produces a `TradeProposal`. It never sizes or
validates anything — that is `risk/engine.py`'s job. Any parse/validation
failure degrades to a HOLD proposal, and the raw LLM output is logged for
later prompt tuning.
"""
from __future__ import annotations

import json

from loguru import logger
from pydantic import ValidationError

from alphatrader.data.indicators import ema, macd, rsi
from alphatrader.llm.prompts import SYSTEM_PROMPT, build_user_prompt
from alphatrader.llm.provider import LLMProvider
from alphatrader.models import Action, Candle, TradeProposal


def _hold(symbol: str, reason: str) -> TradeProposal:
    return TradeProposal(symbol=symbol, action=Action.HOLD, confidence=0.0, rationale=reason)


def build_market_context(symbol: str, candles: list[Candle]) -> str:
    """Render a compact, human-readable technical summary for the LLM prompt."""
    if not candles:
        return f"No candle data available for {symbol}."

    closes = [c.close for c in candles]
    last = candles[-1]

    rsi_values = rsi(closes, 14)
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    _, _, hist = macd(closes)

    def _fmt(value: float | None) -> str:
        return f"{value:.2f}" if value is not None else "n/a"

    lines = [
        f"Last close: {last.close:.2f} (as of {last.timestamp.isoformat()})",
        f"RSI(14): {_fmt(rsi_values[-1])}",
        f"EMA(20): {_fmt(ema20[-1])}",
        f"EMA(50): {_fmt(ema50[-1])}",
        f"MACD histogram: {_fmt(hist[-1])}",
        f"Recent closes (oldest to newest): {[round(v, 2) for v in closes[-10:]]}",
    ]
    return "\n".join(lines)


def _parse_proposal(symbol: str, raw_text: str) -> TradeProposal:
    text = raw_text.strip()
    # Tolerate LLMs that wrap JSON in a fenced code block despite instructions.
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[len("json"):]
        text = text.strip()

    payload = json.loads(text)  # raises json.JSONDecodeError on malformed output
    proposal = TradeProposal(**payload)  # raises ValidationError on schema mismatch
    if proposal.symbol != symbol:
        # Not fatal, but the analyst should propose for the symbol it was asked about.
        proposal = proposal.model_copy(update={"symbol": symbol})
    return proposal


def propose(provider: LLMProvider, symbol: str, candles: list[Candle]) -> TradeProposal:
    """Ask the LLM for a proposal on `symbol`. Never raises: malformed or
    unparseable output degrades to a logged HOLD proposal.
    """
    market_context = build_market_context(symbol, candles)
    user_prompt = build_user_prompt(symbol, market_context)

    try:
        raw_text = provider.complete(SYSTEM_PROMPT, user_prompt)
    except Exception as exc:  # noqa: BLE001 - provider/network failures degrade to HOLD
        logger.warning("LLM provider call failed for {}: {}", symbol, exc)
        return _hold(symbol, f"LLM provider call failed: {exc}")

    try:
        return _parse_proposal(symbol, raw_text)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        logger.warning(
            "Malformed LLM proposal for {}: {} | raw output: {!r}", symbol, exc, raw_text
        )
        return _hold(symbol, f"Malformed LLM output, defaulted to hold: {exc}")
