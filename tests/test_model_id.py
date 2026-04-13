"""Tests for model ID parsing."""

import pytest

from voicegateway.core.model_id import ModelId


def test_parse_simple():
    m = ModelId.parse("deepgram/nova-3")
    assert m.provider == "deepgram"
    assert m.model == "nova-3"
    assert m.variant is None


def test_parse_with_variant():
    m = ModelId.parse("local/kokoro:af_heart")
    assert m.provider == "local"
    assert m.model == "kokoro"
    assert m.variant == "af_heart"


def test_parse_ollama_model_with_colon():
    """Ollama models use colons in model names (qwen2.5:3b), not variants."""
    m = ModelId.parse("ollama/qwen2.5:3b")
    assert m.provider == "ollama"
    assert m.model == "qwen2.5:3b"
    assert m.variant is None


def test_parse_invalid_no_slash():
    with pytest.raises(ValueError):
        ModelId.parse("noslash")


def test_parse_empty_provider():
    with pytest.raises(ValueError):
        ModelId.parse("/model")


def test_full_id():
    m = ModelId.parse("local/kokoro:af_heart")
    assert m.full_id == "local/kokoro:af_heart"


def test_config_key_strips_variant():
    m = ModelId.parse("local/kokoro:af_heart")
    assert m.config_key == "local/kokoro"
