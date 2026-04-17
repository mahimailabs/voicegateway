"""Tests for the Ollama provider."""

import pytest

from voicegateway.providers.ollama_provider import OllamaProvider


def _has_openai_plugin():
    try:
        __import__("livekit.plugins.openai")
        return True
    except (ImportError, ModuleNotFoundError):
        return False


def test_ollama_defaults():
    provider = OllamaProvider({})
    assert provider.base_url == "http://localhost:11434"


def test_ollama_custom_url():
    provider = OllamaProvider({"base_url": "http://custom:8080"})
    assert provider.base_url == "http://custom:8080"


def test_ollama_stt_unsupported():
    provider = OllamaProvider({})
    with pytest.raises(NotImplementedError):
        provider.create_stt(model="anything")


def test_ollama_tts_unsupported():
    provider = OllamaProvider({})
    with pytest.raises(NotImplementedError):
        provider.create_tts(model="anything")


@pytest.mark.skipif(
    not _has_openai_plugin(),
    reason="openai plugin not installed",
)
def test_ollama_creates_llm():
    provider = OllamaProvider({})
    llm = provider.create_llm(model="qwen2.5:3b")
    assert llm is not None
    assert type(llm).__name__ == "LLM"


def test_ollama_pricing_zero():
    provider = OllamaProvider({})
    pricing = provider.get_pricing("qwen2.5:3b", "llm")
    assert pricing["input_per_1k"] == 0.0
    assert pricing["output_per_1k"] == 0.0
