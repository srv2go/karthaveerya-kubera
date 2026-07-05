"""Unified market data access: symbols.yaml routing + a small TTL cache.

Routes stock/ETF symbols to a stock DataSource and crypto symbols to a
crypto DataSource, based on `instrument_class` in symbols.yaml.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import yaml

from alphatrader.data.sources import DataSource
from alphatrader.models import Candle, Quote


class UnknownSymbolError(ValueError):
    """Raised when a symbol is not present in symbols.yaml."""


@dataclass(frozen=True)
class SymbolInfo:
    data_symbol: str
    etoro_name: str
    instrument_class: str
    typical_spread_bps: float


def load_symbols_config(path: str) -> dict[str, SymbolInfo]:
    with open(path) as fh:
        raw = yaml.safe_load(fh)
    result: dict[str, SymbolInfo] = {}
    for entry in raw["watchlist"]:
        info = SymbolInfo(
            data_symbol=entry["data_symbol"],
            etoro_name=entry["etoro_name"],
            instrument_class=entry["instrument_class"],
            typical_spread_bps=float(entry["typical_spread_bps"]),
        )
        result[info.data_symbol] = info
    return result


class MarketDataService:
    def __init__(
        self,
        symbols: dict[str, SymbolInfo],
        stock_source: DataSource,
        crypto_source: DataSource,
        cache_ttl_seconds: float = 30.0,
    ):
        self._symbols = symbols
        self._stock_source = stock_source
        self._crypto_source = crypto_source
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[tuple, tuple[float, object]] = {}

    def symbol_info(self, symbol: str) -> SymbolInfo:
        info = self._symbols.get(symbol)
        if info is None:
            raise UnknownSymbolError(f"Unknown symbol: {symbol!r}. Add it to symbols.yaml.")
        return info

    def _source_for(self, info: SymbolInfo) -> DataSource:
        return self._crypto_source if info.instrument_class == "crypto" else self._stock_source

    def _cached(self, key: tuple, fn):
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached is not None and now - cached[0] < self._cache_ttl:
            return cached[1]
        value = fn()
        self._cache[key] = (now, value)
        return value

    def get_quote(self, symbol: str) -> Quote:
        info = self.symbol_info(symbol)
        source = self._source_for(info)
        return self._cached(("quote", symbol), lambda: source.get_quote(symbol))

    def get_candles(self, symbol: str, limit: int = 60) -> list[Candle]:
        info = self.symbol_info(symbol)
        source = self._source_for(info)
        return self._cached(("candles", symbol, limit), lambda: source.get_candles(symbol, limit))
