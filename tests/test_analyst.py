from datetime import UTC, datetime, timedelta

from alphatrader.agent.analyst import build_market_context, propose
from alphatrader.models import Action, Candle


def _candles(closes: list[float]) -> list[Candle]:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Candle(
            symbol="AAPL",
            timestamp=base + timedelta(days=i),
            open=c - 0.5,
            high=c + 1.0,
            low=c - 1.0,
            close=c,
            volume=1_000_000,
        )
        for i, c in enumerate(closes)
    ]


class WellFormedProvider:
    def complete(self, system: str, user: str) -> str:
        return (
            '{"symbol": "AAPL", "action": "buy", "entry": 231.40, '
            '"stop_loss": 227.10, "take_profit": 240.10, "confidence": 0.7, '
            '"rationale": "Uptrend continuation; could fail if support breaks."}'
        )


class FencedJsonProvider:
    def complete(self, system: str, user: str) -> str:
        return (
            "```json\n"
            '{"symbol": "AAPL", "action": "hold", "confidence": 0.2, '
            '"rationale": "No clear setup."}\n'
            "```"
        )


class GarbageProvider:
    def complete(self, system: str, user: str) -> str:
        return "Sure! Here's my analysis: AAPL looks bullish, I'd say buy around 231."


class ProviderFailure:
    def complete(self, system: str, user: str) -> str:
        raise RuntimeError("connection reset")


def test_build_market_context_includes_indicators():
    context = build_market_context("AAPL", _candles([float(100 + i) for i in range(60)]))
    assert "RSI(14)" in context
    assert "EMA(20)" in context
    assert "Last close" in context


def test_build_market_context_handles_no_candles():
    context = build_market_context("AAPL", [])
    assert "No candle data" in context


def test_propose_parses_well_formed_json():
    proposal = propose(WellFormedProvider(), "AAPL", _candles([100.0] * 60))
    assert proposal.action == Action.BUY
    assert proposal.entry == 231.40
    assert proposal.stop_loss == 227.10
    assert proposal.take_profit == 240.10
    assert proposal.confidence == 0.7


def test_propose_strips_fenced_json_block():
    proposal = propose(FencedJsonProvider(), "AAPL", _candles([100.0] * 60))
    assert proposal.action == Action.HOLD


def test_propose_garbage_output_degrades_to_hold():
    proposal = propose(GarbageProvider(), "AAPL", _candles([100.0] * 60))
    assert proposal.action == Action.HOLD
    assert proposal.confidence == 0.0
    assert "Malformed LLM output" in proposal.rationale


def test_propose_provider_exception_degrades_to_hold():
    proposal = propose(ProviderFailure(), "AAPL", _candles([100.0] * 60))
    assert proposal.action == Action.HOLD
    assert "LLM provider call failed" in proposal.rationale
