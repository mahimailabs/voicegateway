"""Shared factory machinery for VoiceGateway inference modalities.

Every modality (STT, LLM, TTS) is a drop-in mirror of a LiveKit inference
factory: ``Modality(model, ...)`` returns the wrapped LiveKit plugin
instance, not an instance of ``Modality``. The wrapped instance handles
both streaming and batch use cases per LiveKit's plugin design:

- STT: ``recognize(buffer)`` (batch) and ``stream()`` (real-time audio).
- LLM: ``chat(chat_ctx)`` returns an ``LLMStream`` (token-by-token).
- TTS: ``synthesize(text)`` (batch) and ``stream()`` (incremental text in).

VoiceGateway's instrumentation (see
``voicegateway.middleware.base_middleware.InstrumentationMixin._log_request``)
fires once per request regardless of mode, so the wrapped plugin behaves
identically to a bare LiveKit plugin from the caller's point of view.

Subclasses override:

- ``_modality``: the modality slug, e.g. ``"stt"``.
- ``_strip_suffix(model)``: optional trailing-suffix parser
  (``"openai/whisper-1:en"`` -> language ``"en"`` for STT;
  ``"cartesia/sonic:my-voice"`` -> voice ``"my-voice"`` for TTS).
- ``_create_plugin(provider_instance, model_name, plugin_kwargs)``:
  invokes the provider's ``create_<modality>`` method.

Each subclass's ``__new__`` translates its LiveKit-shaped kwargs into a
plain ``plugin_kwargs: dict[str, Any]`` and delegates to ``cls._build``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from voicegateway.core import registry as _registry
from voicegateway.core.config import ConfigError
from voicegateway.core.gateway_factory import get_gateway
from voicegateway.inference.project import get_active_project
from voicegateway.inference.session.context import get_or_create_session_id
from voicegateway.middleware.instrumented_provider_middleware import wrap_provider

if TYPE_CHECKING:
    pass

_LOCAL_PROVIDERS = frozenset({"ollama", "whisper", "kokoro", "piper"})


class InferenceFactory(ABC):
    """Base for STT / LLM / TTS drop-in factories.

    Concrete subclasses keep their LiveKit-shaped ``__new__`` signature
    and call ``cls._build(...)`` once they have parsed their kwargs into
    a ``plugin_kwargs`` dict. The shared flow (session id, provider
    config, key check, provider instantiation, plugin creation,
    instrumentation wrap) lives in ``_build``.
    """

    _modality: str = ""

    @classmethod
    def _strip_suffix(cls, model: str) -> tuple[str, str | None]:
        """Default: nothing to strip. STT overrides for ``:language``; TTS for ``:voice``."""
        return model, None

    @classmethod
    @abstractmethod
    def _create_plugin(
        cls,
        provider_instance: Any,
        model_name: str,
        plugin_kwargs: dict[str, Any],
    ) -> Any:
        """Call the provider's ``create_<modality>`` method and return its result."""
        ...

    @classmethod
    def _resolve_provider_config(
        cls,
        gateway: Any,
        provider_name: str,
        api_key_override: str | None,
        project: str,
    ) -> dict[str, Any]:
        """Build the provider config dict for an inference factory call."""
        base_config = (
            gateway.config.get_provider_config_for_project(provider_name, project) or {}
        )
        if api_key_override is None:
            return dict(base_config)
        return {**base_config, "api_key": api_key_override}

    @classmethod
    def _assert_key_resolved(
        cls,
        provider_name: str,
        project: str,
        config: dict[str, Any],
    ) -> None:
        """Fail-fast preflight that verifies the API key before stream start."""
        if provider_name in _LOCAL_PROVIDERS:
            return
        if config.get("api_key"):
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

    @classmethod
    def _build(
        cls,
        provider_name: str,
        model_name: str,
        plugin_kwargs: dict[str, Any],
        api_key_override: str | None,
    ) -> Any:
        """Shared factory flow: session id, config, key check, create, wrap."""
        get_or_create_session_id()

        gateway = get_gateway()
        project = get_active_project()
        provider_config = cls._resolve_provider_config(
            gateway=gateway,
            provider_name=provider_name,
            api_key_override=api_key_override,
            project=project,
        )
        cls._assert_key_resolved(provider_name, project, provider_config)
        # Module-attribute access so tests can monkeypatch
        # ``voicegateway.core.registry.create_provider`` without depending
        # on the binding inside this module.
        provider_instance = _registry.create_provider(provider_name, provider_config)

        plugin = cls._create_plugin(provider_instance, model_name, plugin_kwargs)

        return wrap_provider(
            instance=plugin,
            modality=cls._modality,
            model_id=f"{provider_name}/{model_name}",
            provider=provider_name,
            project=project,
            cost_tracker=gateway._cost_tracker,
            storage=gateway._storage,
        )


__all__ = ["InferenceFactory"]
