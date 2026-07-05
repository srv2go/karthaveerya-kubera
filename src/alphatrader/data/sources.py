"""DataSource protocol implemented by every market data adapter.

Implementations (alpaca_data.py, ccxt_public.py) are read-only: quotes and
candles only. No adapter in this package is ever capable of placing an
order — that capability does not exist anywhere in this codebase.
"""
from __future__ import annotations

from typing import Protocol

from alphatrader.models import Candle, Quote


class DataSource(Protocol):
    def get_quote(self, symbol: str) -> Quote: ...

    def get_candles(self, symbol: str, limit: int = 60) -> list[Candle]: ...
