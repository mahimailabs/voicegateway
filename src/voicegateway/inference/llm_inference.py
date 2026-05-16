"""Drop-in mirror of `livekit.agents.inference.LLM` backed by VoiceGateway."""

from __future__ import annotations

from typing import Any

from livekit.agents.inference.llm import (
    ChatCompletionOptions,
    LLMModels,
)

from voicegateway.core.model_resolution import resolve_model
from voicegateway.inference.base_inference import InferenceFactory


class LLM(InferenceFactory):
    """LiveKit-plugin LLM factory backed by VoiceGateway."""

    _modality = "llm"

    @classmethod
    def _create_plugin(
        cls,
        provider_instance: Any,
        model_name: str,
        plugin_kwargs: dict[str, Any],
    ) -> Any:
        return provider_instance.create_llm(model=model_name, **plugin_kwargs)

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

        plugin_kwargs: dict[str, Any] = {}
        if base_url is not None:
            plugin_kwargs["base_url"] = base_url
        if extra_kwargs is not None:
            plugin_kwargs.update(dict(extra_kwargs))

        return cls._build(
            provider_name=provider_name,
            model_name=model_name,
            plugin_kwargs=plugin_kwargs,
            api_key_override=api_key,
        )
