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

from voicegateway.core.registry import create_provider
from voicegateway.inference.factory import get_gateway
from voicegateway.inference.project import get_active_project
from voicegateway.inference.resolution import resolve_model
from voicegateway.inference.session.context import get_or_create_session_id
from voicegateway.inference.stt import _assert_key_resolved, _resolve_provider_config
from voicegateway.middleware.instrumented_provider import wrap_provider


def _strip_voice_suffix(model: str) -> tuple[str, str | None]:
    """Strip a trailing ``:voice`` suffix from a model string."""
    idx = model.rfind(":")
    if idx == -1:
        return model, None
    return model[:idx], model[idx + 1 :]


class TTS:
    """LiveKit-plugin TTS factory backed by VoiceGateway."""

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

        cleaned_model, parsed_voice = _strip_voice_suffix(model)

        effective_voice: NotGivenOr[str] = voice
        if parsed_voice is not None and not is_given(effective_voice):
            effective_voice = parsed_voice

        provider_name, model_name = resolve_model(cleaned_model)

        get_or_create_session_id()

        plugin_kwargs: dict[str, Any] = {}
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

        gateway = get_gateway()
        active_project = get_active_project()
        provider_config = _resolve_provider_config(
            gateway=gateway,
            provider_name=provider_name,
            api_key_override=api_key if is_given(api_key) else None,
            project=active_project,
        )
        _assert_key_resolved(provider_name, active_project, provider_config)
        provider_instance = create_provider(provider_name, provider_config)

        if is_given(effective_voice):
            plugin = provider_instance.create_tts(
                model=model_name, voice=effective_voice, **plugin_kwargs
            )
        else:
            plugin = provider_instance.create_tts(model=model_name, **plugin_kwargs)

        return wrap_provider(
            instance=plugin,
            modality="tts",
            model_id=f"{provider_name}/{model_name}",
            provider=provider_name,
            project=active_project,
            cost_tracker=gateway._cost_tracker,
            storage=gateway._storage,
        )
