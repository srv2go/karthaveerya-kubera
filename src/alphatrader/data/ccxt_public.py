"""ccxt-backed public market data adapter (crypto).

Uses only public, unauthenticated ccxt endpoints (fetch_ticker, fetch_ohlcv).
No API keys are read or required, and no ccxt trading/order methods are ever
called from this module.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from alphatrader.models import Candle, Quote


class CcxtRawClient(Protocol):
    def fetch_ticker(self, symbol: str) -> dict: ...

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> list[list[float]]: ...


class CcxtPublicDataSource:
    def __init__(self, client: CcxtRawClient, timeframe: str = "1d"):
        self._client = client
        self._timeframe = timeframe

    @classmethod
    def for_exchange(
        cls, exchange_id: str = "binance", timeframe: str = "1d"
    ) -> CcxtPublicDataSource:
        import ccxt

        exchange_class = getattr(ccxt, exchange_id)
        return cls(exchange_class(), timeframe=timeframe)

    def get_quote(self, symbol: str) -> Quote:
        ticker = self._client.fetch_ticker(symbol)
        ts = ticker.get("timestamp")
        timestamp = datetime.fromtimestamp(ts / 1000, tz=UTC) if ts else datetime.now(UTC)
        return Quote(
            symbol=symbol,
            price=float(ticker["last"]),
            timestamp=timestamp,
            bid=ticker.get("bid"),
            ask=ticker.get("ask"),
        )

    def get_candles(self, symbol: str, limit: int = 60) -> list[Candle]:
        raw = self._client.fetch_ohlcv(symbol, timeframe=self._timeframe, limit=limit)
        candles = []
        for ts_ms, o, h, low, c, v in raw:
            candles.append(
                Candle(
                    symbol=symbol,
                    timestamp=datetime.fromtimestamp(ts_ms / 1000, tz=UTC),
                    open=float(o),
                    high=float(h),
                    low=float(low),
                    close=float(c),
                    volume=float(v),
                )
            )
        return candles
