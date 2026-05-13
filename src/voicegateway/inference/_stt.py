"""Drop-in mirror of `livekit.agents.inference.STT` backed by VoiceGateway."""

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

from voicegateway.core.config import ConfigError
from voicegateway.core.registry import create_provider
from voicegateway.inference._factory import get_gateway
from voicegateway.inference._project import get_active_project
from voicegateway.inference._resolution import resolve_model
from voicegateway.inference._session_context import get_or_create_session_id
from voicegateway.middleware.instrumented_provider import wrap_provider

_LOCAL_PROVIDERS = frozenset({"ollama", "whisper", "kokoro", "piper"})


def _strip_language_suffix(model: str) -> tuple[str, str | None]:
    """Strip a trailing ``:language`` suffix from a model string."""
    idx = model.rfind(":")
    if idx == -1:
        return model, None
    return model[:idx], model[idx + 1 :]


def _resolve_provider_config(
    gateway: Any,
    provider_name: str,
    api_key_override: str | None,
    project: str | None = None,
) -> dict[str, Any]:
    """Build the provider config dict for an inference factory call."""
    base_config = (
        gateway.config.get_provider_config_for_project(provider_name, project) or {}
    )
    if api_key_override is None:
        return dict(base_config)
    return {**base_config, "api_key": api_key_override}


def _assert_key_resolved(
    provider_name: str,
    project: str,
    config: dict[str, Any],
) -> None:
    """Fail-fast preflight per REQ-VG-INFER-003.3."""
    if provider_name in _LOCAL_PROVIDERS:
        return
    api_key = config.get("api_key")
    if api_key:
        return
    raise ConfigError(
        f"No API key configured for provider '{provider_name}' in "
        f"project '{project}'. Add it to voicegw.yaml under "
        f"projects.{project}.providers.{provider_name}.api_key, set "
        f"the matching environment variable referenced by your YAML "
        f"(e.g. ${{{provider_name.upper()}_API_KEY}}), or run "
        f"`vg_add_provider(project='{project}', provider="
        f"'{provider_name}', api_key=...)` via MCP / the dashboard."
    )


class STT:
    """LiveKit-plugin STT factory backed by VoiceGateway."""

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
            project=project,
        )
        _assert_key_resolved(provider_name, project, provider_config)
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
