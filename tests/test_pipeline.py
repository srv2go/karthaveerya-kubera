import json
from datetime import UTC, datetime, timedelta

from alphatrader.agent.pipeline import scan
from alphatrader.data.market import MarketDataService, SymbolInfo
from alphatrader.models import AgentStateName, Candle
from alphatrader.signals.cards import render


def _candles(symbol: str, closes: list[float]) -> list[Candle]:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Candle(
            symbol=symbol,
            timestamp=base + timedelta(days=i),
            open=c - 0.5,
            high=c + 1.0,
            low=c - 1.0,
            close=c,
            volume=1_000_000,
        )
        for i, c in enumerate(closes)
    ]


class FakeSource:
    def __init__(self, candles_by_symbol: dict[str, list[Candle]]):
        self._candles = candles_by_symbol

    def get_quote(self, symbol: str):
        raise NotImplementedError("pipeline only uses candles in these tests")

    def get_candles(self, symbol: str, limit: int = 60):
        return self._candles[symbol]


def _market(
    candles_by_symbol: dict[str, list[Candle]], spreads: dict[str, float]
) -> MarketDataService:
    symbols = {
        sym: SymbolInfo(
            data_symbol=sym, etoro_name=sym, instrument_class="stock",
            typical_spread_bps=spreads[sym],
        )
        for sym in candles_by_symbol
    }
    source = FakeSource(candles_by_symbol)
    return MarketDataService(symbols=symbols, stock_source=source, crypto_source=source)


class ScriptedProvider:
    """Returns a canned JSON response per symbol, based on the user prompt text."""

    def __init__(self, responses: dict[str, str]):
        self._responses = responses

    def complete(self, system: str, user: str) -> str:
        for symbol, response in self._responses.items():
            if f"Symbol: {symbol}" in user:
                return response
        raise AssertionError(f"no scripted response for prompt: {user[:80]!r}")


def _proposal_json(symbol, action, entry, stop_loss, take_profit, confidence, rationale="test"):
    return json.dumps(
        {
            "symbol": symbol,
            "action": action,
            "entry": entry,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "confidence": confidence,
            "rationale": rationale,
        }
    )


def test_scan_yields_ranked_card_from_well_formed_llm(risk_config):
    candles = {"AAPL": _candles("AAPL", [float(90 + i) for i in range(60)])}  # uptrend
    market = _market(candles, {"AAPL": 3.0})
    provider = ScriptedProvider(
        {"AAPL": _proposal_json("AAPL", "buy", 100.0, 99.0, 104.0, 0.8)}
    )

    cards = scan(
        ["AAPL"], market, provider, risk_config,
        balance_gbp=1000.0, open_positions_count=0, agent_state=AgentStateName.ACTIVE,
    )

    assert len(cards) == 1
    card = cards[0]
    assert card.symbol == "AAPL"
    assert card.action == "buy"
    assert card.risk_gbp <= 10.0 + 1e-9
    text = render(card)
    assert "SIGNAL #1" in text
    assert "Signal, not advice — you decide." in text


def test_scan_garbage_llm_output_yields_no_card(risk_config):
    candles = {"AAPL": _candles("AAPL", [100.0] * 60)}
    market = _market(candles, {"AAPL": 3.0})

    class GarbageProvider:
        def complete(self, system: str, user: str) -> str:
            return "not json, just vibes: buy AAPL I guess"

    cards = scan(
        ["AAPL"], market, GarbageProvider(), risk_config,
        balance_gbp=1000.0, open_positions_count=0, agent_state=AgentStateName.ACTIVE,
    )
    assert cards == []


def test_scan_rejects_setup_below_spread_floor(risk_config):
    candles = {"AAPL": _candles("AAPL", [100.0] * 60)}
    # Spread is 50bps (0.50 abs on entry 100); stop distance 0.6 => only 1.2x spread,
    # below the 3x floor.
    market = _market(candles, {"AAPL": 50.0})
    provider = ScriptedProvider(
        {"AAPL": _proposal_json("AAPL", "buy", 100.0, 99.4, 101.8, 0.8)}
    )

    cards = scan(
        ["AAPL"], market, provider, risk_config,
        balance_gbp=1000.0, open_positions_count=0, agent_state=AgentStateName.ACTIVE,
    )
    assert cards == []


def test_scan_ranks_higher_score_first_and_respects_max_cards(risk_config):
    candles = {
        "AAPL": _candles("AAPL", [float(90 + i) for i in range(60)]),  # uptrend
        "MSFT": _candles("MSFT", [float(90 + i) for i in range(60)]),  # uptrend
    }
    market = _market(candles, {"AAPL": 3.0, "MSFT": 3.0})
    provider = ScriptedProvider(
        {
            # Higher confidence and higher R/R => higher score.
            "AAPL": _proposal_json("AAPL", "buy", 100.0, 99.0, 106.0, 0.9),
            "MSFT": _proposal_json("MSFT", "buy", 100.0, 99.0, 102.0, 0.3),
        }
    )

    cards = scan(
        ["MSFT", "AAPL"], market, provider, risk_config,
        balance_gbp=1000.0, open_positions_count=0, agent_state=AgentStateName.ACTIVE,
    )
    assert [c.symbol for c in cards] == ["AAPL", "MSFT"]


def test_scan_halted_agent_state_yields_no_cards(risk_config):
    candles = {"AAPL": _candles("AAPL", [100.0] * 60)}
    market = _market(candles, {"AAPL": 3.0})
    provider = ScriptedProvider(
        {"AAPL": _proposal_json("AAPL", "buy", 100.0, 99.0, 104.0, 0.8)}
    )

    cards = scan(
        ["AAPL"], market, provider, risk_config,
        balance_gbp=1000.0, open_positions_count=0, agent_state=AgentStateName.HALTED_DAILY,
    )
    assert cards == []
