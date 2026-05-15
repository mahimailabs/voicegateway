"""Drop-in mirror of `livekit.agents.inference.STT` backed by VoiceGateway."""

from __future__ import annotations

from typing import Any

import aiohttp
from livekit.agents.inference.stt import (
    STTEncoding,
    STTModels,
)
from livekit.agents.types import (
    NOT_GIVEN,
    NotGivenOr,
)
from livekit.agents.utils import is_given

from voicegateway.inference.base_inference import InferenceFactory
from voicegateway.core.model_resolution import resolve_model


class STT(InferenceFactory):
    """LiveKit-plugin STT factory backed by VoiceGateway."""

    _modality = "stt"

    @classmethod
    def _strip_suffix(cls, model: str) -> tuple[str, str | None]:
        """Strip a trailing ``:language`` suffix from a model string."""
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
        return provider_instance.create_stt(model=model_name, **plugin_kwargs)

    def __new__(
        cls,
        model: NotGivenOr[STTModels | str] = NOT_GIVEN,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        base_url: NotGivenOr[str] = NOT_GIVEN,
        encoding: NotGivenOr[STTEncoding] = NOT_GIVEN,
        sample_rate: NotGivenOr[int] = NOT_GIVEN,
        api_key: NotGivenOr[str] = NOT_GIVEN,
        http_session: aiohttp.ClientSession | None = None,
        extra_kwargs: NotGivenOr[Any] = NOT_GIVEN,
    ) -> Any:
        if not is_given(model) or not isinstance(model, str):
            raise ValueError(
                "voicegateway.inference.STT requires a model string in "
                "'provider/model[:language]' format"
            )

        cleaned_model, parsed_language = cls._strip_suffix(model)

        effective_language: NotGivenOr[str] = language
        if parsed_language is not None and not is_given(effective_language):
            effective_language = parsed_language

        provider_name, model_name = resolve_model(cleaned_model)

        plugin_kwargs: dict[str, Any] = {}
        if is_given(effective_language):
            plugin_kwargs["language"] = effective_language
        if is_given(base_url):
            plugin_kwargs["base_url"] = base_url
        if is_given(encoding):
            plugin_kwargs["encoding"] = encoding
        if is_given(sample_rate):
            plugin_kwargs["sample_rate"] = sample_rate
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
