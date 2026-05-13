"""Tests for voicegateway/core/registry.py — provider name lookup and lazy import."""

from __future__ import annotations

import pytest

from voicegateway.core import registry


def test_list_providers_returns_sorted_known_names() -> None:
    """`list_providers` returns the registered names alphabetically."""
    names = registry.list_providers()
    assert names == sorted(names)
    # Sanity: all 11 v0.1.0 providers are registered.
    assert {
        "openai",
        "deepgram",
        "cartesia",
        "anthropic",
        "groq",
        "elevenlabs",
        "assemblyai",
        "ollama",
        "whisper",
        "kokoro",
        "piper",
    }.issubset(set(names))


def test_create_provider_unknown_name_raises_value_error() -> None:
    """Unknown provider name raises a ValueError listing the available names."""
    with pytest.raises(ValueError, match="Unknown provider"):
        registry.create_provider("does-not-exist", {})


def test_create_provider_unknown_error_lists_alternatives() -> None:
    """The ValueError message names available providers so the user can recover."""
    with pytest.raises(ValueError) as exc_info:
        registry.create_provider("typo", {})
    msg = str(exc_info.value)
    # The error message should include at least one known provider name
    # so the user has a hint about which names are valid.
    assert "openai" in msg or "deepgram" in msg


def test_create_provider_known_name_returns_instance() -> None:
    """Creating an Ollama provider returns a BaseProvider subclass instance."""
    from voicegateway.inference.providers.base import BaseProvider

    provider = registry.create_provider("ollama", {})
    assert isinstance(provider, BaseProvider)
    assert type(provider).__name__ == "OllamaProvider"


def test_create_provider_passes_config_through() -> None:
    """Provider config dict is forwarded to the provider constructor."""
    provider = registry.create_provider(
        "ollama", {"base_url": "http://example.test:11434"}
    )
    # OllamaProvider stores base_url on the instance.
    assert provider.base_url == "http://example.test:11434"
