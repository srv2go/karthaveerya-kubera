from datetime import UTC, datetime
from pathlib import Path

import pytest

from alphatrader.data.alpaca_data import AlpacaDataSource
from alphatrader.data.ccxt_public import CcxtPublicDataSource
from alphatrader.data.market import MarketDataService, UnknownSymbolError, load_symbols_config

REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeAlpacaClient:
    def latest_quote(self, symbol: str) -> dict:
        return {"price": 231.40, "bid": 231.35, "ask": 231.45, "timestamp": datetime.now(UTC)}

    def bars(self, symbol: str, limit: int) -> list[dict]:
        return [
            {
                "timestamp": datetime.now(UTC),
                "open": 230.0,
                "high": 232.0,
                "low": 229.0,
                "close": 231.4,
                "volume": 1_000_000,
            }
            for _ in range(limit)
        ]


class FakeCcxtClient:
    def fetch_ticker(self, symbol: str) -> dict:
        return {"last": 65_000.0, "bid": 64_990.0, "ask": 65_010.0, "timestamp": 1_700_000_000_000}

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> list[list[float]]:
        return [
            [1_700_000_000_000 + i * 86_400_000, 64_000, 66_000, 63_000, 65_000, 10.5]
            for i in range(limit)
        ]


def test_loads_real_repo_symbols_config():
    symbols = load_symbols_config(str(REPO_ROOT / "config" / "symbols.yaml"))
    assert "AAPL" in symbols
    assert "BTC/USD" in symbols
    assert symbols["BTC/USD"].instrument_class == "crypto"


def test_alpaca_adapter_maps_quote_and_candles():
    source = AlpacaDataSource(FakeAlpacaClient())
    quote = source.get_quote("AAPL")
    assert quote.symbol == "AAPL"
    assert quote.price == 231.40

    candles = source.get_candles("AAPL", limit=5)
    assert len(candles) == 5
    assert candles[0].close == 231.4


def test_ccxt_adapter_maps_quote_and_candles():
    source = CcxtPublicDataSource(FakeCcxtClient())
    quote = source.get_quote("BTC/USD")
    assert quote.price == 65_000.0

    candles = source.get_candles("BTC/USD", limit=3)
    assert len(candles) == 3
    assert candles[0].high == 66_000


def _service() -> MarketDataService:
    symbols = load_symbols_config(str(REPO_ROOT / "config" / "symbols.yaml"))
    return MarketDataService(
        symbols=symbols,
        stock_source=AlpacaDataSource(FakeAlpacaClient()),
        crypto_source=CcxtPublicDataSource(FakeCcxtClient()),
    )


def test_market_data_service_routes_stock_and_crypto():
    service = _service()
    stock_quote = service.get_quote("AAPL")
    crypto_quote = service.get_quote("BTC/USD")
    assert stock_quote.price == 231.40
    assert crypto_quote.price == 65_000.0


def test_market_data_service_unknown_symbol_raises():
    service = _service()
    with pytest.raises(UnknownSymbolError):
        service.get_quote("NOT_A_SYMBOL")


def test_market_data_service_caches_within_ttl():
    calls = {"count": 0}

    class CountingClient(FakeAlpacaClient):
        def latest_quote(self, symbol: str) -> dict:
            calls["count"] += 1
            return super().latest_quote(symbol)

    symbols = load_symbols_config(str(REPO_ROOT / "config" / "symbols.yaml"))
    service = MarketDataService(
        symbols=symbols,
        stock_source=AlpacaDataSource(CountingClient()),
        crypto_source=CcxtPublicDataSource(FakeCcxtClient()),
        cache_ttl_seconds=60.0,
    )
    service.get_quote("AAPL")
    service.get_quote("AAPL")
    assert calls["count"] == 1
