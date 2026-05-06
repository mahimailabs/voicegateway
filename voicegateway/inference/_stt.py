"""Drop-in mirror of `livekit.agents.inference.STT` backed by VoiceGateway.

Constructing this class returns a wrapped LiveKit-plugin STT instance
that AgentSession can consume identically to LK Cloud's inference STT,
but routed through the user's own provider keys with VG cost tracking
and session correlation in the middle.

The constructor signature mirrors `livekit.agents.inference.STT` from
livekit-agents 1.5.7 exactly, so dropping the import is a one-line
change for the user.

Example::

    from voicegateway import inference

    stt = inference.STT("deepgram/nova-3:en")
    # equivalent to: from livekit.agents import inference; inference.STT(...)
"""

from __future__ import annotations

import warnings
from typing import Any

import aiohttp
from livekit.agents.inference.stt import (
    FallbackModelType,
    STTEncoding,
    STTModels,
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
from voicegateway.middleware.instrumented_provider import wrap_provider


def _strip_language_suffix(model: str) -> tuple[str, str | None]:
    """Strip a trailing ``:language`` suffix from a model string.

    Mirrors `livekit.agents.inference.stt._parse_model_string`: uses
    ``rfind`` so the LAST colon delimits the suffix. Returns
    ``(cleaned_model, None)`` when no colon is present.
    """
    idx = model.rfind(":")
    if idx == -1:
        return model, None
    return model[:idx], model[idx + 1 :]


def _resolve_provider_config(
    gateway: Any,
    provider_name: str,
    api_key_override: str | None,
) -> dict[str, Any]:
    """Build the provider config dict, applying the per-call api_key override.

    When ``api_key_override`` is ``None``, returns the gateway's stored
    config for the provider verbatim. When given, returns a shallow copy
    with the override applied so the existing gateway-cached provider
    instance is not mutated.
    """
    base_config = gateway.config.get_provider_config(provider_name) or {}
    if api_key_override is None:
        return dict(base_config)
    return {**base_config, "api_key": api_key_override}


class STT:
    """LiveKit-plugin STT factory backed by VoiceGateway.

    Constructing ``STT(model="deepgram/nova-3", ...)`` resolves the
    provider, looks up its API key from the active project (or uses the
    ``api_key`` override), constructs the corresponding
    ``livekit.plugins.<provider>.STT`` instance, and wraps it for cost,
    latency, and session-id tracking. The returned object behaves
    identically to a plain LK plugin STT.
    """

    def __new__(
        cls,
        model: NotGivenOr[STTModels | str] = NOT_GIVEN,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        base_url: NotGivenOr[str] = NOT_GIVEN,
        encoding: NotGivenOr[STTEncoding] = NOT_GIVEN,
        sample_rate: NotGivenOr[int] = NOT_GIVEN,
        api_key: NotGivenOr[str] = NOT_GIVEN,
        api_secret: NotGivenOr[str] = NOT_GIVEN,
        http_session: aiohttp.ClientSession | None = None,
        extra_kwargs: NotGivenOr[Any] = NOT_GIVEN,
        fallback: NotGivenOr[list[FallbackModelType] | FallbackModelType] = NOT_GIVEN,
        conn_options: NotGivenOr[APIConnectOptions] = NOT_GIVEN,
    ) -> Any:
        if not is_given(model) or not isinstance(model, str):
            raise ValueError(
                "voicegateway.inference.STT requires a model string in "
                "'provider/model[:language]' format"
            )

        cleaned_model, parsed_language = _strip_language_suffix(model)

        effective_language: NotGivenOr[str] = language
        if parsed_language is not None and not is_given(effective_language):
            effective_language = parsed_language

        provider_name, model_name = resolve_model(cleaned_model)

        get_or_create_session_id()

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
            # The Options classes are TypedDicts at runtime; spread them
            # into individual plugin kwargs so e.g. DeepgramOptions(keyterm=...)
            # ends up as STT(keyterm=...) on the LK plugin call.
            plugin_kwargs.update(dict(extra_kwargs))

        if is_given(api_secret):
            warnings.warn(
                "voicegateway.inference.STT ignores api_secret; VG uses "
                "provider keys directly. See docs/migration/from-livekit-inference.md.",
                stacklevel=2,
            )
        if is_given(fallback):
            warnings.warn(
                "voicegateway.inference.STT does not yet honor `fallback`; "
                "the parameter is accepted for drop-in compat but ignored. "
                "Use voicegw.yaml `fallbacks:` for now.",
                stacklevel=2,
            )
        if is_given(conn_options):
            warnings.warn(
                "voicegateway.inference.STT does not yet honor `conn_options`; "
                "ignored.",
                stacklevel=2,
            )

        gateway = get_gateway()
        project = get_active_project()
        provider_config = _resolve_provider_config(
            gateway=gateway,
            provider_name=provider_name,
            api_key_override=api_key if is_given(api_key) else None,
        )
        provider_instance = create_provider(provider_name, provider_config)

        plugin = provider_instance.create_stt(model=model_name, **plugin_kwargs)

        return wrap_provider(
            instance=plugin,
            modality="stt",
            model_id=f"{provider_name}/{model_name}",
            provider=provider_name,
            project=project,
            cost_tracker=gateway._cost_tracker,
            storage=gateway._storage,
        )
