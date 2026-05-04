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
    """Local Whisper STT prices at $0 via the unified catalog."""
    from decimal import Decimal

    from voicegateway.pricing import catalog

    cost = catalog.calculate_cost("stt", "local/whisper-large-v3", audio_seconds=60)
    assert cost == Decimal("0")
