"""Pure technical indicator functions.

All functions take plain lists of floats (or Candle objects for ATR/swings)
and return lists of the same length, with `None` where the indicator is not
yet defined (insufficient lookback). No I/O, no state — these are the
primary test surface for numerical correctness.
"""
from __future__ import annotations

from alphatrader.models import Candle


def sma(values: list[float], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        if i + 1 < period:
            continue
        window = values[i + 1 - period : i + 1]
        out[i] = sum(window) / period
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    multiplier = 2.0 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        current = (values[i] - prev) * multiplier + prev
        out[i] = current
        prev = current
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period + 1:
        return out
    gains = []
    losses = []
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    out[period] = _rsi_from_averages(avg_gain, avg_loss)

    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        out[i] = _rsi_from_averages(avg_gain, avg_loss)
    return out


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr(candles: list[Candle], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(candles)
    if len(candles) < period + 1:
        return out

    true_ranges: list[float] = []
    for i in range(1, len(candles)):
        c, prev = candles[i], candles[i - 1]
        tr = max(
            c.high - c.low,
            abs(c.high - prev.close),
            abs(c.low - prev.close),
        )
        true_ranges.append(tr)

    avg = sum(true_ranges[:period]) / period
    out[period] = avg
    for i in range(period, len(true_ranges)):
        avg = (avg * (period - 1) + true_ranges[i]) / period
        out[i + 1] = avg
    return out


def macd(
    values: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    macd_line: list[float | None] = [
        (f - s) if f is not None and s is not None else None
        for f, s in zip(ema_fast, ema_slow, strict=True)
    ]
    defined = [v for v in macd_line if v is not None]
    signal_line: list[float | None] = [None] * len(values)
    if len(defined) >= signal:
        start = next(i for i, v in enumerate(macd_line) if v is not None)
        sig_values = ema(defined, signal)
        for offset, v in enumerate(sig_values):
            signal_line[start + offset] = v
    histogram: list[float | None] = [
        (m - s) if m is not None and s is not None else None
        for m, s in zip(macd_line, signal_line, strict=True)
    ]
    return macd_line, signal_line, histogram


def swing_highs_lows(candles: list[Candle], window: int = 2) -> tuple[list[int], list[int]]:
    """Return (swing_high_indices, swing_low_indices).

    A candle at index i is a swing high if its high is strictly greater than
    the highs of `window` candles on each side (and analogously for lows).
    """
    highs_idx: list[int] = []
    lows_idx: list[int] = []
    n = len(candles)
    for i in range(window, n - window):
        left = candles[i - window : i]
        right = candles[i + 1 : i + 1 + window]
        if all(candles[i].high > c.high for c in left) and all(
            candles[i].high > c.high for c in right
        ):
            highs_idx.append(i)
        if all(candles[i].low < c.low for c in left) and all(
            candles[i].low < c.low for c in right
        ):
            lows_idx.append(i)
    return highs_idx, lows_idx
