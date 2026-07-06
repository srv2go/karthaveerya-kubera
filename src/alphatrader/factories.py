"""Builds runtime dependencies (market data, LLM provider) from `Settings`.

Shared by `__main__.py` (the live bot process) and `ui/dashboard.py` (the
read-only Streamlit dashboard) so there is exactly one place that wires
credentials into concrete adapters.
"""
from __future__ import annotations

from alphatrader.data.alpaca_data import AlpacaDataSource
from alphatrader.data.ccxt_public import CcxtPublicDataSource
from alphatrader.data.market import MarketDataService, load_symbols_config
from alphatrader.llm.anthropic_p import AnthropicProvider
from alphatrader.llm.openai_compat import OpenAICompatProvider
from alphatrader.llm.provider import LLMProvider
from alphatrader.settings import Settings


def build_market(settings: Settings) -> MarketDataService:
    symbols = load_symbols_config(str(settings.symbols_config_path))
    stock_source = AlpacaDataSource.from_credentials(
        settings.alpaca_key_id, settings.alpaca_secret
    )
    crypto_source = CcxtPublicDataSource.for_exchange()
    return MarketDataService(symbols, stock_source, crypto_source)


def build_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(api_key=settings.llm_api_key, model=settings.llm_model)
    return OpenAICompatProvider(
        api_key=settings.llm_api_key, base_url=settings.llm_base_url, model=settings.llm_model
    )
