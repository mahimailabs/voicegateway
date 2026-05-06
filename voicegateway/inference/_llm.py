"""Drop-in mirror of `livekit.agents.inference.LLM` backed by VoiceGateway.

Constructing this class returns a wrapped LiveKit-plugin LLM instance
that AgentSession can consume identically to LK Cloud's inference LLM,
but routed through the user's own provider keys with VG cost tracking
and session correlation in the middle.

The constructor signature mirrors `livekit.agents.inference.LLM` from
livekit-agents 1.5.7 exactly. Note that LK's LLM signature is
**different** from STT/TTS: it uses ``str | None`` defaults instead of
``NotGivenOr``, has no ``fallback`` / ``conn_options`` / ``http_session``,
and adds ``provider`` and ``inference_class`` parameters.

Example::

    from voicegateway import inference

    llm = inference.LLM("openai/gpt-4o-mini")
    # equivalent to: from livekit.agents import inference; inference.LLM(...)
"""

from __future__ import annotations

import warnings
from typing import Any

from livekit.agents.inference.llm import (
    ChatCompletionOptions,
    InferenceClass,
    LLMModels,
)

from voicegateway.core.registry import create_provider
from voicegateway.inference._factory import get_gateway
from voicegateway.inference._project import get_active_project
from voicegateway.inference._resolution import resolve_model
from voicegateway.inference._session_context import get_or_create_session_id
from voicegateway.inference._stt import _resolve_provider_config
from voicegateway.middleware.instrumented_provider import wrap_provider


class LLM:
    """LiveKit-plugin LLM factory backed by VoiceGateway.

    Constructing ``LLM(model="openai/gpt-4o-mini", ...)`` resolves the
    provider, looks up its API key from the active project (or uses the
    ``api_key`` override), constructs the corresponding
    ``livekit.plugins.<provider>.LLM`` instance, and wraps it for cost,
    latency, and session-id tracking.

    Unlike STT and TTS, LLM model strings do NOT carry a colon-suffix
    convention in the LK inference module: ``"ollama/qwen2.5:3b"`` keeps
    the ``":3b"`` as part of the model name verbatim.
    """

    def __new__(
        cls,
        model: LLMModels | str,
        *,
        provider: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        api_secret: str | None = None,
        inference_class: InferenceClass | None = None,
        extra_kwargs: ChatCompletionOptions | dict[str, Any] | None = None,
    ) -> Any:
        if not isinstance(model, str) or not model:
            raise ValueError(
                "voicegateway.inference.LLM requires a non-empty model string"
            )

        # Provider precedence: explicit `provider` kwarg wins; otherwise
        # parse the leading "provider/" segment of the model string.
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
            # ChatCompletionOptions is a TypedDict at runtime, behaves
            # like a dict — spread into individual plugin kwargs so e.g.
            # `extra_kwargs={"temperature": 0.2}` becomes LLM(temperature=0.2).
            plugin_kwargs.update(dict(extra_kwargs))

        if api_secret is not None:
            warnings.warn(
                "voicegateway.inference.LLM ignores api_secret; VG uses "
                "provider keys directly. See docs/migration/from-livekit-inference.md.",
                stacklevel=2,
            )
        if inference_class is not None:
            warnings.warn(
                "voicegateway.inference.LLM ignores inference_class; "
                "LK Cloud routing modes do not apply when self-hosting.",
                stacklevel=2,
            )

        gateway = get_gateway()
        active_project = get_active_project()
        provider_config = _resolve_provider_config(
            gateway=gateway,
            provider_name=provider_name,
            api_key_override=api_key,
        )
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
