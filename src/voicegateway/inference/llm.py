"""Drop-in mirror of `livekit.agents.inference.LLM` backed by VoiceGateway."""

from __future__ import annotations

from typing import Any

from livekit.agents.inference.llm import (
    ChatCompletionOptions,
    LLMModels,
)

from voicegateway.core.registry import create_provider
from voicegateway.inference.factory import get_gateway
from voicegateway.inference.project import get_active_project
from voicegateway.inference.resolution import resolve_model
from voicegateway.inference.session.context import get_or_create_session_id
from voicegateway.inference.stt import _assert_key_resolved, _resolve_provider_config
from voicegateway.middleware.instrumented_provider import wrap_provider


class LLM:
    """LiveKit-plugin LLM factory backed by VoiceGateway."""

    def __new__(
        cls,
        model: LLMModels | str,
        *,
        provider: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        extra_kwargs: ChatCompletionOptions | dict[str, Any] | None = None,
    ) -> Any:
        if not isinstance(model, str) or not model:
            raise ValueError(
                "voicegateway.inference.LLM requires a non-empty model string"
            )

        if provider is not None:
            provider_name = provider
            model_name = model.split("/", 1)[1] if "/" in model else model
        else:
            provider_name, model_name = resolve_model(model)

        get_or_create_session_id()

        plugin_kwargs: dict[str, Any] = {}
        if base_url is not None:
            plugin_kwargs["base_url"] = base_url
        if extra_kwargs is not None:
            plugin_kwargs.update(dict(extra_kwargs))

        gateway = get_gateway()
        active_project = get_active_project()
        provider_config = _resolve_provider_config(
            gateway=gateway,
            provider_name=provider_name,
            api_key_override=api_key,
            project=active_project,
        )
        _assert_key_resolved(provider_name, active_project, provider_config)
        provider_instance = create_provider(provider_name, provider_config)

        plugin = provider_instance.create_llm(model=model_name, **plugin_kwargs)

        return wrap_provider(
            instance=plugin,
            modality="llm",
            model_id=f"{provider_name}/{model_name}",
            provider=provider_name,
            project=active_project,
            cost_tracker=gateway._cost_tracker,
            storage=gateway._storage,
        )
