"""Alpaca market-data adapter (stocks/ETFs) — data only.

This module never imports or calls any Alpaca *trading*/order endpoint.
`AlpacaRawClient` is a minimal protocol kept separate from alpaca-py's own
client so tests can inject a fake without depending on alpaca-py's exact
wire types; `_LiveAlpacaClient` is the thin, lazily-imported adapter used
for real credentials.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from alphatrader.models import Candle, Quote


class AlpacaRawClient(Protocol):
    def latest_quote(self, symbol: str) -> dict: ...

    def bars(self, symbol: str, limit: int) -> list[dict]: ...


class AlpacaDataSource:
    def __init__(self, client: AlpacaRawClient):
        self._client = client

    @classmethod
    def from_credentials(cls, key_id: str, secret: str) -> AlpacaDataSource:
        return cls(_LiveAlpacaClient(key_id, secret))

    def get_quote(self, symbol: str) -> Quote:
        raw = self._client.latest_quote(symbol)
        return Quote(
            symbol=symbol,
            price=float(raw["price"]),
            timestamp=raw.get("timestamp") or datetime.now(UTC),
            bid=raw.get("bid"),
            ask=raw.get("ask"),
        )

    def get_candles(self, symbol: str, limit: int = 60) -> list[Candle]:
        raw_bars = self._client.bars(symbol, limit)
        return [
            Candle(
                symbol=symbol,
                timestamp=bar["timestamp"],
                open=float(bar["open"]),
                high=float(bar["high"]),
                low=float(bar["low"]),
                close=float(bar["close"]),
                volume=float(bar.get("volume", 0.0)),
            )
            for bar in raw_bars
        ]


class _LiveAlpacaClient:
    """Lazily imports alpaca-py so it is only required when real credentials
    are actually used, never during import or tests.
    """

    def __init__(self, key_id: str, secret: str):
        from alpaca.data.historical import StockHistoricalDataClient

        self._client = StockHistoricalDataClient(key_id, secret)

    def latest_quote(self, symbol: str) -> dict:
        from alpaca.data.requests import StockLatestQuoteRequest

        req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        resp = self._client.get_stock_latest_quote(req)
        q = resp[symbol]
        mid = (q.bid_price + q.ask_price) / 2 if q.bid_price and q.ask_price else q.ask_price
        return {"price": mid, "bid": q.bid_price, "ask": q.ask_price, "timestamp": q.timestamp}

    def bars(self, symbol: str, limit: int) -> list[dict]:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day, limit=limit)
        resp = self._client.get_stock_bars(req)
        bars = resp[symbol]
        return [
            {
                "timestamp": b.timestamp,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars
        ]
