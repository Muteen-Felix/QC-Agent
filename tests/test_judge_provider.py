import pytest

from tools.judge_provider import (
    NoUsableProvider,
    ProviderProbeError,
    select_judge_provider,
)


def test_openai_401_falls_back_to_gemini():
    calls = []

    def probe(provider, _api_key):
        calls.append(provider)
        if provider == "openai":
            raise ProviderProbeError("openai", "HTTP 401")
        return {
            "models": [
                {"name": "models/antigravity-preview", "supportedGenerationMethods": ["countTokens"]},
                {"name": "models/gemini-test-flash", "supportedGenerationMethods": ["generateContent"]}
            ]
        }

    selection = select_judge_provider(
        keys={"openai": "invalid-openai-test-key", "gemini": "test-gemini-key"},
        probe=probe,
    )

    assert calls == ["openai", "gemini"]
    assert selection.provider == "gemini"
    assert selection.model_id == "gemini-test-flash"
    assert selection.fallback_from == "openai"
    assert selection.attempts == ("openai: HTTP 401",)


def test_usable_primary_is_preferred():
    calls = []

    def probe(provider, _api_key):
        calls.append(provider)
        if provider == "openai":
            return {"data": [{"id": "gpt-test"}]}
        raise AssertionError("fallback must not be probed after primary success")

    selection = select_judge_provider(
        keys={"openai": "test-openai-key", "gemini": "test-gemini-key"},
        probe=probe,
    )

    assert calls == ["openai"]
    assert selection.provider == "openai"
    assert selection.model_id == "gpt-test"
    assert selection.fallback_from is None


def test_no_usable_provider_reports_status_without_key():
    secrets = ("invalid-openai-test-key", "invalid-gemini-test-key")

    def probe(provider, _api_key):
        raise ProviderProbeError(provider, "HTTP 401")

    with pytest.raises(NoUsableProvider) as error:
        select_judge_provider(
            keys={"openai": secrets[0], "gemini": secrets[1]},
            probe=probe,
        )

    message = str(error.value)
    assert "openai: HTTP 401" in message
    assert "gemini: HTTP 401" in message
    assert all(secret not in message for secret in secrets)


def test_configured_fallback_model_is_used():
    def probe(provider, _api_key):
        if provider == "openai":
            raise ProviderProbeError("openai", "HTTP 401")
        return {"models": [{"baseModelId": "gemini-chosen", "supportedActions": ["generateContent"]}]}

    selection = select_judge_provider(
        keys={"openai": "invalid-test-key", "gemini": "test-gemini-key"},
        models={"gemini": "gemini-chosen"},
        probe=probe,
    )

    assert selection.provider == "gemini"
    assert selection.model_id == "gemini-chosen"


def test_gemini_default_prefers_highest_stable_flash_model():
    def probe(provider, _api_key):
        if provider == "openai":
            raise ProviderProbeError("openai", "HTTP 401")
        return {
            "models": [
                {"baseModelId": "antigravity-preview-05-2026", "supportedActions": ["generateContent"]},
                {"baseModelId": "gemini-2.5-flash", "supportedActions": ["generateContent"]},
                {"baseModelId": "gemini-3.6-flash", "supportedActions": ["generateContent"]},
                {"baseModelId": "gemini-3.8-flash", "supportedActions": ["generateContent"]},
                {"baseModelId": "gemini-3.8-flash-lite", "supportedActions": ["generateContent"]},
                {"baseModelId": "gemini-3.9-flash-preview", "supportedActions": ["generateContent"]},
            ]
        }

    selection = select_judge_provider(
        keys={"openai": "invalid-test-key", "gemini": "test-gemini-key"},
        probe=probe,
    )

    assert selection.provider == "gemini"
    assert selection.model_id == "gemini-3.8-flash"
