"""Drop-in mirror of `livekit.agents.inference.TTS` backed by VoiceGateway."""

from __future__ import annotations

from typing import Any

import aiohttp
from livekit.agents.inference.tts import (
    TTSEncoding,
    TTSModels,
)
from livekit.agents.types import (
    NOT_GIVEN,
    NotGivenOr,
)
from livekit.agents.utils import is_given

from voicegateway.inference.base_inference import InferenceFactory
from voicegateway.core.model_resolution import resolve_model


class TTS(InferenceFactory):
    """LiveKit-plugin TTS factory backed by VoiceGateway."""

    _modality = "tts"

    @classmethod
    def _strip_suffix(cls, model: str) -> tuple[str, str | None]:
        """Strip a trailing ``:voice`` suffix from a model string."""
        idx = model.rfind(":")
        if idx == -1:
            return model, None
        return model[:idx], model[idx + 1 :]

    @classmethod
    def _create_plugin(
        cls,
        provider_instance: Any,
        model_name: str,
        plugin_kwargs: dict[str, Any],
    ) -> Any:
        # voice= is a typed positional kwarg on provider.create_tts; pop
        # it out of plugin_kwargs so it reaches the dedicated parameter.
        voice = plugin_kwargs.pop("voice", None)
        if voice is not None:
            return provider_instance.create_tts(
                model=model_name, voice=voice, **plugin_kwargs
            )
        return provider_instance.create_tts(model=model_name, **plugin_kwargs)

    def __new__(
        cls,
        model: TTSModels | str,
        *,
        voice: NotGivenOr[str] = NOT_GIVEN,
        language: NotGivenOr[str] = NOT_GIVEN,
        encoding: NotGivenOr[TTSEncoding] = NOT_GIVEN,
        sample_rate: NotGivenOr[int] = NOT_GIVEN,
        base_url: NotGivenOr[str] = NOT_GIVEN,
        api_key: NotGivenOr[str] = NOT_GIVEN,
        http_session: aiohttp.ClientSession | None = None,
        extra_kwargs: NotGivenOr[Any] = NOT_GIVEN,
    ) -> Any:
        if not isinstance(model, str) or not model:
            raise ValueError(
                "voicegateway.inference.TTS requires a model string in "
                "'provider/model[:voice]' format"
            )

        cleaned_model, parsed_voice = cls._strip_suffix(model)

        effective_voice: NotGivenOr[str] = voice
        if parsed_voice is not None and not is_given(effective_voice):
            effective_voice = parsed_voice

        provider_name, model_name = resolve_model(cleaned_model)

        plugin_kwargs: dict[str, Any] = {}
        if is_given(effective_voice):
            plugin_kwargs["voice"] = effective_voice
        if is_given(language):
            plugin_kwargs["language"] = language
        if is_given(encoding):
            plugin_kwargs["encoding"] = encoding
        if is_given(sample_rate):
            plugin_kwargs["sample_rate"] = sample_rate
        if is_given(base_url):
            plugin_kwargs["base_url"] = base_url
        if http_session is not None:
            plugin_kwargs["http_session"] = http_session
        if is_given(extra_kwargs):
            plugin_kwargs.update(dict(extra_kwargs))

        return cls._build(
            provider_name=provider_name,
            model_name=model_name,
            plugin_kwargs=plugin_kwargs,
            api_key_override=api_key if is_given(api_key) else None,
        )
