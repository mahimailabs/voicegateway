"""Tests for voicegateway.core.model_resolution.resolve_model."""

from __future__ import annotations

import pytest

from voicegateway.core.model_resolution import ModelResolutionError, resolve_model


class TestHappyPath:
    def test_canonical_split(self):
        assert resolve_model("deepgram/flux-general") == ("deepgram", "flux-general")

    def test_openai_split(self):
        assert resolve_model("openai/gpt-4o-mini") == ("openai", "gpt-4o-mini")

    def test_internal_colons_preserved_for_ollama_tags(self):
        # Ollama uses ":3b" style tags as part of the model name. resolve_model
        # MUST NOT strip them; that is stt/tts colon-suffix territory only.
        assert resolve_model("ollama/qwen2.5:3b") == ("ollama", "qwen2.5:3b")

    def test_internal_colons_preserved_for_tts_voice(self):
        # cartesia/sonic-3:voice_id selects a voice via colon-suffix. resolve_model
        # passes it through; the TTS factory strips the suffix later.
        assert resolve_model("cartesia/sonic-3:voice_id") == (
            "cartesia",
            "sonic-3:voice_id",
        )

    def test_partition_uses_first_slash_only(self):
        # Path-style model names with extra slashes survive; only the first
        # `/` is consumed.
        assert resolve_model("openai/llama/3.1-8b") == ("openai", "llama/3.1-8b")


class TestUnknownProvider:
    def test_completely_unknown_provider_raises(self):
        with pytest.raises(ModelResolutionError, match="Unknown provider 'made-up-co'"):
            resolve_model("made-up-co/whisper-1")

    def test_typo_in_known_provider_raises(self):
        with pytest.raises(ModelResolutionError, match="Unknown provider 'depgram'"):
            resolve_model("depgram/nova-3")


class TestMalformedInput:
    def test_empty_string_raises(self):
        with pytest.raises(ModelResolutionError, match="non-empty string"):
            resolve_model("")

    def test_no_slash_raises(self):
        with pytest.raises(ModelResolutionError, match="provider/model"):
            resolve_model("nova-3")

    def test_empty_provider_raises(self):
        with pytest.raises(ModelResolutionError, match="provider/model"):
            resolve_model("/nova-3")

    def test_empty_model_raises(self):
        with pytest.raises(ModelResolutionError, match="provider/model"):
            resolve_model("deepgram/")

    def test_non_string_raises(self):
        with pytest.raises(ModelResolutionError, match="non-empty string"):
            resolve_model(None)  # type: ignore[arg-type]
