"""LLM provider adapter configuration behavior."""

from unittest.mock import patch

from normflow.llm_config import LLMConfig
from normflow.llm_matcher import build_client


def test_build_client_ignores_legacy_openai_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://legacy.example/v1")
    build_client.cache_clear()

    with patch("normflow.llm_matcher.OpenAI") as openai:
        build_client()

    openai.assert_called_once_with(base_url="https://api.openai.com/v1")
    build_client.cache_clear()


def test_build_client_uses_validated_custom_endpoint_configuration():
    config = LLMConfig(
        api_key="test-key",
        base_url="https://llm.example/v1",
        model="provider-model",
    )
    build_client.cache_clear()

    with patch("normflow.llm_matcher.OpenAI") as openai:
        build_client(config)

    openai.assert_called_once_with(
        api_key="test-key",
        base_url="https://llm.example/v1",
    )
    build_client.cache_clear()
