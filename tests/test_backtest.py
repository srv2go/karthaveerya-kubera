"""Backtester tests: deterministic fixture data with known, reproducible
results, plus a stress fixture that demonstrably fires a circuit breaker.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from alphatrader.backtest.engine import run_backtest
from alphatrader.backtest.report import generate_report
from alphatrader.models import Action, Candle

_START = datetime(2024, 1, 1, tzinfo=UTC)


def _make_candles(symbol: str, closes: list[float]) -> list[Candle]:
    """Deterministic synthetic OHLCV: each bar's high/low bracket the
    open/close by a fixed 0.2%, one bar per calendar day.
    """
    candles = []
    for i, close in enumerate(closes):
        open_ = closes[i - 1] if i > 0 else close
        high = max(open_, close) * 1.002
        low = min(open_, close) * 0.998
        candles.append(
            Candle(
                symbol=symbol,
                timestamp=_START + timedelta(days=i),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=1000.0,
            )
        )
    return candles


def test_backtest_deterministic_fixture_hits_take_profit(risk_config):
    """55 flat bars (EMA20/50 warmup) then a steady 2%/day rise triggers a
    single EMA20/50 golden-cross BUY, which then rides the rise to its
    take-profit. Same inputs must always produce the same trade and report.
    """
    closes = [100.0] * 55 + [100.0 * (1.02**i) for i in range(1, 11)]
    candles = _make_candles("TST", closes)

    result = run_backtest("TST", candles, risk_config)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.action == Action.BUY
    assert trade.exit_reason == "take_profit"
    assert trade.entry_price == 102.0
    assert trade.pnl_gbp == pytest.approx(20.0, rel=1e-6)
    assert result.halts == []

    report = generate_report(result)
    assert report.total_trades == 1
    assert report.wins == 1
    assert report.losses == 0
    assert report.hit_rate_pct == 100.0
    assert report.avg_r_multiple == pytest.approx(2.0, rel=1e-6)
    assert report.halted is False
    assert report.final_balance_gbp > report.initial_balance_gbp


def test_backtest_stress_fixture_fires_daily_breaker(risk_config):
    """Same warmup + entry, but with a razor-thin daily loss limit: the
    single stopped-out trade (~1% of bankroll, per max_risk_per_trade_percent)
    must exceed the 0.5% daily limit and trip HALTED_DAILY.
    """
    stress_cfg = replace(risk_config, daily_loss_limit_percent=0.5)
    closes = [100.0] * 55 + [102.0, 90.0, 90.0, 90.0]
    candles = _make_candles("TST", closes)

    result = run_backtest("TST", candles, stress_cfg)

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.exit_reason == "stop_loss"
    assert trade.pnl_gbp < 0

    assert result.halts, "expected the daily breaker to fire"
    assert "HALTED_DAILY" in result.halts[0][1]

    report = generate_report(result)
    assert report.halted is True
    assert report.halt_count == 1
    assert report.losses == 1
    assert report.final_balance_gbp < report.initial_balance_gbp


def test_backtest_flat_data_never_trades(risk_config):
    """No EMA cross ever occurs on flat data: zero trades, flat equity curve."""
    closes = [100.0] * 80
    candles = _make_candles("TST", closes)

    result = run_backtest("TST", candles, risk_config)

    assert result.trades == []
    assert result.halts == []
    assert result.final_balance_gbp == result.initial_balance_gbp

    report = generate_report(result)
    assert report.total_trades == 0
    assert report.hit_rate_pct == 0.0
    assert report.max_drawdown_pct == 0.0
