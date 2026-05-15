"""Conformance smoke tests for InferenceFactory and its three subclasses.

These tests enforce that every modality class declared to inherit from
``InferenceFactory`` stays in sync with the contract. Add a new row
whenever a new modality joins the family.
"""

from __future__ import annotations

import pytest

from voicegateway.inference.base_inference import InferenceFactory
from voicegateway.inference.llm_inference import LLM
from voicegateway.inference.stt_inference import STT
from voicegateway.inference.tts_inference import TTS


@pytest.mark.parametrize("cls", [STT, LLM, TTS])
def test_modality_classes_inherit_factory(cls: type) -> None:
    assert issubclass(cls, InferenceFactory)


@pytest.mark.parametrize(
    "cls,expected",
    [(STT, "stt"), (LLM, "llm"), (TTS, "tts")],
)
def test_modality_attribute_matches_role(cls: type, expected: str) -> None:
    assert cls._modality == expected


def test_stt_strip_suffix_parses_language() -> None:
    cleaned, suffix = STT._strip_suffix("openai/whisper-1:en")
    assert cleaned == "openai/whisper-1"
    assert suffix == "en"


def test_stt_strip_suffix_no_colon() -> None:
    cleaned, suffix = STT._strip_suffix("openai/whisper-1")
    assert cleaned == "openai/whisper-1"
    assert suffix is None


def test_tts_strip_suffix_parses_voice() -> None:
    cleaned, suffix = TTS._strip_suffix("cartesia/sonic-3:my-voice-id")
    assert cleaned == "cartesia/sonic-3"
    assert suffix == "my-voice-id"


def test_tts_strip_suffix_no_colon() -> None:
    cleaned, suffix = TTS._strip_suffix("cartesia/sonic-3")
    assert cleaned == "cartesia/sonic-3"
    assert suffix is None


def test_llm_strip_suffix_is_identity() -> None:
    # LLM does not override _strip_suffix; the base default is identity.
    cleaned, suffix = LLM._strip_suffix("openai/gpt-4o")
    assert cleaned == "openai/gpt-4o"
    assert suffix is None


def test_factory_create_plugin_is_abstract() -> None:
    # _create_plugin is the only abstract method on the base.
    assert InferenceFactory.__abstractmethods__ == frozenset({"_create_plugin"})


@pytest.mark.parametrize("cls", [STT, LLM, TTS])
def test_subclasses_implement_create_plugin(cls: type) -> None:
    # No subclass should leave _create_plugin abstract.
    assert "_create_plugin" not in getattr(cls, "__abstractmethods__", frozenset())
