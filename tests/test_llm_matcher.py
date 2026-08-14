"""LLM provider adapter configuration behavior."""

from unittest.mock import MagicMock, patch

from normflow.llm_config import DEFAULT_LLM_MODEL, LLMConfig
from normflow.llm_matcher import build_client, configured_suggest, suggest


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


def test_configured_suggest_uses_the_validated_configured_model():
    config = LLMConfig(
        api_key="test-key",
        base_url="https://llm.example/v1",
        model="provider-model",
    )
    build_client.cache_clear()

    with patch("normflow.llm_matcher.OpenAI") as openai:
        openai.return_value.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="Normalized"))]
        )
        assert configured_suggest(config)("raw", []) == "Normalized"

    assert (
        openai.return_value.chat.completions.create.call_args.kwargs["model"]
        == "provider-model"
    )
    build_client.cache_clear()


def test_unconfigured_suggest_uses_the_central_default_model(monkeypatch):
    monkeypatch.setenv("NORMFLOW_LLM_MODEL", "unvalidated-model")
    build_client.cache_clear()

    with patch("normflow.llm_matcher.OpenAI") as openai:
        openai.return_value.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="Normalized"))]
        )
        assert suggest("raw", []) == "Normalized"

    assert (
        openai.return_value.chat.completions.create.call_args.kwargs["model"]
        == DEFAULT_LLM_MODEL
    )
    build_client.cache_clear()
