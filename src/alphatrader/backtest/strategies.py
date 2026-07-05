"""Rule-based proxy strategy used by the backtester in place of the LLM.

Signal: EMA20 crosses EMA50, confirmed by an RSI filter (RSI > 50 for buy,
RSI < 50 for sell). Stop-loss = 1.5x ATR(14), capped at 2% of entry (matching
the compiled-in hard_stop_loss_percent ceiling). Take-profit = 2x the stop
distance. This module never sizes or validates anything — `risk/engine.py`
still does that, exactly as with the live LLM analyst.
"""
from __future__ import annotations

from alphatrader.data.indicators import atr, ema, rsi
from alphatrader.models import Action, Candle, TradeProposal

_ATR_STOP_MULTIPLE = 1.5
_STOP_CAP_PERCENT = 2.0
_TP_MULTIPLE = 2.0
_MIN_CANDLES = 51  # need ema50[-2] and ema50[-1] both defined


def _hold(symbol: str, reason: str) -> TradeProposal:
    return TradeProposal(symbol=symbol, action=Action.HOLD, confidence=0.0, rationale=reason)


def propose(symbol: str, candles: list[Candle]) -> TradeProposal:
    """Deterministic rule-based proposal. Never raises: insufficient data or
    no qualifying cross degrades to a HOLD proposal, mirroring the LLM
    analyst's contract.
    """
    if len(candles) < _MIN_CANDLES:
        return _hold(symbol, "insufficient data")

    closes = [c.close for c in candles]
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    rsi14 = rsi(closes, 14)
    atr14 = atr(candles, 14)

    if None in (ema20[-2], ema20[-1], ema50[-2], ema50[-1], rsi14[-1], atr14[-1]):
        return _hold(symbol, "insufficient data")

    crossed_up = ema20[-2] <= ema50[-2] and ema20[-1] > ema50[-1]
    crossed_down = ema20[-2] >= ema50[-2] and ema20[-1] < ema50[-1]

    entry = closes[-1]
    stop_distance = min(atr14[-1] * _ATR_STOP_MULTIPLE, entry * _STOP_CAP_PERCENT / 100.0)
    if stop_distance <= 0:
        return _hold(symbol, "zero stop distance")

    if crossed_up and rsi14[-1] > 50:
        action = Action.BUY
        stop_loss = entry - stop_distance
        take_profit = entry + stop_distance * _TP_MULTIPLE
    elif crossed_down and rsi14[-1] < 50:
        action = Action.SELL
        stop_loss = entry + stop_distance
        take_profit = entry - stop_distance * _TP_MULTIPLE
    else:
        return _hold(symbol, "no qualifying ema cross")

    return TradeProposal(
        symbol=symbol,
        action=action,
        entry=entry,
        stop_loss=stop_loss,
        take_profit=take_profit,
        confidence=0.6,
        rationale=f"EMA20/50 {action.value} cross, RSI={rsi14[-1]:.1f}",
    )
