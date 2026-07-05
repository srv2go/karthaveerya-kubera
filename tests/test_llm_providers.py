import httpx
import pytest

from alphatrader.llm.anthropic_p import AnthropicProvider
from alphatrader.llm.openai_compat import OpenAICompatProvider


def test_anthropic_provider_extracts_text(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        assert url == "https://api.anthropic.com/v1/messages"
        assert headers["x-api-key"] == "test-key"
        assert json["system"] == "sys"
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "hello world"}]},
            request=request,
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = AnthropicProvider(api_key="test-key", model="claude-test")
    assert provider.complete("sys", "user") == "hello world"


def test_anthropic_provider_raises_on_http_error(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        request = httpx.Request("POST", url)
        return httpx.Response(401, json={"error": "unauthorized"}, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = AnthropicProvider(api_key="bad-key")
    with pytest.raises(httpx.HTTPStatusError):
        provider.complete("sys", "user")


def test_openai_compat_provider_extracts_message(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        assert url == "https://openrouter.ai/api/v1/chat/completions"
        assert headers["Authorization"] == "Bearer test-key"
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "hi there"}}]},
            request=request,
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    provider = OpenAICompatProvider(
        api_key="test-key", base_url="https://openrouter.ai/api/v1", model="some-model"
    )
    assert provider.complete("sys", "user") == "hi there"
