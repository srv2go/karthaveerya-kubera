from datetime import UTC, datetime

import pytest

from alphatrader.data.indicators import atr, ema, macd, rsi, sma, swing_highs_lows
from alphatrader.models import Candle


def test_sma_basic():
    values = [10, 12, 11, 13, 12, 14]
    result = sma(values, 3)
    assert result[:2] == [None, None]
    assert result[2:] == pytest.approx([11.0, 12.0, 12.0, 13.0])


def test_ema_basic():
    values = [10, 12, 11, 13, 12, 14]
    result = ema(values, 3)
    assert result[:2] == [None, None]
    assert result[2:] == pytest.approx([11.0, 12.0, 12.0, 13.0])


def test_ema_insufficient_data_returns_all_none():
    assert ema([1.0, 2.0], 5) == [None, None]


def test_rsi_wilder_smoothing():
    values = [10, 12, 11, 13, 12, 14]
    result = rsi(values, 3)
    assert result[:3] == [None, None, None]
    assert result[3:] == pytest.approx([80.0, 61.53846153846154, 77.27272727272728])


def _make_candles():
    ohlc = [
        (10, 12, 9, 11),
        (11, 13, 10, 12),
        (12, 14, 11, 13),
        (13, 13.5, 11.5, 12),
        (12, 12.5, 10.5, 11),
        (11, 12, 9.5, 10),
    ]
    base = datetime(2024, 1, 1, tzinfo=UTC)
    return [
        Candle(symbol="TEST", timestamp=base, open=o, high=h, low=low, close=c)
        for o, h, low, c in ohlc
    ]


def test_atr_wilder_smoothing():
    result = atr(_make_candles(), period=3)
    assert result[:3] == [None, None, None]
    assert result[3:] == pytest.approx(
        [2.6666666666666665, 2.444444444444444, 2.462962962962963]
    )


def test_macd_and_signal():
    values = [10, 12, 11, 13, 12, 14]
    macd_line, signal_line, histogram = macd(values, fast=2, slow=3, signal=2)
    assert macd_line[2:] == pytest.approx(
        [0.0, 0.3333333333333339, 0.11111111111111072, 0.37037037037037024]
    )
    assert signal_line[3:] == pytest.approx(
        [0.16666666666666696, 0.12962962962962948, 0.2901234567901233]
    )
    assert histogram[3:] == pytest.approx(
        [0.16666666666666696, -0.018518518518518767, 0.08024691358024694]
    )


def test_swing_highs_lows():
    candles = _make_candles()
    highs, lows = swing_highs_lows(candles, window=1)
    # candle index 2 has the highest high (14) surrounded by lower highs on both sides
    assert 2 in highs
    assert lows == []
