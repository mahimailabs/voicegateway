"""Drop-in mirror of `livekit.agents.inference.TTS` backed by VoiceGateway.

Constructing this class returns a wrapped LiveKit-plugin TTS instance
that AgentSession can consume identically to LK Cloud's inference TTS,
but routed through the user's own provider keys with VG cost tracking
and session correlation in the middle.

The constructor signature mirrors `livekit.agents.inference.TTS` from
livekit-agents 1.5.7 exactly: same shape as STT (NotGivenOr defaults)
plus a leading-required ``model`` and a ``voice`` parameter, with the
trailing colon-suffix interpreted as the voice (not language).

Example::

    from voicegateway import inference

    tts = inference.TTS("cartesia/sonic-3:my-voice-id")
    # equivalent to: from livekit.agents import inference; inference.TTS(...)
"""

from __future__ import annotations

import warnings
from typing import Any

import aiohttp
from livekit.agents.inference.tts import (
    FallbackModelType,
    TTSEncoding,
    TTSModels,
)
from livekit.agents.types import (
    NOT_GIVEN,
    APIConnectOptions,
    NotGivenOr,
)
from livekit.agents.utils import is_given

from voicegateway.core.registry import create_provider
from voicegateway.inference._factory import get_gateway
from voicegateway.inference._project import get_active_project
from voicegateway.inference._resolution import resolve_model
from voicegateway.inference._session_context import get_or_create_session_id
from voicegateway.inference._stt import _resolve_provider_config
from voicegateway.middleware.instrumented_provider import wrap_provider


def _strip_voice_suffix(model: str) -> tuple[str, str | None]:
    """Strip a trailing ``:voice`` suffix from a model string.

    Mirrors `livekit.agents.inference.tts._parse_model_string` exactly:
    uses ``rfind`` so the LAST colon delimits the suffix. Returns
    ``(cleaned_model, None)`` when no colon is present.
    """
    idx = model.rfind(":")
    if idx == -1:
        return model, None
    return model[:idx], model[idx + 1 :]


class TTS:
    """LiveKit-plugin TTS factory backed by VoiceGateway.

    Constructing ``TTS(model="cartesia/sonic-3", voice="...")`` resolves
    the provider, looks up its API key from the active project (or uses
    the ``api_key`` override), constructs the corresponding
    ``livekit.plugins.<provider>.TTS`` instance, and wraps it for cost,
    latency, and session-id tracking.

    The trailing ``:suffix`` of the model string is interpreted as the
    voice (mirroring LK's TTS semantics — distinct from STT, where
    ``:suffix`` is the language).
    """

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
        api_secret: NotGivenOr[str] = NOT_GIVEN,
        http_session: aiohttp.ClientSession | None = None,
        extra_kwargs: NotGivenOr[Any] = NOT_GIVEN,
        fallback: NotGivenOr[list[FallbackModelType] | FallbackModelType] = NOT_GIVEN,
        conn_options: NotGivenOr[APIConnectOptions] = NOT_GIVEN,
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

        if is_given(api_secret):
            warnings.warn(
                "voicegateway.inference.TTS ignores api_secret; VG uses "
                "provider keys directly. See docs/migration/from-livekit-inference.md.",
                stacklevel=2,
            )
        if is_given(fallback):
            warnings.warn(
                "voicegateway.inference.TTS does not yet honor `fallback`; "
                "the parameter is accepted for drop-in compat but ignored. "
                "Use voicegw.yaml `fallbacks:` for now.",
                stacklevel=2,
            )
        if is_given(conn_options):
            warnings.warn(
                "voicegateway.inference.TTS does not yet honor `conn_options`; "
                "ignored.",
                stacklevel=2,
            )

        gateway = get_gateway()
        active_project = get_active_project()
        provider_config = _resolve_provider_config(
            gateway=gateway,
            provider_name=provider_name,
            api_key_override=api_key if is_given(api_key) else None,
        )
        provider_instance = create_provider(provider_name, provider_config)

        # BaseProvider.create_tts uses positional `voice`; only forward
        # when explicitly given (avoid leaking the NOT_GIVEN sentinel).
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
