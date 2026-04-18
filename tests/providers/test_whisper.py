"""Tests for the Whisper provider."""

import pytest

from voicegateway.providers.whisper_provider import WhisperProvider


def test_whisper_defaults():
    provider = WhisperProvider({})
    assert provider.model_path == "large-v3"
    assert provider.device == "auto"


def test_whisper_llm_unsupported():
    provider = WhisperProvider({})
    with pytest.raises(NotImplementedError):
        provider.create_llm(model="anything")


def test_whisper_tts_unsupported():
    provider = WhisperProvider({})
    with pytest.raises(NotImplementedError):
        provider.create_tts(model="anything")


def test_whisper_requires_library():
    """When faster-whisper is not installed, raises clear error."""
    provider = WhisperProvider({})
    try:
        import faster_whisper  # noqa: F401
        pytest.skip("faster-whisper is installed")
    except ImportError:
        with pytest.raises(ImportError, match="pip install"):
            provider.create_stt(model="base")


def test_whisper_pricing_zero():
    provider = WhisperProvider({})
    pricing = provider.get_pricing("large-v3", "stt")
    assert pricing["per_minute"] == 0.0
